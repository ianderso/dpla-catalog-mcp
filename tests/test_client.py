"""The client: what it sends, what it caches, how it paces and retries, and where it may go."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from dpla_catalog_mcp import __version__
from dpla_catalog_mcp import client as client_module
from dpla_catalog_mcp.client import (
    API_HOST,
    PROJECT_URL,
    RECORD_CACHE_DAYS,
    RETRY_AFTER_CAP,
    SEARCH_CACHE_DAYS,
    DplaApiError,
    HostNotAllowed,
    ManifestUnavailable,
    _retry_after,
    manifest_host_problem,
)

from .conftest import (
    ATLAS_1929,
    DARTMOUTH_MANIFEST,
    GALVESTON_1870,
    HATHI_1929,
    ITEMS,
    MISSING,
    TEST_KEY,
    UIUC_MANIFEST,
    UNT_MANIFEST,
    fixture,
    fixture_text,
    make_client,
    params_of,
    route_fetch,
    route_search,
)

SEARCH = {"sourceResource.title": "atlas of Champaign County", "page_size": 10}


# --------------------------------------------------------------------------- #
# What goes on the wire
# --------------------------------------------------------------------------- #
async def test_a_search_is_a_get_with_the_key_in_a_header_only(client, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await client.search(SEARCH)
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.host == API_HOST and request.url.scheme == "https"
    assert request.headers["authorization"] == TEST_KEY
    assert TEST_KEY not in str(request.url)
    assert "api_key" not in params_of(request)
    assert params_of(request) == {
        "sourceResource.title": "atlas of Champaign County",
        "page_size": "10",
    }


async def test_the_user_agent_names_the_project_and_version(client, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await client.search(SEARCH)
    agent = route.calls.last.request.headers["user-agent"]
    assert agent == f"dpla-catalog-mcp/{__version__} (+{PROJECT_URL})"


async def test_none_is_dropped_and_booleans_are_spelled_as_dpla_wants(client, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await client.search({"q": "atlas", "op": None, "exact_field_match": True})
    assert params_of(route.calls.last.request) == {"q": "atlas", "exact_field_match": "true"}


async def test_a_request_to_another_host_is_refused(client):
    with pytest.raises(HostNotAllowed):
        await client._http.get("https://example.org/v2/items")


async def test_plain_http_to_the_api_is_refused(client):
    with pytest.raises(HostNotAllowed):
        await client._http.get("http://api.dp.la/v2/items")


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
async def test_a_repeat_is_served_from_the_cache(client, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    first = await client.search(SEARCH)
    second = await client.search(SEARCH)
    assert first == second
    assert route.call_count == 1
    assert (client.live_calls, client.cache_hits) == (1, 1)


async def test_parameter_order_does_not_split_the_cache(client, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await client.search({"q": "atlas", "page_size": 5})
    await client.search({"page_size": 5, "q": "atlas"})
    assert route.call_count == 1


async def test_refresh_asks_again_and_replaces_the_copy(client, dpla):
    answers = iter([{"count": 1, "docs": []}, {"count": 2, "docs": []}])
    route = route_search(dpla, lambda request: httpx.Response(200, json=next(answers)))
    await client.search({"q": "atlas"})
    fresh = await client.search({"q": "atlas"}, refresh=True)
    again = await client.search({"q": "atlas"})
    assert fresh["count"] == 2 and again["count"] == 2
    assert route.call_count == 2


def _age_cache(client, days: float) -> None:
    for entry in client.cache_dir.glob("*.json"):
        data = json.loads(entry.read_text())
        data["stored"] = time.time() - days * 86_400
        entry.write_text(json.dumps(data))


async def test_a_search_expires_after_a_week(client, dpla):
    route = route_search(dpla, {"count": 0, "docs": [], "facets": []})
    await client.search({"q": "atlas"})
    _age_cache(client, SEARCH_CACHE_DAYS - 1)
    await client.search({"q": "atlas"})
    assert route.call_count == 1
    _age_cache(client, SEARCH_CACHE_DAYS + 1)
    await client.search({"q": "atlas"})
    assert route.call_count == 2


async def test_a_record_is_kept_for_a_month(client, dpla):
    route = route_fetch(dpla)
    await client.fetch_items([ATLAS_1929])
    _age_cache(client, SEARCH_CACHE_DAYS + 1)
    await client.fetch_items([ATLAS_1929])
    assert route.call_count == 1
    _age_cache(client, RECORD_CACHE_DAYS + 1)
    await client.fetch_items([ATLAS_1929])
    assert route.call_count == 2


async def test_an_unreadable_cache_entry_is_fetched_again(client, dpla):
    route = route_search(dpla, fixture("search_champaign_atlas.json"))
    await client.search(SEARCH)
    for entry in client.cache_dir.glob("*.json"):
        entry.write_text("{truncated")
    await client.search(SEARCH)
    assert route.call_count == 2


async def test_a_failure_is_not_cached(client, dpla):
    answers = iter(
        [
            httpx.Response(400, json=fixture("error_400_unrecognized_parameter.json")),
            httpx.Response(200, json=fixture("search_champaign_atlas.json")),
        ]
    )
    route_search(dpla, lambda request: next(answers))
    with pytest.raises(DplaApiError):
        await client.search(SEARCH)
    assert (await client.search(SEARCH))["count"] == 7


async def test_the_key_is_in_no_cache_file_and_no_cache_name(client, dpla):
    route_search(dpla, fixture("search_champaign_atlas.json"))
    route_fetch(dpla)
    await client.search(SEARCH)
    await client.fetch_items([ATLAS_1929, HATHI_1929])
    files = list(client.cache_dir.rglob("*"))
    assert files
    for path in files:
        assert TEST_KEY not in path.name
        if path.is_file():
            assert TEST_KEY not in path.read_text()


# --------------------------------------------------------------------------- #
# Fetching records by id
# --------------------------------------------------------------------------- #
async def test_several_ids_go_in_one_request(client, dpla):
    route = route_fetch(dpla)
    found = await client.fetch_items([ATLAS_1929, HATHI_1929])
    assert list(found) == [ATLAS_1929, HATHI_1929]
    assert route.call_count == 1
    assert route.calls.last.request.url.path == f"/v2/items/{ATLAS_1929},{HATHI_1929}"


async def test_only_uncached_ids_are_asked_for(client, dpla):
    route = route_fetch(dpla)
    await client.fetch_items([ATLAS_1929])
    found = await client.fetch_items([ATLAS_1929, HATHI_1929])
    assert set(found) == {ATLAS_1929, HATHI_1929}
    assert route.calls.last.request.url.path == f"/v2/items/{HATHI_1929}"


async def test_an_unknown_id_among_several_is_left_out(client, dpla):
    """Recorded: three real ids and one invented one gave count 3."""
    recorded = fixture("fetch_multi_three_of_four.json")
    dpla.route(url__regex=r"/v2/items/").mock(return_value=httpx.Response(200, json=recorded))
    ids = [d["id"] for d in recorded["docs"]] + [MISSING]
    found = await client.fetch_items(ids)
    assert list(found) == ids[:3]


async def test_a_lone_unknown_id_is_simply_absent(client, dpla):
    route_fetch(dpla)
    assert await client.fetch_items([MISSING]) == {}


async def test_a_404_without_dplas_error_body_is_not_a_missing_record(client, dpla):
    gone = httpx.Response(
        404, text=fixture_text("error_404_route_gone.txt"), headers={"content-type": "text/plain"}
    )
    dpla.route(url__regex=r"/v2/items/").mock(return_value=gone)
    with pytest.raises(DplaApiError) as caught:
        await client.fetch_items([ATLAS_1929])
    assert caught.value.status == 404 and caught.value.code == ""


async def test_repeated_ids_are_asked_for_once(client, dpla):
    route = route_fetch(dpla)
    await client.fetch_items([ATLAS_1929, ATLAS_1929])
    assert route.calls.last.request.url.path == f"/v2/items/{ATLAS_1929}"


# --------------------------------------------------------------------------- #
# Errors, retries and pacing
# --------------------------------------------------------------------------- #
async def test_a_403_carries_dplas_code_and_is_not_retried(client, dpla):
    route = route_search(dpla, httpx.Response(403, json=fixture("error_403_invalid_api_key.json")))
    with pytest.raises(DplaApiError) as caught:
        await client.search(SEARCH)
    assert (caught.value.status, caught.value.code) == (403, "invalid_api_key")
    assert route.call_count == 1


async def test_a_400_passes_dplas_message_on(client, dpla):
    route_search(dpla, httpx.Response(400, json=fixture("error_400_unrecognized_parameter.json")))
    with pytest.raises(DplaApiError) as caught:
        await client.search(SEARCH)
    assert caught.value.detail == "Unrecognized parameter: sourceResource.subject"


async def test_a_429_is_retried_once_honouring_retry_after(tmp_path, dpla):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    client = make_client(tmp_path, sleep=fake_sleep)
    answers = iter(
        [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(200, json={"count": 0, "docs": []}),
        ]
    )
    route = route_search(dpla, lambda request: next(answers))
    assert (await client.search({"q": "atlas"}))["count"] == 0
    assert route.call_count == 2
    assert waits == [7.0]


async def test_retry_after_is_capped(tmp_path, dpla):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    client = make_client(tmp_path, sleep=fake_sleep)
    answers = iter(
        [
            httpx.Response(503, headers={"retry-after": "3600"}),
            httpx.Response(200, json={"count": 0, "docs": []}),
        ]
    )
    route_search(dpla, lambda request: next(answers))
    await client.search({"q": "atlas"})
    assert waits == [RETRY_AFTER_CAP]


async def test_a_5xx_that_persists_gives_up_after_one_retry(client, dpla):
    route = route_search(dpla, httpx.Response(503, text="down"))
    with pytest.raises(DplaApiError) as caught:
        await client.search({"q": "atlas"})
    assert caught.value.status == 503
    assert route.call_count == 2


async def test_a_4xx_other_than_429_is_not_retried(client, dpla):
    route = route_search(dpla, httpx.Response(400, json=fixture("error_400_q_length.json")))
    with pytest.raises(DplaApiError):
        await client.search({"q": "a"})
    assert route.call_count == 1


async def test_no_response_at_all_is_status_zero(client, dpla):
    dpla.get(ITEMS).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(DplaApiError) as caught:
        await client.search({"q": "atlas"})
    assert caught.value.status == 0
    assert "ConnectTimeout" in caught.value.detail


async def test_a_body_that_is_not_json_is_an_error(client, dpla):
    route_search(dpla, httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(DplaApiError) as caught:
        await client.search({"q": "atlas"})
    assert caught.value.status == 502


async def test_the_key_is_scrubbed_from_an_error_and_the_last_error(client, dpla):
    echo = {"error": "bad_request", "message": f"Invalid parameter value {TEST_KEY}"}
    route_search(dpla, httpx.Response(400, json=echo))
    with pytest.raises(DplaApiError) as caught:
        await client.search({"q": "atlas"})
    assert TEST_KEY not in str(caught.value)
    assert "[redacted]" in caught.value.detail
    assert TEST_KEY not in json.dumps(client.last_error)
    assert client.last_error["status"] == 400


async def test_requests_are_spaced_by_the_minimum_interval(tmp_path, dpla):
    now = [100.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    client = make_client(tmp_path, min_interval=0.25, clock=lambda: now[0], sleep=fake_sleep)
    route_search(dpla, {"count": 0, "docs": []})
    await client.search({"q": "a1"})
    now[0] += 0.1
    await client.search({"q": "b1"})
    assert waits == [pytest.approx(0.15)]


async def test_identical_concurrent_calls_share_one_request(client, dpla):
    gate = asyncio.Event()

    async def slow(request):
        await gate.wait()
        return httpx.Response(200, json=fixture("search_champaign_atlas.json"))

    route = dpla.get(ITEMS).mock(side_effect=slow)
    first = asyncio.create_task(client.search(SEARCH))
    second = asyncio.create_task(client.search(SEARCH))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gate.set()
    assert await first == await second
    assert route.call_count == 1
    assert client.shared_waits == 1


@pytest.mark.parametrize(
    ("header", "seconds"),
    [("7", 7.0), ("0", 0.0), ("", None), ("soon", None), (None, None)],
)
def test_retry_after_seconds(header, seconds):
    headers = {} if header is None else {"retry-after": header}
    assert _retry_after(httpx.Response(429, headers=headers)) == seconds


def test_retry_after_as_a_date():
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)
    assert 15 < _retry_after(httpx.Response(429, headers={"retry-after": when})) <= 20


# --------------------------------------------------------------------------- #
# IIIF manifests on institutions' sites
# --------------------------------------------------------------------------- #
async def test_a_manifest_is_fetched_without_the_key(client, dpla):
    route = dpla.get(UNT_MANIFEST).mock(
        return_value=httpx.Response(200, json=fixture("manifest_unt_galveston_1870_v2.json"))
    )
    manifest = await client.manifest(UNT_MANIFEST)
    assert manifest["label"] == "Galveston City Directory, 1870"
    request = route.calls.last.request
    assert "authorization" not in request.headers
    assert TEST_KEY not in str(request.url) and TEST_KEY not in str(request.headers)
    assert request.headers["user-agent"].startswith("dpla-catalog-mcp/")
    assert client.manifest_calls == 1 and client.live_calls == 0


async def test_a_manifest_is_cached(client, dpla):
    route = dpla.get(UNT_MANIFEST).mock(
        return_value=httpx.Response(200, json=fixture("manifest_unt_galveston_1870_v2.json"))
    )
    await client.manifest(UNT_MANIFEST)
    await client.manifest(UNT_MANIFEST)
    assert route.call_count == 1


async def test_a_refusal_is_reported_never_retried_and_never_cached(client, dpla):
    """Recorded: the University of Illinois answers scripts with a 403 page."""
    route = dpla.get(UIUC_MANIFEST).mock(
        return_value=httpx.Response(
            403, text=fixture_text("manifest_uiuc_403.html"), headers={"content-type": "text/html"}
        )
    )
    for _ in range(2):
        with pytest.raises(ManifestUnavailable) as caught:
            await client.manifest(UIUC_MANIFEST)
        assert caught.value.status == 403
        assert "403" in caught.value.reason
    assert route.call_count == 2, "one request per call: no retry, and no cached failure"


async def test_a_bot_check_page_with_a_200_is_not_a_manifest(client, dpla):
    dpla.get(UNT_MANIFEST).mock(
        return_value=httpx.Response(
            200,
            text="<html><title>Just a moment...</title></html>",
            headers={"content-type": "text/html"},
        )
    )
    with pytest.raises(ManifestUnavailable) as caught:
        await client.manifest(UNT_MANIFEST)
    assert "web page" in caught.value.reason


async def test_requests_to_one_host_are_a_second_apart(tmp_path, dpla):
    now = [50.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    client = make_client(tmp_path, iiif_interval=1.0, clock=lambda: now[0], sleep=fake_sleep)
    second = UNT_MANIFEST.replace("metapth636853", "metapth636856")
    for url in (UNT_MANIFEST, second):
        dpla.get(url).mock(
            return_value=httpx.Response(200, json=fixture("manifest_unt_galveston_1870_v2.json"))
        )
    dpla.get(DARTMOUTH_MANIFEST).mock(
        return_value=httpx.Response(200, json=fixture("manifest_dartmouth_northumberland_v3.json"))
    )
    await client.manifest(UNT_MANIFEST)
    now[0] += 0.3
    await client.manifest(DARTMOUTH_MANIFEST)
    await client.manifest(second)
    assert waits == [pytest.approx(0.7)], "another host does not wait; the same host does"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/manifest",
        "https://[::1]/manifest",
        "http://localhost:8080/manifest",
        "https://intranet/manifest",
        "https://files.example.local/manifest",
        "https://api.dp.la/v2/items",
        "ftp://example.org/manifest.json",
        "file:///etc/passwd",
        "",
    ],
)
async def test_a_manifest_address_that_is_not_an_institutions_site_is_never_fetched(
    client, dpla, url
):
    with pytest.raises(ManifestUnavailable) as caught:
        await client.manifest(url)
    assert caught.value.reason.startswith("Not fetched")
    assert dpla.calls.call_count == 0


async def test_a_redirect_to_a_private_address_is_refused(client, dpla):
    dpla.get(UNT_MANIFEST).mock(
        return_value=httpx.Response(301, headers={"location": "http://169.254.169.254/latest"})
    )
    with pytest.raises(ManifestUnavailable) as caught:
        await client.manifest(UNT_MANIFEST)
    assert "Not fetched" in caught.value.reason
    assert dpla.calls.call_count == 1


async def test_a_redirect_to_another_institution_host_is_followed(client, dpla):
    """Recorded behaviour: api.mohistory.org answers 301 to images.mohistory.org."""
    dpla.get("https://api.mohistory.org/manifest/Lib217").mock(
        return_value=httpx.Response(
            301, headers={"location": "https://images.mohistory.org/manifests/Lib217"}
        )
    )
    final = dpla.get("https://images.mohistory.org/manifests/Lib217").mock(
        return_value=httpx.Response(200, json=fixture("manifest_unt_galveston_1870_v2.json"))
    )
    await client.manifest("https://api.mohistory.org/manifest/Lib217")
    assert final.call_count == 1
    assert "authorization" not in final.calls.last.request.headers


async def test_an_oversized_manifest_is_not_read(client, dpla, monkeypatch):
    monkeypatch.setattr(client_module, "MANIFEST_MAX_BYTES", 1_000)
    dpla.get(UNT_MANIFEST).mock(
        return_value=httpx.Response(200, json=fixture("manifest_unt_galveston_1870_v2.json"))
    )
    with pytest.raises(ManifestUnavailable) as caught:
        await client.manifest(UNT_MANIFEST)
    assert "larger than" in caught.value.reason


async def test_a_site_that_does_not_answer(client, dpla):
    dpla.get(UNT_MANIFEST).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(ManifestUnavailable) as caught:
        await client.manifest(UNT_MANIFEST)
    assert "No answer from texashistory.unt.edu" in caught.value.reason
    assert client.last_error["error"] == "manifest_unavailable"


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (UNT_MANIFEST, True),
        ("http://cdm17228.contentdm.oclc.org/iiif/info/plat/10626/manifest.json", True),
        ("https://10.0.0.5/m.json", False),
        ("https://api.dp.la/m.json", False),
        ("https://example.internal/m.json", False),
        (None, False),
    ],
)
def test_manifest_host_problem(url, ok):
    assert (manifest_host_problem(url) is None) is ok


async def test_both_transports_close(client):
    await client.aclose()
    assert client._http.is_closed and client._iiif.is_closed


def test_the_fetch_ids_shape_used_by_the_tests_matches_recorded_records():
    """Guard the conftest helper itself: the recorded records are what it serves."""
    from .conftest import recorded_docs

    docs = recorded_docs()
    assert {ATLAS_1929, HATHI_1929, GALVESTON_1870} <= set(docs)
