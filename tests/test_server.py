"""The tools, called as a client calls them, against recorded DPLA answers."""

from __future__ import annotations

import httpx
import pytest

from dpla_catalog_mcp import server
from dpla_catalog_mcp.config import ConfigError
from dpla_catalog_mcp.shape import SUMMARY_FIELDS

from .conftest import (
    ATLAS_1929,
    DARTMOUTH_1793,
    DARTMOUTH_MANIFEST,
    GALVESTON_1870,
    HATHI_1929,
    ITEMS,
    MISSING,
    TEST_KEY,
    UIUC_MANIFEST,
    UNT_MANIFEST,
    call_tool,
    fixture,
    fixture_text,
    params_of,
    route_fetch,
    route_search,
)

NOT_NARA = 'NOT provider.name:"National Archives and Records Administration"'


# --------------------------------------------------------------------------- #
# search_items
# --------------------------------------------------------------------------- #
async def test_search_items_sends_the_fields_it_shapes_and_nothing_else(served, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    result = await call_tool("search_items", title="  atlas of   Champaign County ", limit=10)
    assert params_of(route.calls.last.request) == {
        "sourceResource.title": "atlas of Champaign County",
        "fields": ",".join(SUMMARY_FIELDS),
        "page_size": "10",
        "page": "1",
    }
    assert "originalRecord" not in params_of(route.calls.last.request)["fields"]
    assert (result["total"], result["returned"], result["reachable_max"]) == (7, 7, 7)
    assert result["next_page"] is None and "paging_note" not in result
    assert {i["id"] for i in result["items"]} >= {ATLAS_1929, HATHI_1929}


async def test_each_simple_filter_reaches_its_dpla_field(served, dpla):
    route = route_search(dpla, fixture("search_galveston_directory.json"))
    await call_tool(
        "search_items",
        query="city directory",
        place="Galveston",
        date_after="1859",
        date_before="1925-06",
        institution="Rosenberg Library",
        hub="The Portal to Texas History",
        type="text",
    )
    sent = params_of(route.calls.last.request)
    assert sent["q"] == "city directory"
    assert sent["sourceResource.spatial"] == "Galveston"
    assert sent["sourceResource.date.after"] == "1859"
    assert sent["sourceResource.date.before"] == "1925-06"
    assert sent["dataProvider.name"] == "Rosenberg Library"
    assert sent["provider.name"] == "The Portal to Texas History"
    assert sent["filter"] == "sourceResource.type:text"
    assert "exact_field_match" not in sent and "op" not in sent


async def test_a_colon_in_the_query_is_escaped(served, dpla):
    """Recorded: unescaped "atlas: Champaign" matched 0; escaped, 23."""
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await call_tool("search_items", query="atlas: Champaign", title="Plat book/atlas")
    sent = params_of(route.calls.last.request)
    assert sent["q"] == r"atlas\: Champaign"
    assert sent["sourceResource.title"] == r"Plat book\/atlas"


@pytest.mark.parametrize(
    "args",
    [{}, {"type": "text"}, {"query": "   "}, {"limit": 5, "page": 2}],
)
async def test_a_search_with_nothing_to_find_is_refused_without_a_call(served, dpla, args):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    assert (await call_tool("search_items", **args))["error"] == "no_criteria"
    assert route.call_count == 0


async def test_page_101_is_refused_because_dpla_would_return_page_100_again(served, dpla):
    """Recorded: page=150 returned the same hits as page=100."""
    route = route_search(dpla, fixture("search_county_atlas_limit2.json"))
    result = await call_tool("search_items", query="county atlas", page=101)
    assert result["error"] == "paging_limit"
    assert "page 100 again" in result["message"]
    assert route.call_count == 0
    assert "error" not in await call_tool("search_items", query="county atlas", page=100)


async def test_limit_is_held_to_100_and_page_to_at_least_1(served, dpla):
    route = route_search(dpla, fixture("search_county_atlas_limit2.json"))
    await call_tool("search_items", query="county atlas", limit=500, page=-4)
    sent = params_of(route.calls.last.request)
    assert (sent["page_size"], sent["page"]) == ("100", "1")


async def test_paging_says_how_far_it_can_reach(served, dpla):
    route_search(dpla, fixture("search_county_atlas_limit2.json"))
    result = await call_tool("search_items", query="county atlas", date_before="1920", limit=2)
    assert result["total"] == 15673
    assert result["reachable_max"] == 200
    assert result["next_page"] == 2
    assert "first 200 of 15,673" in result["paging_note"]


async def test_a_page_past_the_end_says_so(served, dpla):
    route_search(dpla, {"count": 7, "docs": [], "facets": [], "limit": 2, "start": 9})
    result = await call_tool("search_items", title="atlas of Champaign County", limit=2, page=5)
    assert result["returned"] == 0 and result["next_page"] is None
    assert "past the last one (page 4)" in result["paging_note"]


@pytest.mark.parametrize(
    ("args", "named"),
    [
        ({"query": "a"}, "query"),
        ({"query": "x" * 201}, "query"),
        ({"title": "a"}, "title"),
        ({"place": "Z"}, "place"),
    ],
)
async def test_text_outside_dplas_bounds_is_refused_without_a_call(served, dpla, args, named):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    result = await call_tool("search_items", **args)
    assert result["error"] == "invalid_argument" and result["message"].startswith(named)
    assert route.call_count == 0


@pytest.mark.parametrize(
    "bad", ["1900s", "ca. 1900", "19000", "1900-1", "1900/1910", "2026-13-01x"]
)
async def test_a_bad_date_is_refused_without_a_call(served, dpla, bad):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    result = await call_tool("search_items", title="atlas", date_after=bad)
    assert result["error"] == "invalid_argument" and "YYYY" in result["message"]
    assert route.call_count == 0


async def test_an_item_type_outside_the_list_is_refused(served, dpla):
    from mcp.server.mcpserver.exceptions import ToolError

    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    with pytest.raises(ToolError):
        await server.mcp.call_tool("search_items", {"title": "atlas", "type": "photograph"})
    assert route.call_count == 0


async def test_nara_hits_point_at_the_nara_tools(served, dpla):
    route_search(dpla, fixture("search_nara_homestead.json"))
    result = await call_tool(
        "search_items", query="homestead", hub="National Archives and Records Administration"
    )
    assert result["items"][0]["hub"] == "National Archives and Records Administration"
    assert "nara tools" in result["nara_note"]


async def test_no_nara_note_without_nara_hits(served, dpla):
    route_search(dpla, fixture("search_champaign_atlas.json"))
    assert "nara_note" not in await call_tool("search_items", title="atlas of Champaign County")


# --------------------------------------------------------------------------- #
# search_items_advanced
# --------------------------------------------------------------------------- #
async def test_every_advanced_field_reaches_its_dpla_field(served, dpla):
    route = route_search(dpla, fixture("search_south_dakota_plat.json"))
    await call_tool(
        "search_items_advanced",
        county="Clay",
        state="South Dakota",
        city="Vermillion",
        subject="Real property",
        collection="Chilson Collection",
        publisher="Geo. A. Ogle",
        format="Maps",
        creator="Ogle",
        identifier="(OCoLC)13824847",
        about_after="1900",
        about_before="1915",
        rights="http://rightsstatements.org/vocab/NoC-US/1.0/",
        intermediate_provider="Digital Library of South Dakota",
    )
    sent = params_of(route.calls.last.request)
    assert sent["sourceResource.spatial.county"] == "Clay"
    assert sent["sourceResource.spatial.state"] == "South Dakota"
    assert sent["sourceResource.spatial.city"] == "Vermillion"
    assert sent["sourceResource.subject.name"] == "Real property"
    assert sent["sourceResource.collection.title"] == "Chilson Collection"
    assert sent["sourceResource.publisher"] == "Geo. A. Ogle"
    assert sent["sourceResource.format"] == "Maps"
    assert sent["sourceResource.creator"] == "Ogle"
    assert sent["sourceResource.identifier"] == "(OCoLC)13824847"
    assert sent["sourceResource.temporal.after"] == "1900"
    assert sent["sourceResource.temporal.before"] == "1915"
    assert sent["rights"] == '"http://rightsstatements.org/vocab/NoC-US/1.0/"'
    assert sent["intermediateProvider"] == "Digital Library of South Dakota"


async def test_exact_sends_whole_values_unescaped_to_exact_fields(served, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await call_tool(
        "search_items_advanced",
        place="Champaign County (Ill.)",
        institution="University of Illinois Urbana-Champaign Library",
        rights="http://rightsstatements.org/vocab/NoC-US/1.0/",
        exact=True,
    )
    sent = params_of(route.calls.last.request)
    assert sent["exact_field_match"] == "true"
    assert sent["sourceResource.spatial.name"] == "Champaign County (Ill.)"
    assert "sourceResource.spatial" not in sent
    assert sent["dataProvider.name"] == "University of Illinois Urbana-Champaign Library"
    assert sent["rights"] == "http://rightsstatements.org/vocab/NoC-US/1.0/"


@pytest.mark.parametrize("field", ["creator", "identifier"])
async def test_exact_with_a_field_dpla_cannot_match_exactly_is_refused(served, dpla, field):
    """Recorded: creator "Brock & Company" gave 97 hits, and 0 with exact."""
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    result = await call_tool("search_items_advanced", **{field: "Brock & Company"}, exact=True)
    assert result["error"] == "invalid_argument" and field in result["message"]
    assert route.call_count == 0


async def test_match_any_sends_or(served, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await call_tool("search_items_advanced", title="atlas", subject="Real property", match_any=True)
    assert params_of(route.calls.last.request)["op"] == "OR"


@pytest.mark.parametrize(
    "args",
    [
        {"title": "atlas", "date_after": "1900", "match_any": True},
        {"title": "atlas", "match_any": True, "exclude_nara": True},
    ],
)
async def test_match_any_with_dates_or_exclude_nara_is_refused(served, dpla, args):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    assert (await call_tool("search_items_advanced", **args))["error"] == "invalid_argument"
    assert route.call_count == 0


async def test_sort_by_date_sends_the_field_and_order(served, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await call_tool("search_items_advanced", title="atlas", sort_by="date", sort_order="desc")
    sent = params_of(route.calls.last.request)
    assert (sent["sort_by"], sent["sort_order"]) == ("sourceResource.date.begin", "desc")


async def test_relevance_sends_no_sort(served, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await call_tool("search_items_advanced", title="atlas", sort_order="desc")
    sent = params_of(route.calls.last.request)
    assert "sort_by" not in sent and "sort_order" not in sent


async def test_exclude_nara_adds_a_negation_to_the_query(served, dpla):
    """Recorded: q=atlas 145,017; NARA alone 3,718; with the negation 141,299."""
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    result = await call_tool("search_items_advanced", query="atlas", exclude_nara=True)
    assert params_of(route.calls.last.request)["q"] == f"(atlas) {NOT_NARA}"
    assert "left out" in result["nara_note"]
    await call_tool("search_items_advanced", title="atlas", exclude_nara=True)
    assert params_of(route.calls.last.request)["q"] == NOT_NARA


async def test_a_query_too_long_to_carry_exclude_nara_is_refused(served, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    result = await call_tool("search_items_advanced", query="x" * 150, exclude_nara=True)
    assert result["error"] == "invalid_argument" and "exclude_nara adds 67" in result["message"]
    assert route.call_count == 0


async def test_with_no_words_and_no_sort_the_order_is_flagged(served, dpla):
    route_search(dpla, fixture("search_champaign_atlas.json"))
    result = await call_tool("search_items_advanced", date_after="1900", date_before="1901")
    assert "storage order" in result["order_note"]
    sorted_ = await call_tool(
        "search_items_advanced", date_after="1900", date_before="1901", sort_by="title"
    )
    assert "order_note" not in sorted_


# --------------------------------------------------------------------------- #
# facet_items
# --------------------------------------------------------------------------- #
async def test_facet_items_asks_for_counts_only(served, dpla):
    route = route_search(dpla, fixture("facet_institution_plat_book.json"))
    result = await call_tool("facet_items", facet="institution", title="plat book", size=10)
    assert params_of(route.calls.last.request) == {
        "sourceResource.title": "plat book",
        "facets": "dataProvider.name",
        "facet_size": "10",
        "page_size": "0",
    }
    assert result["total"] == 1999
    assert result["values"][0] == {"value": "Minnesota Historical Society", "count": 417}
    assert "raise size" in result["note"]


async def test_year_facets_list_every_year_oldest_first(served, dpla):
    route = route_search(dpla, fixture("facet_year_galveston_directory.json"))
    result = await call_tool("facet_items", facet="year", title="Galveston city directory")
    assert params_of(route.calls.last.request)["facets"] == "sourceResource.date.begin.year"
    years = [v["value"] for v in result["values"]]
    assert years == sorted(years) and "note" not in result


async def test_a_facet_over_everything_needs_no_filter(served, dpla):
    route = route_search(dpla, fixture("facet_institution_plat_book.json"))
    result = await call_tool("facet_items", facet="hub")
    assert "error" not in result
    assert params_of(route.calls.last.request)["facets"] == "provider.name"


async def test_geocoded_facets_carry_a_coverage_warning(served, dpla):
    route_search(dpla, {"count": 10, "docs": [], "facets": {}})
    result = await call_tool("facet_items", facet="county", query="atlas")
    assert "Facet on place" in result["coverage_note"]


@pytest.mark.parametrize(
    ("facet", "field"),
    [
        ("place", "sourceResource.spatial.name"),
        ("subject", "sourceResource.subject.name"),
        ("collection", "sourceResource.collection.title"),
        ("rights", "rights"),
        ("type", "sourceResource.type"),
    ],
)
async def test_each_facet_name_reaches_its_field(served, dpla, facet, field):
    route = route_search(dpla, {"count": 0, "docs": [], "facets": []})
    await call_tool("facet_items", facet=facet, query="atlas")
    assert params_of(route.calls.last.request)["facets"] == field


# --------------------------------------------------------------------------- #
# get_item
# --------------------------------------------------------------------------- #
async def test_get_item_reads_a_record_with_its_citation_parts(served, dpla):
    route = route_fetch(dpla)
    result = await call_tool("get_item", ids=[f"https://dp.la/item/{ATLAS_1929}"])
    assert route.calls.last.request.url.path == f"/v2/items/{ATLAS_1929}"
    assert params_of(route.calls.last.request) == {}
    [item] = result["items"]
    parts = item["citation_parts"]
    assert parts["holding_institution"] == "University of Illinois Urbana-Champaign Library"
    assert parts["institution_identifiers"][0]["value"] == "99247290812205899"
    assert "original_record" not in item


async def test_get_item_reads_several_and_names_the_missing(served, dpla):
    route = route_fetch(dpla)
    result = await call_tool("get_item", ids=[ATLAS_1929, MISSING, HATHI_1929])
    assert [i["id"] for i in result["items"]] == [ATLAS_1929, HATHI_1929]
    assert result["not_found"] == [MISSING]
    assert route.call_count == 1


async def test_get_item_with_the_original_record(served, dpla):
    route_fetch(dpla)
    result = await call_tool("get_item", ids=[HATHI_1929], include_original_record=True)
    assert result["items"][0]["original_record"].startswith("<record>")


async def test_get_item_not_found(served, dpla):
    route_fetch(dpla)
    result = await call_tool("get_item", ids=[MISSING])
    assert result["error"] == "not_found"
    assert "re-platforms" in result["message"]


@pytest.mark.parametrize("bad", [["../../v2/collections"], ["x" * 40], [""], ["a b"]])
async def test_get_item_refuses_what_is_not_an_id(served, dpla, bad):
    route = route_fetch(dpla)
    assert (await call_tool("get_item", ids=bad))["error"] == "invalid_id"
    assert route.call_count == 0


async def test_get_item_refuses_too_many_or_none(served, dpla):
    route = route_fetch(dpla)
    assert (await call_tool("get_item", ids=[]))["error"] == "invalid_argument"
    many = [f"{n:032x}" for n in range(21)]
    assert (await call_tool("get_item", ids=many))["error"] == "invalid_argument"
    assert route.call_count == 0


async def test_get_item_refresh_asks_again(served, dpla):
    route = route_fetch(dpla)
    await call_tool("get_item", ids=[ATLAS_1929])
    await call_tool("get_item", ids=[ATLAS_1929])
    await call_tool("get_item", ids=[ATLAS_1929], refresh=True)
    assert route.call_count == 2


# --------------------------------------------------------------------------- #
# get_item_images
# --------------------------------------------------------------------------- #
async def test_images_from_a_v2_manifest_page_by_page(served, dpla):
    route_fetch(dpla)
    manifest = dpla.get(UNT_MANIFEST).mock(
        return_value=httpx.Response(200, json=fixture("manifest_unt_galveston_1870_v2.json"))
    )
    result = await call_tool("get_item_images", id=GALVESTON_1870, first=3, count=4)
    assert "authorization" not in manifest.calls.last.request.headers
    assert result["iiif_version"] == 2
    assert result["institution"] == "Rosenberg Library"
    assert result["total_pages"] == 8
    assert [p["n"] for p in result["pages"]] == [3, 4, 5, 6]
    assert result["next_first"] == 7
    assert result["manifest_license"] == "https://texashistory.unt.edu/terms-of-use/"
    last = await call_tool("get_item_images", id=GALVESTON_1870, first=7, count=4)
    assert [p["n"] for p in last["pages"]] == [7, 8] and last["next_first"] is None
    beyond = await call_tool("get_item_images", id=GALVESTON_1870, first=20)
    assert beyond["pages"] == [] and "past the last image (8)" in beyond["note"]


async def test_images_from_a_v3_manifest(served, dpla):
    route_fetch(dpla)
    dpla.get(DARTMOUTH_MANIFEST).mock(
        return_value=httpx.Response(200, json=fixture("manifest_dartmouth_northumberland_v3.json"))
    )
    result = await call_tool("get_item_images", id=DARTMOUTH_1793)
    assert result["iiif_version"] == 3 and result["total_pages"] == 1
    assert result["rights"] == "http://rightsstatements.org/vocab/NoC-US/1.0/"


async def test_a_refused_manifest_sends_the_reader_to_a_browser(served, dpla):
    """Recorded: the University of Illinois answers scripts with 403."""
    route_fetch(dpla)
    refused = dpla.get(UIUC_MANIFEST).mock(
        return_value=httpx.Response(
            403, text=fixture_text("manifest_uiuc_403.html"), headers={"content-type": "text/html"}
        )
    )
    result = await call_tool("get_item_images", id=ATLAS_1929)
    assert result["pages"] == []
    assert result["open_in_browser"] == result["is_shown_at"]
    assert result["open_in_browser"].startswith("https://digital.library.illinois.edu/items/")
    assert "403" in result["reason"] and "does not retry" in result["reason"]
    assert refused.call_count == 1


async def test_a_record_with_no_manifest_says_so(served, dpla):
    route_fetch(dpla)
    result = await call_tool("get_item_images", id=HATHI_1929)
    assert result["pages"] == []
    assert result["open_in_browser"] == "http://catalog.hathitrust.org/Record/103030935"
    assert "no IIIF manifest" in result["reason"]


async def test_get_item_images_not_found(served, dpla):
    route_fetch(dpla)
    assert (await call_tool("get_item_images", id=MISSING))["error"] == "not_found"


# --------------------------------------------------------------------------- #
# Errors as envelopes
# --------------------------------------------------------------------------- #
async def test_a_refused_key_is_reported_as_such(served, dpla):
    route_search(dpla, httpx.Response(403, json=fixture("error_403_invalid_api_key.json")))
    result = await call_tool("search_items", query="atlas")
    assert result["error"] == "invalid_api_key"
    assert "says nothing about the search" in result["message"]
    assert TEST_KEY not in str(result)


async def test_a_gone_route_is_api_changed_not_not_found(served, dpla):
    dpla.get(ITEMS).mock(
        return_value=httpx.Response(
            404,
            text=fixture_text("error_404_route_gone.txt"),
            headers={"content-type": "text/plain"},
        )
    )
    result = await call_tool("search_items", query="atlas")
    assert result["error"] == "api_changed" and "not a missing record" in result["message"]


async def test_a_bad_request_passes_dplas_message_verbatim(served, dpla):
    route_search(dpla, httpx.Response(400, json=fixture("error_400_unrecognized_parameter.json")))
    result = await call_tool("search_items", query="atlas")
    assert result == {
        "error": "bad_request",
        "status": 400,
        "message": "Unrecognized parameter: sourceResource.subject",
    }


async def test_a_syntax_error_gets_a_hint(served, dpla):
    body = {"error": "bad_request", "message": "The q parameter contains invalid search syntax."}
    route_search(dpla, httpx.Response(400, json=body))
    result = await call_tool("search_items", query='"unbalanced')
    assert "quotes and parentheses" in result["hint"]


@pytest.mark.parametrize("status", [429, 503])
async def test_rate_limiting_is_reported_as_such_not_as_empty(served, dpla, status):
    route_search(dpla, httpx.Response(status))
    result = await call_tool("search_items", query="atlas")
    assert result["error"] == "rate_limited"
    assert "not an empty result" in result["message"]


async def test_an_outage_is_reported_as_such(served, dpla):
    route_search(dpla, httpx.Response(502, text="bad gateway"))
    assert (await call_tool("search_items", query="atlas"))["error"] == "upstream_error"


async def test_a_missing_key_surfaces_on_the_first_call(monkeypatch, tmp_path):
    monkeypatch.setattr(server.state, "client", None)
    monkeypatch.delenv("DPLA_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    result = await call_tool("search_items", query="atlas")
    assert result["error"] == "not_configured"
    assert "DPLA_API_KEY is not set" in result["message"]
    status = await call_tool("api_status")
    assert status["key_configured"] is False and "not set" in status["problem"]


async def test_a_bad_setting_surfaces_on_the_first_call(monkeypatch):
    monkeypatch.setattr(server.state, "client", None)

    def broken():
        raise ConfigError("DPLA_TIMEOUT must be a number; got 'thirty'.")

    monkeypatch.setattr(server, "load_config", broken)
    result = await call_tool("get_item", ids=[ATLAS_1929])
    assert result == {
        "error": "not_configured",
        "message": "DPLA_TIMEOUT must be a number; got 'thirty'.",
    }


# --------------------------------------------------------------------------- #
# api_status
# --------------------------------------------------------------------------- #
async def test_api_status_counts_and_never_shows_the_key(served, dpla):
    route_fetch(dpla)
    route_search(dpla, httpx.Response(403, json=fixture("error_403_invalid_api_key.json")))
    await call_tool("get_item", ids=[ATLAS_1929])
    await call_tool("get_item", ids=[ATLAS_1929])
    await call_tool("search_items", query="atlas")
    status = await call_tool("api_status")
    assert status["key_configured"] is True
    assert (status["dpla_calls_this_session"], status["cache_hits_this_session"]) == (2, 1)
    assert status["last_error"]["error"] == "invalid_api_key"
    assert TEST_KEY not in str(status)
