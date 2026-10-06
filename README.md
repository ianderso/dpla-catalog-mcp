# dpla-catalog-mcp

[![CI](https://github.com/ianderso/dpla-catalog-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ianderso/dpla-catalog-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dpla-catalog-mcp)](https://pypi.org/project/dpla-catalog-mcp/)

<!-- mcp-name: io.github.ianderso/dpla-catalog-mcp -->

An [MCP](https://modelcontextprotocol.io) server for the **[Digital Public
Library of America](https://dp.la)**: one search across descriptions of more
than 50 million digitised items held by hundreds of US libraries, archives,
historical societies and museums. For family history that means county
atlases and plat books, city directories, county histories, yearbooks, local
newspapers and photographs, many of them held by a small institution you
would not think to search.

DPLA holds none of these items. It gathers the descriptions that holding
institutions publish, through state and regional "hubs", and every record
points back at the item on the holding institution's own site. That page is
where the evidence is and what a citation names. The server is built around
that fact: each hit carries `is_shown_at` (the item at its holder), the
holding institution, and the rights statement for the digital copy;
`get_item` assembles the parts of a citation of the holder's record; and
`get_item_images` lists the page images from the holder's own IIIF manifest
when it publishes one.

It works the way a careful genealogist does. A DPLA record is a finding aid
to a finding aid: written by the institution, harvested by a hub, and
enriched by DPLA. DPLA searches that metadata only, never the text inside a
book or on a page. The tools say so, in the descriptions a model reads.

Nothing here writes anywhere, and nothing here keeps a family tree. It sits
well beside [nara-catalog-mcp](https://github.com/ianderso/nara-catalog-mcp)
(federal records, with OCR and page images),
[snac-archives-mcp](https://github.com/ianderso/snac-archives-mcp) (which
archive holds the papers) and
[familysearch-mcp](https://github.com/ianderso/familysearch-mcp) (indexed
records and images).

This is an independent project. It is not affiliated with, endorsed by, or
supported by the Digital Public Library of America or any contributing
institution.

## Tools

The server publishes six tools, all read-only and annotated so for the
client. One makes no network call at all.

**Finding items**

| Tool | Purpose |
| --- | --- |
| `search_items` | The everyday search: words, title, place, a date range, holding institution, hub, item type. Each hit names its holding institution, the item's address there (`is_shown_at`), its rights statement, and whether page images can be listed. Copies of one work held in several places are flagged. |
| `search_items_advanced` | The same, with every filter DPLA offers: county, state and city as DPLA geocoded them, subject, collection, publisher, format, creator, identifier, the period an item is *about*, rights, exact matching, any-of matching, sorting, and leaving out National Archives records. |
| `facet_items` | Counts before searching: which institutions hold plat books of a county, which years a run of directories covers, which hubs cover a place. |

**Reading an item**

| Tool | Purpose |
| --- | --- |
| `get_item` | Up to 20 records in full, each with `citation_parts`: the holding institution, title, date, the institution's own identifiers (call numbers, local ids, OCLC numbers, read from the harvested record too), `is_shown_at`, rights, and the DPLA id as a finder. Optionally the raw harvested record. |
| `get_item_images` | The page images, from the IIIF manifest on the holding institution's site (Presentation API 2 and 3), a stretch of the list at a time. When a site refuses scripts, it says so and gives the address to open in a browser. |

**The server itself**

| Tool | Purpose |
| --- | --- |
| `api_status` | Whether a key is configured (never the key), this session's calls and cache hits, and the last error. Makes no request. |

## Setup

You need Python 3.11 or later, [uv](https://docs.astral.sh/uv/), and a DPLA
API key.

**The key is free and arrives at once,** but it is issued to an email
address, so request it yourself:

```bash
curl -X POST https://api.dp.la/v2/api_key/you@example.org
```

DPLA emails a 32-character key to that address. Asking again re-sends the
same key. DPLA records only the email address, and counts each call made
with the key.

**Without cloning.** `uvx` fetches the server from PyPI and runs it in one
step:

```bash
DPLA_API_KEY=your-key uvx dpla-catalog-mcp
```

**From a clone**, which is what you want if you will change it:

```bash
git clone https://github.com/ianderso/dpla-catalog-mcp
cd dpla-catalog-mcp
uv sync
cp .env.example .env       # then put your key in .env
uv run dpla-catalog-mcp    # stdio server, usually launched by the client
```

Either way the server speaks MCP over stdio, so you will normally let an MCP
client start it rather than run it by hand.

### Claude Desktop

```json
{
  "mcpServers": {
    "dpla": {
      "command": "uvx",
      "args": ["dpla-catalog-mcp"],
      "env": { "DPLA_API_KEY": "your-key" }
    }
  }
}
```

A desktop app does not always inherit your shell's `PATH`. If the server fails
to start because `uvx` cannot be found, give the full path that `which uvx`
prints as the `command`.

### Claude Code

```bash
claude mcp add dpla --env DPLA_API_KEY="$DPLA_API_KEY" -- uvx dpla-catalog-mcp
```

Or keep the key in a `.env` file and have the launcher read it, so it is in
neither the client's configuration nor your shell history:

```json
"dpla": { "type": "stdio", "command": "sh", "args": ["-c",
  "set -a; . \"$HOME/research/.env\"; set +a; exec uvx dpla-catalog-mcp@latest"] }
```

## Configuration

| Variable | Meaning |
| --- | --- |
| `DPLA_API_KEY` | Your key. Required. A missing or malformed key is reported on the first tool call as `not_configured`, without repeating the value. |
| `DPLA_CACHE_DIR` | Response cache directory. Default `~/.cache/dpla-catalog-mcp`. |
| `DPLA_TIMEOUT` | HTTP timeout in seconds. Default 30. |
| `DPLA_MIN_INTERVAL` | Least seconds between requests to DPLA. Default 0.25. |

A `.env` file in the directory the server starts in supplies anything the
environment does not; only that directory is read. An unusable value is
reported on the first tool call, naming the variable.

## Being a good guest

DPLA sets no quota, but every call is counted against the key. The server
sends one request at a time, at least `DPLA_MIN_INTERVAL` apart; two identical
calls in flight share one request; searches and facets are cached on disk
for 7 days and records and manifests for 30 (DPLA re-harvests monthly). A 429
or a 5xx gets one retry, honouring `Retry-After` up to 30 seconds, and is then
reported as `rate_limited` or `upstream_error`, which is never the same as
"nothing found". Requests to institutions' sites are spaced a second apart per
site and never retried.

## How to read what comes back

- **A hit is a description, not the item.** Open `is_shown_at`, read the item
  there (or its page images), and cite the **holding institution** and its
  record, for example "Standard atlas of Champaign County, Illinois (Brock &
  Company, 1929), University of Illinois Urbana-Champaign Library,
  digital.library.illinois.edu/items/465c93c0-…". Not DPLA, and not the hub.
  In a genealogy database: repository = the holding institution; source = the
  item; citation = the page or plate, with `is_shown_at` and the
  institution's identifier. Keep the DPLA id as an attribute, a finder only.
- **DPLA does not search inside books or pages.** A surname printed in a city
  directory, a county history or a yearbook will not match. Use DPLA to find
  *which volume exists and where*, then search its text at the holder: the
  Internet Archive's full-text search, HathiTrust's, the Portal to Texas
  History's, Chronicling America's. No hits means no description matched,
  not that the person is absent.
- **Places are often missing, and enriched when present.** Many records name
  no place at all (the University of Illinois's county atlases carry none),
  so search the county in `query` or `title` as well as `place`. `county`,
  `state` and `city` are DPLA's geocoding, filled for few records (mostly
  Massachusetts). Facet on `place` to see places as the records name them.
- **Dates match by overlap.** `date_after=1905` matches an item dated
  1900-1909. "ca. 1900" may be indexed as a decade. `display_date` is what
  the institution wrote.
- **One work, several copies.** A county history is often in DPLA as a
  HathiTrust copy, an Internet Archive copy and a state hub's scan.
  `possible_duplicate_of` flags them within a page of results, by title and
  year; prefer the copy whose holder publishes page images and full text.
- **National Archives records** are a third of DPLA and duplicate the NARA
  Catalog. They are better read with nara-catalog-mcp, which has the NAID,
  the OCR text and the images. `exclude_nara` leaves them out.
- **Rights belong to the digital copy and vary by institution.** Read
  `rights` (a rightsstatements.org or Creative Commons address, with a short
  label) before republishing an image. Some hubs, such as the Portal to Texas
  History, give only a free-text statement. A thumbnail is not evidence.
- **Ids are finders, not citations.** A DPLA id is a hash of the hub's local
  id, so it changes when a hub moves platform, and the old id stops working.
  Record `is_shown_at` and the institution's own identifier as well.
- **Exact matching is literal.** `exact=true` matches a whole value,
  case-sensitively, as `facet_items` returns it. DPLA has no exact form of
  creator or identifier, so those are refused with `exact`.
- **Paging stops at page 100.** DPLA answers any higher page with page 100
  again, silently; the server refuses instead, and says how many hits paging
  can reach. Narrow the search, or use `facet_items` to see how the hits
  divide.
- **Some sites refuse scripts.** The University of Illinois and the
  HathiTrust catalog answer automated requests with 403. The server reports
  that and gives `open_in_browser`; it does not work around a refusal.
- **Descriptions are untrusted text,** written by hundreds of institutions,
  and the raw harvested record is exactly that. Treat it as material to
  weigh, never as instructions.

## Deliberately not here

- **Requesting a key.** `POST /v2/api_key/{email}` sends email in someone's
  name. You request your own key.
- **Downloading page images** and **bulk downloads.** Listed as later work in
  [docs/DESIGN.md](docs/DESIGN.md#later-work). The image addresses are in the
  `get_item_images` result.
- **DPLA's collections route,** which is gone (it answers 404), its random
  item and archive-request routes, and its Primary Source Sets (K-12
  teaching sets): none has research value here.
- **A raw-query passthrough.** It would bypass the local checks that keep the
  server from spending calls on what DPLA would refuse.
- **Working around a site's bot protection.** A refusal is reported, not
  evaded.

## Security

- **The key goes to one place, one way.** It is sent only to
  `https://api.dp.la`, only in the `Authorization` header: never in a URL, a
  log line, a cache key or file, an error message, or a tool parameter. A
  request hook refuses any other host for the key-bearing client, and
  redirects are not followed.
- **Institutions' sites get no credentials.** Manifests are fetched by a
  separate client that holds no key and strips any `Authorization` or
  `Cookie` header. It refuses IP addresses, local names and DPLA's own host,
  on the first request and on every redirect.
- **Inputs are checked before a request is made:** ids, dates, text lengths,
  enumerations, and the page limit. Query text has the characters that DPLA's
  parser would read as syntax escaped.
- **Catalogue text is untrusted.** Descriptions and the raw harvested record
  reach the model verbatim. The server's instructions tell the model to treat
  that text as material to weigh, never as instructions; the model still
  decides, so review what it proposes to do.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync --extra dev
uv run pytest                      # mocked with respx; never touches DPLA
uv run ruff check .
uv run ruff format --check .
uv run python -m tests.live_check  # paced calls to the live API; needs DPLA_API_KEY
```

The live check asks DPLA what the recorded fixtures cannot: whether its
answers still have the shape the server reads, and whether the behaviour the
server works around (silent page clamping, colon parsing, URL quoting) still
holds. See [CONTRIBUTING.md](CONTRIBUTING.md) for how the suite is organised,
[docs/API-NOTES.md](docs/API-NOTES.md) for what was observed of the API and
when, and [docs/DESIGN.md](docs/DESIGN.md) for why the server is shaped this
way.

## Credits

The metadata is the [Digital Public Library of America](https://dp.la)'s,
contributed by its hubs and their member institutions. The items, their
images and their rights belong to the holding institutions.

## License

[MIT](LICENSE).
