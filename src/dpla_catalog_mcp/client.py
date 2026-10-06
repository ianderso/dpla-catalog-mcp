"""Async client for the DPLA API, and for the IIIF manifests its records name.

Two HTTP clients, kept apart on purpose:

* **The API client** talks to ``https://api.dp.la`` and nothing else. It is
  the only thing that holds the key, which it sends in the ``Authorization``
  header (verified live 2026-10-05), so the key is never in a URL, a log line
  or a cache key. A request hook refuses any other host, and redirects are
  not followed.
* **The IIIF client** fetches a manifest from the holding institution's own
  site, at the address a DPLA record gives. It holds no key and strips any
  ``Authorization`` header, refuses ``api.dp.la``, IP addresses and local
  names, and spaces requests to any one host at least a second apart. A
  refusal (a 403, a bot check that answers HTML) is reported, never retried
  or worked around.

Courtesy and economy, as in the sibling servers:

* **One API request at a time,** at least ``min_interval`` apart.
* **Identical concurrent calls share one request.**
* **Answers are cached on disk,** keyed by the path and the sorted parameters
  (never the key). Searches and facets keep 7 days, records and manifests 30:
  DPLA is re-harvested monthly, so nothing is kept forever. A failure is
  never cached, and an unreadable entry is fetched again.
* **A 429 or a 5xx gets one retry,** honouring ``Retry-After`` up to 30 s.
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import ipaddress
import json
import logging
import os
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from . import __version__

logger = logging.getLogger("dpla_catalog_mcp.client")

#: Where the project lives; named in the User-Agent so DPLA can see who calls.
PROJECT_URL = "https://github.com/ianderso/dpla-catalog-mcp"

#: The DPLA API. Not configurable: the key must never be sent anywhere else.
API_BASE = "https://api.dp.la/v2"

#: The one host the API client may reach.
API_HOST = "api.dp.la"

#: Days a cached search or facet answer is served before DPLA is asked again.
SEARCH_CACHE_DAYS = 7

#: Days a cached record or IIIF manifest is served.
RECORD_CACHE_DAYS = 30

#: Longest wait honoured from a ``Retry-After`` header, in seconds.
RETRY_AFTER_CAP = 30.0

#: Least seconds between two requests to one institution's host.
IIIF_INTERVAL = 1.0

#: A manifest larger than this is not read. A 600-page book is about 1 MB.
MANIFEST_MAX_BYTES = 15_000_000

#: Redirects followed when fetching a manifest. Each hop is checked again.
MANIFEST_MAX_REDIRECTS = 5

_DAY = 86_400.0

#: Host suffixes that only make sense on a private network.
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")


class DplaApiError(RuntimeError):
    """A request DPLA refused, or one that could not be completed.

    ``status`` is the HTTP status, or 0 when no response arrived at all.
    ``code`` is DPLA's own error name (``invalid_api_key``, ``not_found``,
    ``bad_request``) when the body was DPLA's JSON error, and empty otherwise:
    a 404 with no code is a route that does not exist, not a missing record.
    """

    def __init__(self, status: int, detail: str, *, path: str, code: str = ""):
        """Record the failing request and what went wrong."""
        self.status = status
        self.detail = detail
        self.path = path
        self.code = code
        super().__init__(f"GET {path} -> {status or 'no response'}: {detail}")


class HostNotAllowed(RuntimeError):
    """Raised when a request is aimed at a host this client may not reach."""


class ManifestUnavailable(RuntimeError):
    """A IIIF manifest that could not be read from the institution's site.

    ``status`` is the HTTP status, or 0 when there was none (a timeout, a
    refused host, a body that is not a manifest).
    """

    def __init__(self, reason: str, *, url: str, status: int = 0):
        """Record the manifest address and why it could not be read."""
        self.reason = reason
        self.url = url
        self.status = status
        super().__init__(f"{url}: {reason}")


def user_agent() -> str:
    """The User-Agent sent with every request, naming this project."""
    return f"dpla-catalog-mcp/{__version__} (+{PROJECT_URL})"


def manifest_host_problem(url: object) -> str | None:
    """Why a manifest address may not be fetched, or None if it may.

    Only http and https, only a DNS name with a dot in it, never an IP
    address or a local name, and never DPLA's API host. The address comes
    from a DPLA record, which hundreds of institutions write; it is checked
    before the request and again on every redirect.
    """
    if not isinstance(url, str) or not url.strip():
        return "no address"
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return f"scheme {parts.scheme or 'none'!r} is not http or https"
    host = (parts.hostname or "").lower().rstrip(".")
    return _host_problem(host)


def _host_problem(host: str) -> str | None:
    if not host:
        return "no host"
    if host == API_HOST:
        return "it is DPLA's API host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return "it is an IP address, not an institution's site"
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES) or "." not in host:
        return "it is a local name"
    return None


class DplaClient:
    """Cached, paced async client for the DPLA API and IIIF manifests.

    Usable as an async context manager, which closes both transports on exit.

    Parameters
    ----------
    api_key : str
        The DPLA key. Sent only to :data:`API_HOST`, only in a header.
    cache_dir : Path
        Directory for cached responses. Created on first write.
    timeout : float, optional
        Per-request timeout in seconds.
    min_interval : float, optional
        Least time between the start of one API request and the next.
    retries : int, optional
        Further attempts after a 429, a 5xx or no response at all.
    backoff : float, optional
        Base of the wait between attempts when DPLA gives no ``Retry-After``.
    iiif_interval : float, optional
        Least time between two requests to one institution's host.
    transport : httpx.AsyncBaseTransport, optional
        For tests.
    """

    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        *,
        timeout: float = 30.0,
        min_interval: float = 0.25,
        retries: int = 1,
        backoff: float = 1.0,
        iiif_interval: float = IIIF_INTERVAL,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._secret = api_key
        self._cache_dir = cache_dir
        self._min_interval = min_interval
        self._retries = retries
        self._backoff = backoff
        self._iiif_interval = iiif_interval
        self._clock = clock
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": user_agent(),
                "Accept": "application/json",
                "Authorization": api_key,
            },
            event_hooks={"request": [self._only_the_api_host]},
            follow_redirects=False,
            transport=transport,
        )
        self._iiif = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": user_agent(),
                "Accept": "application/ld+json, application/json;q=0.9",
            },
            event_hooks={"request": [self._only_institution_hosts]},
            follow_redirects=True,
            max_redirects=MANIFEST_MAX_REDIRECTS,
            transport=transport,
        )
        self._lock = asyncio.Lock()
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._last_start: float | None = None
        self._live_calls = 0
        self._manifest_calls = 0
        self._cache_hits = 0
        self._shared_waits = 0
        self._last_error: dict | None = None

    # ------------------------------------------------------------------ #
    # Counters
    # ------------------------------------------------------------------ #
    @property
    def live_calls(self) -> int:
        """int: Requests sent to DPLA this session, retries included."""
        return self._live_calls

    @property
    def manifest_calls(self) -> int:
        """int: Manifest requests sent to institutions' sites this session."""
        return self._manifest_calls

    @property
    def cache_hits(self) -> int:
        """int: Answers served from the disk cache this session."""
        return self._cache_hits

    @property
    def shared_waits(self) -> int:
        """int: Calls answered by joining an identical request already in flight."""
        return self._shared_waits

    @property
    def last_error(self) -> dict | None:
        """dict or None: The most recent failure this session, key removed."""
        return self._last_error

    @property
    def cache_dir(self) -> Path:
        """Path: Where answers are cached."""
        return self._cache_dir

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def aclose(self) -> None:
        """Close both HTTP transports."""
        await self._http.aclose()
        await self._iiif.aclose()

    async def __aenter__(self) -> DplaClient:
        """Return the client."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the transports."""
        await self.aclose()

    async def _only_the_api_host(self, request: httpx.Request) -> None:
        """Refuse any key-bearing request that is not https to DPLA's API host."""
        if request.url.scheme != "https" or request.url.host != API_HOST:
            raise HostNotAllowed(
                f"refusing a request to {request.url.host!r}: the DPLA key is only "
                f"ever sent to {API_HOST!r}"
            )

    async def _only_institution_hosts(self, request: httpx.Request) -> None:
        """Check every manifest request and redirect hop; strip any credential."""
        for header in ("authorization", "cookie"):
            if header in request.headers:
                del request.headers[header]
        if request.url.scheme not in ("http", "https"):
            raise HostNotAllowed(f"refusing a {request.url.scheme!r} manifest address")
        if problem := _host_problem(request.url.host.lower().rstrip(".")):
            raise HostNotAllowed(f"refusing a manifest request to {request.url.host!r}: {problem}")

    # ------------------------------------------------------------------ #
    # The API
    # ------------------------------------------------------------------ #
    async def search(self, params: dict[str, Any], *, refresh: bool = False) -> Any:
        """Run one ``/items`` search, served from the cache when possible.

        Parameters
        ----------
        params : dict
            DPLA's own parameter names. ``None`` values are dropped.
        refresh : bool, optional
            Skip the cached copy and ask DPLA again.

        Returns
        -------
        Any
            The decoded body: ``{count, start, limit, docs, facets}``.

        Raises
        ------
        DplaApiError
            If DPLA refuses the request or does not answer. Nothing is cached.
        """
        return await self.get("/items", params, ttl_days=SEARCH_CACHE_DAYS, refresh=refresh)

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        ttl_days: float = SEARCH_CACHE_DAYS,
        refresh: bool = False,
    ) -> Any:
        """GET one API path, cached for ``ttl_days`` and shared while in flight."""
        clean = _clean(params)
        key = self._key("api", path, clean)
        if not refresh:
            cached = self._cache_get(key, ttl_days * _DAY)
            if cached is not None:
                self._cache_hits += 1
                return cached
        data = await self._shared(key, lambda: self._fetch(path, clean))
        self._cache_put(key, data)
        return data

    async def fetch_items(self, ids: Iterable[str], *, refresh: bool = False) -> dict[str, dict]:
        """Read records by DPLA id; return those found, keyed by id.

        Each record is cached on its own, so asking for ``[a, b]`` after
        ``[a]`` sends only ``b``. The ids not in the cache go to DPLA in one
        request, ``/items/a,b,c``. DPLA leaves out an id it does not hold
        (verified 2026-10-05: three real ids and one invented one gave
        ``count: 3``) and answers a lone unknown id with a 404; either way the
        id is simply absent from the result.

        Raises
        ------
        DplaApiError
            For anything but a missing record: a refused key, a route that no
            longer exists, an outage.
        """
        found: dict[str, dict] = {}
        wanted = list(dict.fromkeys(ids))
        missing = []
        for item_id in wanted:
            cached = (
                None
                if refresh
                else self._cache_get(self._item_key(item_id), RECORD_CACHE_DAYS * _DAY)
            )
            if isinstance(cached, dict):
                self._cache_hits += 1
                found[item_id] = cached
            else:
                missing.append(item_id)
        if missing:
            path = "/items/" + ",".join(missing)
            try:
                payload = await self._shared(
                    self._key("api", path, {}), lambda: self._fetch(path, {})
                )
            except DplaApiError as exc:
                if not (exc.status == 404 and exc.code == "not_found"):
                    raise
                payload = {}
            docs = payload.get("docs") if isinstance(payload, dict) else None
            for doc in docs if isinstance(docs, list) else []:
                if isinstance(doc, dict) and doc.get("id") in missing:
                    found[doc["id"]] = doc
                    self._cache_put(self._item_key(doc["id"]), doc)
        return {i: found[i] for i in wanted if i in found}

    async def _fetch(self, path: str, params: dict[str, str]) -> Any:
        """Send one GET, paced and retried; return the decoded body."""
        url = API_BASE + path
        attempt = 0
        while True:
            response: httpx.Response | None = None
            async with self._lock:
                await self._pace()
                self._last_start = self._clock()
                self._live_calls += 1
                try:
                    response = await self._http.get(url, params=params)
                except httpx.TransportError as exc:
                    error = DplaApiError(0, self._scrub(_transport_text(exc)), path=path)
            if response is not None:
                if response.status_code < 400:
                    return self._decode(response, path)
                error = self._api_error(response, path)
            retryable = error.status in (0, 429) or error.status >= 500
            if not retryable or attempt >= self._retries:
                self._note(error.status, error.code or "error", error.detail, path)
                raise error
            attempt += 1
            wait = _retry_after(response)
            if wait is None:
                wait = self._backoff * 2 ** (attempt - 1) + random.uniform(0, self._backoff / 2)
            wait = min(wait, RETRY_AFTER_CAP)
            logger.info(
                "%s -> %s; retry %d in %.1fs", path, error.status or "no response", attempt, wait
            )
            await self._sleep(wait)

    async def _pace(self) -> None:
        """Wait until at least ``min_interval`` has passed since the last request."""
        if self._last_start is None:
            return
        wait = self._min_interval - (self._clock() - self._last_start)
        if wait > 0:
            await self._sleep(wait)

    def _decode(self, response: httpx.Response, path: str) -> Any:
        try:
            return response.json()
        except ValueError:
            error = DplaApiError(502, "DPLA answered with something that is not JSON", path=path)
            self._note(502, "not_json", error.detail, path)
            raise error from None

    def _api_error(self, response: httpx.Response, path: str) -> DplaApiError:
        """DPLA's explanation of a refusal, with the key scrubbed from it."""
        code = ""
        try:
            body = response.json()
        except ValueError:
            detail = response.text.strip()[:300] or response.reason_phrase
        else:
            if isinstance(body, dict):
                code = str(body.get("error") or "")
                detail = str(body.get("message") or body.get("error") or response.reason_phrase)
            else:
                detail = response.reason_phrase
        return DplaApiError(response.status_code, self._scrub(detail), path=path, code=code)

    # ------------------------------------------------------------------ #
    # IIIF manifests on institutions' sites
    # ------------------------------------------------------------------ #
    async def manifest(self, url: str, *, refresh: bool = False) -> dict:
        """Fetch one IIIF manifest from the holding institution's site.

        Raises
        ------
        ManifestUnavailable
            If the address is not one this client may fetch, the site refuses
            or does not answer, or the body is not a JSON manifest. Never
            retried, never cached.
        """
        if problem := manifest_host_problem(url):
            raise self._unavailable(f"Not fetched: {problem}", str(url))
        url = url.strip()
        key = self._key("iiif", url, {})
        if not refresh:
            cached = self._cache_get(key, RECORD_CACHE_DAYS * _DAY)
            if isinstance(cached, dict):
                self._cache_hits += 1
                return cached
        data = await self._shared(key, lambda: self._fetch_manifest(url))
        self._cache_put(key, data)
        return data

    async def _fetch_manifest(self, url: str) -> dict:
        host = (urlsplit(url).hostname or "").lower()
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._host_last.get(host)
            if last is not None:
                wait = self._iiif_interval - (self._clock() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._host_last[host] = self._clock()
            self._manifest_calls += 1
            try:
                body, status, ctype = await self._read_capped(url)
            except HostNotAllowed as exc:
                raise self._unavailable(f"Not fetched: {exc}", url) from None
            except httpx.TooManyRedirects:
                raise self._unavailable("The site redirected too many times", url) from None
            except httpx.TransportError as exc:
                raise self._unavailable(
                    f"No answer from {host} ({_transport_text(exc)})", url
                ) from None
        if status >= 400:
            raise self._unavailable(f"The institution's site answered HTTP {status}", url, status)
        try:
            data = json.loads(body)
        except ValueError:
            kind = "a web page" if "html" in ctype or body.lstrip()[:1] == b"<" else "something"
            raise self._unavailable(
                f"The institution's site answered with {kind}, not a IIIF manifest "
                "(often a bot check)",
                url,
                status,
            ) from None
        if not isinstance(data, dict):
            raise self._unavailable("The answer is not a IIIF manifest", url, status)
        return data

    async def _read_capped(self, url: str) -> tuple[bytes, int, str]:
        """Stream a manifest, refusing to hold more than MANIFEST_MAX_BYTES."""
        async with self._iiif.stream("GET", url) as resp:
            ctype = resp.headers.get("content-type", "").lower()
            if resp.status_code >= 400:
                return b"", resp.status_code, ctype
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MANIFEST_MAX_BYTES:
                    raise self._unavailable(
                        f"The manifest is larger than {MANIFEST_MAX_BYTES // 1_000_000} MB", url
                    )
                chunks.append(chunk)
            return b"".join(chunks), resp.status_code, ctype

    def _unavailable(self, reason: str, url: str, status: int = 0) -> ManifestUnavailable:
        self._note(status, "manifest_unavailable", reason, url)
        return ManifestUnavailable(reason, url=url, status=status)

    # ------------------------------------------------------------------ #
    # Shared machinery
    # ------------------------------------------------------------------ #
    async def _shared(self, key: str, make: Callable[[], Awaitable[Any]]) -> Any:
        """Run ``make`` once for concurrent callers asking the same thing."""
        if (pending := self._inflight.get(key)) is not None:
            self._shared_waits += 1
            return await asyncio.shield(pending)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            data = await make()
        except asyncio.CancelledError:
            future.cancel()
            raise
        except Exception as exc:
            future.set_exception(exc)
            # Mark it retrieved: a failure nobody else was waiting on must
            # not be logged as "exception was never retrieved".
            future.exception()
            raise
        else:
            future.set_result(data)
            return data
        finally:
            self._inflight.pop(key, None)

    def _scrub(self, text: str) -> str:
        """Remove the key from any text that might be shown or logged."""
        return text.replace(self._secret, "[redacted]") if self._secret else text

    def _note(self, status: int, code: str, detail: str, where: str) -> None:
        self._last_error = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "status": status or None,
            "error": code,
            "detail": self._scrub(detail)[:300],
            "request": self._scrub(where)[:200],
        }

    # ------------------------------------------------------------------ #
    # Cache
    # ------------------------------------------------------------------ #
    @staticmethod
    def _key(kind: str, path: str, params: dict[str, str]) -> str:
        """The cache key: request kind, path and sorted parameters. Never the key."""
        query = urlencode(sorted(params.items()))
        return hashlib.sha256(f"{kind}\n{API_BASE}\n{path}?{query}".encode()).hexdigest()[:24]

    def _item_key(self, item_id: str) -> str:
        return self._key("api", f"/items/{item_id}", {})

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _cache_get(self, key: str, ttl: float) -> Any:
        """Return a cached answer, or None if absent, expired or unreadable."""
        path = self._cache_path(key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, ValueError):
            # A truncated or hand-edited entry is a re-fetch, not a crash.
            logger.warning("discarding unreadable cache entry %s", path.name)
            return None
        if not isinstance(entry, dict) or "data" not in entry:
            return None
        stored = entry.get("stored")
        if not isinstance(stored, int | float) or time.time() - stored > ttl:
            return None
        return entry["data"]

    def _cache_put(self, key: str, data: Any) -> None:
        try:
            _write_atomically(
                self._cache_path(key), json.dumps({"stored": time.time(), "data": data})
            )
        except OSError:
            # A cache that cannot be written costs speed, not correctness.
            logger.warning("could not write the response cache in %s", self._cache_dir)


def _clean(params: dict[str, Any] | None) -> dict[str, str]:
    """Drop ``None`` values and render the rest as DPLA expects them."""
    out: dict[str, str] = {}
    for name, value in (params or {}).items():
        if value is None:
            continue
        out[name] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return out


def _transport_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".rstrip(": ")


def _retry_after(response: httpx.Response | None) -> float | None:
    """Seconds a ``Retry-After`` header asks for, or None if absent or unreadable."""
    if response is None:
        return None
    raw = (response.headers.get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _write_atomically(path: Path, text: str) -> None:
    """Write a file so a reader never sees it half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
