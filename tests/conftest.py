"""Shared fixtures. Every test runs against a mocked API; nothing touches DPLA.

The fixtures under ``fixtures/`` are real responses recorded on 2026-10-05
(see docs/API-NOTES.md): DPLA searches and records made by the server's own
requests, IIIF manifests from the Portal to Texas History (v2) and Dartmouth
(v3), the 403 page the University of Illinois serves to scripts, and DPLA's
error bodies. One change was made: the Portal's manifest for the 1870
Galveston city directory is trimmed from 152 canvases to 8, and says so in a
``_trimmed_note`` key. No key appears in any of them; the server sends it in
a header. The records are historical: county atlases of 1909-1951, city
directories of 1859-1918, a 1793 map, and nineteenth-century homestead
material.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import respx

from dpla_catalog_mcp import server
from dpla_catalog_mcp.client import API_BASE, DplaClient
from dpla_catalog_mcp.config import Config

FIXTURES = Path(__file__).parent / "fixtures"

#: A made-up key of the right shape. Tests assert it never leaks.
TEST_KEY = "TESTkey0-0123456789abcdef0123456"

#: Where every mocked search goes.
ITEMS = f"{API_BASE}/items"

#: Any fetch by id.
FETCH = respx.patterns.M(url__regex=r"^https://api\.dp\.la/v2/items/[^?]+$")

UNT_MANIFEST = "https://texashistory.unt.edu/ark:/67531/metapth636853/manifest/"
DARTMOUTH_MANIFEST = (
    "https://collections.dartmouth.edu/archive/iiif/nh-cities-towns/"
    "nh-cities-towns-northumberland-1793-mods.json"
)
UIUC_MANIFEST = (
    "https://digital.library.illinois.edu/items/465c93c0-6f6a-013b-43a3-02d0d7bfd6e4-c/manifest"
)

ATLAS_1929 = "4917d2c8281726bc960e78330c17e5fe"
HATHI_1929 = "b35d7d4066dbb76bff2ce7ab93eca613"
GALVESTON_1870 = "8875b2794f3059a6d30cf64cd9a30025"
DARTMOUTH_1793 = "5e4b30238fc58d1689d8009ef143ee90"
MISSING = "0000000000000000000000000000beef"
assert len(TEST_KEY) == 32


def fixture(name: str):
    """Load one recorded JSON response."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    """Load one recorded non-JSON response."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def recorded_docs() -> dict[str, dict]:
    """Every full record in the fixtures, keyed by DPLA id."""
    docs: dict[str, dict] = {}
    for path in sorted(FIXTURES.glob("fetch_*.json")):
        for doc in fixture(path.name).get("docs", []):
            docs[doc["id"]] = doc
    return docs


def params_of(request: httpx.Request) -> dict[str, str]:
    """The query parameters a mocked request carried."""
    return dict(parse_qsl(urlsplit(str(request.url)).query, keep_blank_values=True))


def make_client(tmp_path: Path, **kwargs) -> DplaClient:
    """A client with no pacing and no back-off, caching under ``tmp_path``."""
    options = {"min_interval": 0.0, "backoff": 0.0, "iiif_interval": 0.0}
    options.update(kwargs)
    return DplaClient(TEST_KEY, tmp_path / "cache", **options)


@pytest.fixture
def client(tmp_path) -> DplaClient:
    return make_client(tmp_path)


@pytest.fixture
def served(tmp_path, monkeypatch) -> DplaClient:
    """Install a fast test client as the server's client for the duration of a test."""
    c = make_client(tmp_path)
    monkeypatch.setattr(server.state, "client", c)
    monkeypatch.setattr(
        server.state, "config", Config(api_key=TEST_KEY, cache_dir=tmp_path / "cache")
    )
    return c


@pytest.fixture
def dpla():
    """A respx router. Any request it does not expect fails the test."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


def route_search(router: respx.MockRouter, answer) -> respx.Route:
    """Answer every ``/items`` search with ``answer`` (a dict, a Response or a callable)."""
    if isinstance(answer, dict):
        return router.get(ITEMS).mock(return_value=httpx.Response(200, json=answer))
    if isinstance(answer, httpx.Response):
        return router.get(ITEMS).mock(return_value=answer)
    return router.get(ITEMS).mock(side_effect=answer)


def route_fetch(router: respx.MockRouter, docs: dict[str, dict] | None = None) -> respx.Route:
    """Answer fetches by id as DPLA does, from recorded records.

    Several ids get ``{count, docs}`` with unknown ids left out, sorted by id
    as DPLA sorts them; a lone unknown id gets DPLA's JSON 404.
    """
    known = recorded_docs() if docs is None else docs

    def respond(request: httpx.Request) -> httpx.Response:
        ids = urlsplit(str(request.url)).path.rsplit("/", 1)[-1].split(",")
        found = [known[i] for i in sorted(ids) if i in known]
        if len(ids) == 1 and not found:
            return httpx.Response(404, json=fixture("error_404_not_found.json"))
        return httpx.Response(200, json={"count": len(found), "docs": found})

    return router.route(FETCH, method="GET").mock(side_effect=respond)


async def call_tool(tool_name: str, /, **arguments) -> dict:
    """Invoke a tool the way a client does, so Field defaults are resolved."""
    result = await server.mcp.call_tool(tool_name, arguments)
    return json.loads(result.content[0].text)


# --------------------------------------------------------------------------- #
# Argument building for whole-surface sweeps
# --------------------------------------------------------------------------- #
#: Errors a tool returns from its own input checks, before any work. A sweep
#: that gets one of these has not tested what it thinks it has.
LOCAL_VALIDATION_ERRORS = frozenset(
    {"no_criteria", "invalid_id", "invalid_argument", "paging_limit"}
)

#: Arguments that carry each tool past its own checks.
VALID_ARGS = {
    "search_items": {"title": "atlas of Champaign County"},
    "search_items_advanced": {"title": "atlas of Champaign County", "sort_by": "date"},
    "facet_items": {"facet": "institution", "title": "plat book"},
    "get_item": {"ids": [ATLAS_1929]},
    "get_item_images": {"id": GALVESTON_1870},
    "api_status": {},
}


def assert_reached_body(tool_name: str, result) -> None:
    """Fail if a sweep stopped at input validation instead of the tool's body."""
    if isinstance(result, dict) and result.get("error") in LOCAL_VALIDATION_ERRORS:
        raise AssertionError(
            f"{tool_name} rejected the sweep's arguments with {result['error']!r}; "
            f"update VALID_ARGS in tests/conftest.py. Message: {result.get('message')!r}"
        )
