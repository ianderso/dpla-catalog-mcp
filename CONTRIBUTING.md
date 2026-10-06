# Contributing

Issues and pull requests are welcome. This file says how the project is put
together and what a change is expected to carry.

## Setting up

```bash
git clone https://github.com/ianderso/dpla-catalog-mcp
cd dpla-catalog-mcp
uv sync --extra dev
```

Before sending a change, run what CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The suite is mocked with [respx](https://lundberg.github.io/respx/) against
recorded responses and needs no key. It must never touch the live API: CI has
no key, and should not depend on DPLA, or any institution's site, being up.

## Where things live

| Path | What it holds |
| --- | --- |
| `src/dpla_catalog_mcp/server.py` | The tools and the query builder. Their docstrings and `Field` descriptions *are* the published tool descriptions and schema. |
| `src/dpla_catalog_mcp/client.py` | The cached, paced HTTP clients: one for DPLA, which alone holds the key, and one for IIIF manifests on institutions' sites, which holds none. Host checks for both. |
| `src/dpla_catalog_mcp/shape.py` | Turning DPLA records and IIIF manifests into compact results: uneven fields, citation parts, identifiers, rights, duplicates, query escaping. |
| `src/dpla_catalog_mcp/config.py` | Settings from the environment and `.env`. |
| `docs/API-NOTES.md` | What the API and institutions' sites were observed to do, and when. |
| `docs/DESIGN.md` | Why the server is shaped the way it is, what is out of scope by decision, and later work. |
| `tests/fixtures/` | Recorded responses and the tool-schema snapshot. |
| `tests/test_tool_contract.py` | Tests over the tool surface as a client sees it. |
| `tests/live_check.py` | The one script that talks to the live API, run by hand with a key. Not collected. |

## What a change carries

**A test that fails without it.** Bug fixes especially: reproduce the bug as a
test first.

**Descriptions written for the model.** A tool's docstring is what a model
reads when choosing and calling it. The combined descriptions have a ceiling
(`DESCRIPTION_BUDGET` in `tests/test_tool_contract.py`), because they are sent
on every session. Raise it deliberately, in a pull request of its own.

**The evidence model, kept.** A DPLA record describes an item held elsewhere.
Tools that return descriptions or images say where the item is
(`is_shown_at`) and to cite the holding institution, not DPLA; `search_items`
says DPLA does not search inside books or pages. Contract tests enforce it.

**A structured result, never an exception.** Every tool catches its failures
and returns an `error` envelope. A sweep test calls every tool with DPLA
failing and fails if one raises.

**The key kept in its place.** It goes only to `api.dp.la`, only in the
`Authorization` header. Never put it in a URL, a log line, a cache key, an
error message, a fixture or a tool parameter. Tests check each of these.

**Nothing that writes, and nothing that works around a refusal.** See
[docs/DESIGN.md](docs/DESIGN.md#out-of-scope-by-decision).

**A new fixture recorded, not invented,** when a change depends on how DPLA or
an institution's site answers, with what was observed and when added to
`docs/API-NOTES.md`. Record with a key in the environment, never in a file;
the client sends it in a header, so a recorded body or URL does not carry it,
but check before committing. Keep fixtures to historical subjects, never
living people.

**The snapshot, when the surface changes.** Renaming or adding a tool or a
parameter fails the snapshot test on purpose. Regenerate it with
`uv run python -m tests.regen_tool_snapshot`, update the README tables, give
a new search parameter an alternative value in the cache-key sweep, and add a
`CHANGELOG.md` entry.

## Releasing

A maintainer bumps `__version__` in `src/dpla_catalog_mcp/__init__.py` and
both versions in `server.json`, moves the changelog's Unreleased entries under
the new version, and publishes a GitHub release tagged `v<version>`. The
release workflow builds the tag, publishes to PyPI by Trusted Publishing, and
lists the version in the MCP Registry.
