<!-- What this changes and why. Link the issue it closes, if there is one. -->

## Checklist

What each item means is in [CONTRIBUTING.md](https://github.com/ianderso/dpla-catalog-mcp/blob/main/CONTRIBUTING.md#what-a-change-carries).

- [ ] A test that fails without this change.
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` pass.
- [ ] Tool descriptions are written for the model and fit under `DESCRIPTION_BUDGET`. Raising it takes a pull request of its own, saying why.
- [ ] Anything returning an item description or image says where the item is (`is_shown_at`) and to cite the holding institution, not DPLA.
- [ ] Failures come back as an `error` result, not an exception.
- [ ] The key goes only to `api.dp.la`, only in a header: never in a URL, a log line, a cache key, an error, a fixture or a tool parameter.
- [ ] If a tool or parameter changed: `tests/fixtures/tool_schema.json` is regenerated and the README tables are updated.
- [ ] If this relies on how DPLA or an institution's site answers: a recorded fixture, and what was observed and when in `docs/API-NOTES.md`.
- [ ] A `CHANGELOG.md` entry, if users will notice the change.
- [ ] Nothing here writes anywhere or works around a site's refusal of scripts.
