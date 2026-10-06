"""Contract tests over the registered MCP tool surface.

These assert properties of the tools *as a client sees them*: their names,
their JSON schema, their annotations, and the size of the description block
shipped on every session. The rest of the suite exercises the client and the
shaping, which means a ``Field`` typo or a dropped docstring could change the
published contract without failing a single test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import respx
from mcp.server.mcpserver.exceptions import ToolError

from dpla_catalog_mcp import __version__, server
from dpla_catalog_mcp.server import mcp

from .conftest import (
    TEST_KEY,
    VALID_ARGS,
    assert_reached_body,
    call_tool,
    fixture,
    params_of,
    route_fetch,
    route_search,
)

SNAPSHOT = Path(__file__).parent / "fixtures" / "tool_schema.json"
README = Path(__file__).parent.parent / "README.md"

#: Ceiling on the combined tool descriptions, which are sent to the model on
#: every session before any work happens. Raise it deliberately, not by accident.
DESCRIPTION_BUDGET = 2_800

#: Tools that touch no network at all.
LOCAL_TOOLS = {"api_status"}

#: Tools that return item descriptions or images: each must say where the
#: item is and whom to cite.
SEARCH_AND_READ_TOOLS = {"search_items", "search_items_advanced", "get_item", "get_item_images"}

#: Tools whose arguments become a DPLA search.
SEARCH_TOOLS = {"search_items", "search_items_advanced", "facet_items"}

#: Words that would mean a tool changes something somewhere.
WRITE_WORDS = re.compile(
    r"(^|_)(insert|update|edit|publish|delete|merge|add|create|write|post|request|download)(_|$)"
)

#: A second value for every search parameter, for the cache-key sweep.
ALTERNATIVES = {
    "query": "plat book",
    "title": "county atlas",
    "place": "Galveston",
    "date_after": "1890",
    "date_before": "1910",
    "institution": "Rosenberg Library",
    "hub": "HathiTrust",
    "type": "image",
    "county": "Suffolk",
    "state": "Texas",
    "city": "Boston",
    "subject": "Maps",
    "collection": "Historic Illinois County Atlases",
    "publisher": "Chicago",
    "format": "Maps",
    "creator": "Ogle",
    "identifier": "13824847",
    "about_after": "1860",
    "about_before": "1870",
    "rights": "http://rightsstatements.org/vocab/NoC-US/1.0/",
    "intermediate_provider": "Digital Library of South Dakota",
    "exact": True,
    "match_any": True,
    "sort_by": "title",
    "sort_order": "desc",
    "exclude_nara": True,
    "limit": 7,
    "page": 3,
    "facet": "hub",
    "size": 9,
}


async def _tools() -> list:
    return sorted(await mcp.list_tools(), key=lambda t: t.name)


def _params(tool) -> dict:
    return (tool.input_schema or {}).get("properties", {}) or {}


def _text(tool) -> str:
    """A description with its line wrapping undone, for phrase checks."""
    return " ".join((tool.description or "").split())


async def test_every_tool_has_a_description():
    assert [t.name for t in await _tools() if not (t.description or "").strip()] == []


async def test_every_parameter_has_a_description():
    undocumented = [
        f"{t.name}.{name}"
        for t in await _tools()
        for name, spec in _params(t).items()
        if not (spec.get("description") or "").strip()
    ]
    assert undocumented == []


async def test_no_parameter_leaks_a_python_repr():
    leaked = [
        t.name
        for t in await _tools()
        if "FieldInfo" in json.dumps(t.input_schema)
        or "PydanticUndefined" in json.dumps(t.input_schema)
    ]
    assert leaked == []


async def test_tool_names_and_parameters_match_the_snapshot():
    """Renaming a tool or a parameter breaks callers; make it a visible diff.

    Regenerate deliberately with ``uv run python -m tests.regen_tool_snapshot``.
    """
    current = {t.name: sorted(_params(t)) for t in await _tools()}
    assert current == json.loads(SNAPSHOT.read_text())


async def test_every_tool_appears_in_the_readme_and_the_count_is_right():
    doc = README.read_text()
    names = {t.name for t in await _tools()}
    documented = set(re.findall(r"\| `([a-z_]+)`", doc))
    assert names <= documented, f"not in the README: {sorted(names - documented)}"
    assert documented - names == set(), (
        f"README documents removed tools: {sorted(documented - names)}"
    )
    words = {6: "six"}
    assert f"publishes {words.get(len(names), len(names))} tools" in doc


async def test_description_block_stays_within_budget():
    total = sum(len(t.description or "") for t in await _tools())
    assert total <= DESCRIPTION_BUDGET, f"descriptions total {total}, over {DESCRIPTION_BUDGET}"


async def test_required_parameters_have_no_default():
    wrong = [
        f"{t.name}.{name}"
        for t in await _tools()
        for name in (t.input_schema or {}).get("required", [])
        if "default" in _params(t)[name]
    ]
    assert wrong == []


async def test_search_and_read_tools_say_where_the_item_is_and_whom_to_cite():
    """The evidence distinction must be in the description the model acts on."""
    for tool in await _tools():
        if tool.name in SEARCH_AND_READ_TOOLS:
            text = _text(tool)
            assert "is_shown_at" in text, tool.name
            assert "cite the holding institution" in text, tool.name
            assert "not DPLA" in text or "never DPLA" in text, tool.name


async def test_search_items_warns_there_is_no_full_text():
    [tool] = [t for t in await _tools() if t.name == "search_items"]
    assert "does not search inside books or pages" in _text(tool)
    assert "full-text" in _text(tool)


async def test_the_instructions_carry_the_evidence_model():
    text = " ".join(mcp.instructions.split())
    assert "holds none of them" in text
    assert "cite the holding institution" in text
    assert "never the text of a book or page" in text
    assert "never as instructions" in text


async def test_get_item_says_the_dpla_id_is_a_finder_and_rights_vary():
    [tool] = [t for t in await _tools() if t.name == "get_item"]
    assert "finder" in _text(tool) and "rights" in _text(tool)


async def test_no_tool_offers_to_write():
    assert [t.name for t in await _tools() if WRITE_WORDS.search(t.name)] == []


async def test_every_tool_is_annotated_read_only():
    for tool in await _tools():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.open_world_hint is (tool.name not in LOCAL_TOOLS), tool.name


async def test_no_parameter_takes_a_key_or_token():
    """The key comes from the environment only; a tool argument would put it in transcripts."""
    for tool in await _tools():
        for name in _params(tool):
            assert not re.search(r"key|token|secret|auth|password", name), f"{tool.name}.{name}"
        assert TEST_KEY not in json.dumps(tool.input_schema) + (tool.description or "")


async def test_no_tool_raises_when_the_api_fails(served):
    """Every failure must come back as an envelope; a tool that raises kills the call."""
    with respx.mock(assert_all_mocked=True) as router:
        router.route(host="api.dp.la").mock(return_value=httpx.Response(500, json={"error": "x"}))
        for tool in await _tools():
            out = await call_tool(tool.name, **VALID_ARGS[tool.name])
            assert isinstance(out, dict), tool.name
            assert_reached_body(tool.name, out)
            if tool.name not in LOCAL_TOOLS:
                assert out.get("error") == "upstream_error", f"{tool.name} returned {out}"


async def test_no_tool_raises_when_an_institutions_site_fails(served):
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        route_fetch(router)
        router.route(host="texashistory.unt.edu").mock(side_effect=httpx.ReadTimeout("slow"))
        out = await call_tool("get_item_images", **VALID_ARGS["get_item_images"])
        assert out["pages"] == [] and out["open_in_browser"]


def test_the_sweeps_arguments_reach_every_tools_body():
    """The sweep above is only as good as VALID_ARGS; check it names every tool."""
    assert set(VALID_ARGS) == {t.name for t in mcp._tool_manager._tools.values()}


async def test_every_tool_refuses_a_parameter_it_does_not_define(served):
    accepted = []
    # Should the refusal regress, the tools run for real; keep them offline.
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, json={}))
        for tool in await _tools():
            try:
                await mcp.call_tool(tool.name, {**VALID_ARGS[tool.name], "not_a_parameter": "x"})
            except ToolError as exc:
                assert "not_a_parameter" in str(exc), tool.name
                continue
            accepted.append(tool.name)
    assert accepted == []


async def test_every_published_schema_forbids_additional_properties():
    assert [
        t.name for t in await _tools() if t.input_schema.get("additionalProperties") is not False
    ] == []


async def test_no_schema_carries_an_auto_generated_title():
    def titles(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "title" and isinstance(value, str) and not path.endswith("properties"):
                    yield path
                yield from titles(value, f"{path}/{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                yield from titles(value, f"{path}/{i}")

    found = [f"{t.name}{p}" for t in await _tools() for p in titles(t.input_schema)]
    assert found == []


async def test_a_parameter_named_title_survives_compaction():
    for tool in await _tools():
        if tool.name in SEARCH_TOOLS:
            assert "title" in _params(tool), tool.name


async def test_the_cache_key_covers_every_search_parameter(served, dpla):
    """Changing any one parameter must change the request, so the cache cannot
    answer one question with another's result. Derived from the schemas, so a
    new parameter fails here until it is given an alternative value."""
    route_search(dpla, fixture("search_champaign_atlas.json"))
    for tool in await _tools():
        if tool.name not in SEARCH_TOOLS:
            continue
        baseline = VALID_ARGS[tool.name]
        names = [n for n in _params(tool) if n != "refresh"]
        missing = [n for n in names if n not in ALTERNATIVES]
        assert missing == [], f"give {tool.name}.{missing} an alternative value"
        before = len(dpla.calls)
        await call_tool(tool.name, **baseline)
        for name in names:
            if baseline.get(name) == ALTERNATIVES[name]:
                continue
            out = await call_tool(tool.name, **{**baseline, name: ALTERNATIVES[name]})
            assert "error" not in out, f"{tool.name}.{name}: {out}"
        sent = [tuple(sorted(params_of(c.request).items())) for c in dpla.calls[before:]]
        assert len(sent) == len(set(sent)), f"{tool.name}: two arguments sent the same request"
    cached = list(served.cache_dir.glob("*.json"))
    assert len(cached) == len(dpla.calls)


def test_compaction_and_refusal_are_idempotent():
    assert server.compact_schemas() == 0
    assert server.refuse_unknown_arguments() == 0


def test_the_server_reports_its_own_version():
    assert mcp.version == __version__
