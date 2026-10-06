"""Ask the live DPLA API what the recorded fixtures cannot. Run by hand.

    DPLA_API_KEY=... uv run python -m tests.live_check

About twenty calls to DPLA, paced by the client itself (one at a time, a
second apart), plus three manifests from institutions' sites and one read of
DPLA's source on GitHub. Each checks that DPLA still answers in the shape the
server reads, or that a behaviour the server works around still holds. It is
not a test module: pytest does not collect it, and CI never runs it, because
the suite must never depend on a third party's service being up, nor on a key.

The key is read from the environment (or ``.env``) and sent only in a
header. It is never printed.

Exit status 0 if every check passed, 1 if any failed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from dpla_catalog_mcp import server
from dpla_catalog_mcp.client import DplaApiError, DplaClient
from dpla_catalog_mcp.config import ConfigError, load_config
from dpla_catalog_mcp.shape import NARA_HUB, SUMMARY_FIELDS

#: DPLA's field definitions on the deployed repository's main branch.
FIELDS_SOURCE = (
    "https://raw.githubusercontent.com/dpla/api/main/"
    "src/main/scala/dpla/api/v2/search/models/DPLAMAPFields.scala"
)

ATLAS_1929 = "4917d2c8281726bc960e78330c17e5fe"
HATHI_1929 = "b35d7d4066dbb76bff2ce7ab93eca613"
GALVESTON_1870 = "8875b2794f3059a6d30cf64cd9a30025"
DARTMOUTH_1793 = "5e4b30238fc58d1689d8009ef143ee90"
INVENTED = "0000000000000000000000000000beef"


async def tool(name: str, **args) -> dict:
    return json.loads((await server.mcp.call_tool(name, args)).content[0].text)


async def raw(params: dict) -> dict:
    return await server.state.client.search(params, refresh=True)


# --------------------------------------------------------------------------- #
# The checks: (what it means, the check). A check returns (passed, detail).
# --------------------------------------------------------------------------- #
async def header_auth_and_the_1929_atlas() -> tuple[bool, str]:
    r = await tool("search_items", title="Standard atlas of Champaign County", limit=10)
    hit = next((i for i in r.get("items", []) if i.get("id") == ATLAS_1929), {})
    ok = hit.get("institution") == "University of Illinois Urbana-Champaign Library" and (
        hit.get("is_shown_at") or ""
    ).startswith("https://digital.library.illinois.edu/")
    return ok, json.dumps(hit or r)[:300]


async def facets_with_page_size_zero_are_docless() -> tuple[bool, str]:
    r = await raw({"q": "atlas", "facets": "provider.name", "facet_size": 3, "page_size": 0})
    ok = r.get("docs") == [] and isinstance(r.get("facets"), dict) and r.get("count", 0) > 0
    return ok, f"docs={len(r.get('docs', []))} facets={type(r.get('facets')).__name__}"


async def no_facets_is_an_empty_list() -> tuple[bool, str]:
    r = await raw({"q": "atlas", "page_size": 1, "fields": "id"})
    return r.get("facets") == [], f"facets={r.get('facets')!r}"


async def page_above_100_is_silently_clamped() -> tuple[bool, str]:
    r = await raw({"q": "atlas", "page": 150, "page_size": 2, "fields": "id"})
    return r.get("start") == 199, f"page=150 gave start={r.get('start')} (page 100 is 199)"


async def the_deepest_page_answers() -> tuple[bool, str]:
    r = await raw({"q": "atlas", "page": 100, "page_size": 100, "fields": "id"})
    return len(r.get("docs", [])) == 100, f"page 100 x 100 gave {len(r.get('docs', []))} docs"


async def text_over_200_characters_is_a_400() -> tuple[bool, str]:
    try:
        await raw({"q": "atlas " + "x" * 195, "page_size": 0})
    except DplaApiError as exc:
        return exc.status == 400 and "200" in exc.detail, exc.detail
    return False, "accepted"


async def an_unescaped_colon_still_matches_nothing() -> tuple[bool, str]:
    """If this starts failing, DPLA changed its parser; the escaping is still harmless."""
    bare = await raw({"q": "atlas: Champaign", "page_size": 0})
    escaped = await tool("search_items", query="atlas: Champaign", limit=1)
    ok = bare.get("count") == 0 and escaped.get("total", 0) > 0
    return ok, f"bare={bare.get('count')} escaped={escaped.get('total')}"


async def a_rights_uri_matches_only_in_quotes() -> tuple[bool, str]:
    r = await tool(
        "search_items_advanced",
        query="atlas Champaign",
        rights="http://rightsstatements.org/vocab/NoC-US/1.0/",
        limit=1,
    )
    return r.get("total", 0) > 0, f"total={r.get('total')} {r.get('error', '')}"


async def exclude_nara_leaves_nara_out() -> tuple[bool, str]:
    r = await tool("facet_items", facet="hub", query="atlas", exclude_nara=True, size=200)
    hubs = [v["value"] for v in r.get("values", [])]
    return bool(hubs) and NARA_HUB not in hubs, f"{len(hubs)} hubs, NARA present={NARA_HUB in hubs}"


async def exact_matching_round_trips_a_facet_value() -> tuple[bool, str]:
    r = await tool(
        "search_items_advanced",
        query="atlas",
        institution="University of Illinois Urbana-Champaign Library",
        exact=True,
        limit=1,
    )
    return r.get("total", 0) > 0, f"total={r.get('total')}"


async def several_ids_in_one_fetch_drop_the_unknown() -> tuple[bool, str]:
    r = await tool("get_item", ids=[ATLAS_1929, HATHI_1929, INVENTED], refresh=True)
    got = [i.get("id") for i in r.get("items", [])]
    ok = got == [ATLAS_1929, HATHI_1929] and r.get("not_found") == [INVENTED]
    return ok, f"items={got} not_found={r.get('not_found')}"


async def a_record_has_citation_parts() -> tuple[bool, str]:
    r = await tool("get_item", ids=[HATHI_1929])
    parts = (r.get("items") or [{}])[0].get("citation_parts", {})
    ids = [i["value"] for i in parts.get("institution_identifiers", [])]
    ok = parts.get("holding_institution") == "University of Illinois" and "(OCoLC)13824847" in ids
    return ok, json.dumps(parts)[:300]


async def a_lone_unknown_id_is_not_found() -> tuple[bool, str]:
    r = await tool("get_item", ids=[INVENTED])
    return r.get("error") == "not_found", json.dumps(r)[:200]


async def a_gone_route_is_a_plain_text_404() -> tuple[bool, str]:
    try:
        await server.state.client.get("/collections", refresh=True)
    except DplaApiError as exc:
        return exc.status == 404 and exc.code == "", f"{exc.status} code={exc.code!r}"
    return False, "answered"


async def the_portal_to_texas_history_manifest_lists_pages() -> tuple[bool, str]:
    r = await tool("get_item_images", id=GALVESTON_1870, count=2, refresh=True)
    pages = r.get("pages", [])
    ok = r.get("iiif_version") == 2 and r.get("total_pages", 0) > 100 and pages[0].get("image_url")
    return bool(ok), f"v{r.get('iiif_version')} {r.get('total_pages')} pages {r.get('reason', '')}"


async def a_dartmouth_manifest_is_iiif_3() -> tuple[bool, str]:
    r = await tool("get_item_images", id=DARTMOUTH_1793, refresh=True)
    ok = r.get("iiif_version") == 3 and r.get("pages")
    return bool(ok), f"v{r.get('iiif_version')} {r.get('total_pages')} pages {r.get('reason', '')}"


async def a_refusing_site_is_reported_not_retried() -> tuple[bool, str]:
    """UIUC answered scripts with 403 on 2026-10-05. If it opens up, pages appear instead."""
    r = await tool("get_item_images", id=ATLAS_1929, refresh=True)
    refused = r.get("pages") == [] and r.get("open_in_browser") and "error" not in r
    return bool(refused or r.get("pages")), r.get("reason") or f"{r.get('total_pages')} pages"


async def the_fields_the_server_uses_still_exist() -> tuple[bool, str]:
    """Diff against DPLAMAPFields.scala on dpla/api main: fail on drift."""
    async with httpx.AsyncClient(timeout=30) as http:
        source = (await http.get(FIELDS_SOURCE)).raise_for_status().text
    fields = {
        m.group(1): {
            "searchable": m.group(2) == "true",
            "facetable": m.group(3) == "true" and m.group(5) != "None",
            "sortable": m.group(4) == "true" and m.group(5) != "None",
            "exact": m.group(5) != "None",
        }
        for m in re.finditer(
            r'name = "([^"]+)",\s*fieldType = \w+,\s*searchable = (true|false),\s*'
            r"facetable = (true|false),\s*sortable = (true|false),\s*"
            r'elasticSearchDefault = "[^"]*",\s*elasticSearchNotAnalyzed = (None|Some)',
            source,
        )
    }
    problems = []
    for word, exact in server.FIELD_PARAMS.values():
        if not fields.get(word, {}).get("searchable"):
            problems.append(f"{word} not searchable")
        if exact and not fields.get(exact, {}).get("exact"):
            problems.append(f"{exact} has no exact form")
    for name in [*server.DATE_PARAMS.values(), "sourceResource.type"]:
        if not fields.get(name, {}).get("searchable"):
            problems.append(f"{name} not searchable")
    for name in server.FACET_FIELDS.values():
        if not fields.get(name, {}).get("facetable"):
            problems.append(f"{name} not facetable")
    for name in server.SORT_FIELDS.values():
        if not fields.get(name, {}).get("sortable"):
            problems.append(f"{name} not sortable")
    for name in SUMMARY_FIELDS:
        if name not in fields:
            problems.append(f"{name} not defined")
    return not problems and len(fields) > 50, "; ".join(problems) or f"{len(fields)} fields read"


CHECKS: list[tuple[str, Callable[[], Awaitable[tuple[bool, str]]]]] = [
    (
        "the key in the Authorization header finds the 1929 Champaign atlas",
        header_auth_and_the_1929_atlas,
    ),
    (
        "facets with page_size=0 return counts and no records",
        facets_with_page_size_zero_are_docless,
    ),
    ("no facets asked for is an empty list, not an object", no_facets_is_an_empty_list),
    ("page=150 is answered as page 100, silently", page_above_100_is_silently_clamped),
    ("page 100 at 100 a page still answers", the_deepest_page_answers),
    ("a query over 200 characters is refused with 400", text_over_200_characters_is_a_400),
    (
        "an unescaped colon matches nothing; the server's escaping finds hits",
        an_unescaped_colon_still_matches_nothing,
    ),
    ("a rights URI, quoted by the server, matches", a_rights_uri_matches_only_in_quotes),
    ("exclude_nara's negation leaves NARA out", exclude_nara_leaves_nara_out),
    ("an institution name from a facet matches exactly", exact_matching_round_trips_a_facet_value),
    ("a multi-id fetch drops an unknown id", several_ids_in_one_fetch_drop_the_unknown),
    ("HathiTrust's record yields the OCLC number for a citation", a_record_has_citation_parts),
    ("a lone unknown id is not_found", a_lone_unknown_id_is_not_found),
    ("the removed collections route is a plain-text 404", a_gone_route_is_a_plain_text_404),
    (
        "the Portal to Texas History's IIIF v2 manifest lists pages",
        the_portal_to_texas_history_manifest_lists_pages,
    ),
    ("Dartmouth's IIIF v3 manifest lists pages", a_dartmouth_manifest_is_iiif_3),
    ("UIUC's refusal is reported with open_in_browser", a_refusing_site_is_reported_not_retried),
    (
        "every DPLA field the server uses is still defined as it expects",
        the_fields_the_server_uses_still_exist,
    ),
]


async def main() -> int:
    # The MCP SDK turns on INFO logging at import; request lines are noise here.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"not configured: {exc}")
        return 1
    with tempfile.TemporaryDirectory() as cache:
        server.state.config = cfg
        server.state.client = DplaClient(
            cfg.api_key, Path(cache), timeout=cfg.timeout, min_interval=1.0
        )
        failed = 0
        for meaning, check in CHECKS:
            try:
                ok, detail = await check()
            except Exception as exc:  # noqa: BLE001 - a check that crashes has failed
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {meaning}")
            if not ok:
                print(f"      got: {detail[:400]}")
        calls = server.state.client.live_calls
        manifests = server.state.client.manifest_calls
        await server.state.client.aclose()
    print(
        f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed ({calls} DPLA calls, {manifests} manifests)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
