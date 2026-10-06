# Design

Why the server is shaped the way it is, and what is out of scope by decision.

## The question it answers

"Does a digitised copy of this exist, and who holds it?" A county atlas with
landowners' names, a run of city directories, a county history, a church
anniversary booklet, a photograph of a main street: these are scanned by
hundreds of local institutions, each on its own site. DPLA gathers their
descriptions into one search. This server makes that search usable by a
model without losing the fact that DPLA is an index, not a repository.

## The evidence model

DPLA holds no items. A record is a finding aid to a finding aid: written by
the holding institution, harvested by a hub, enriched by DPLA (geocoding,
date parsing, type mapping). The descriptions and the server instructions say
so in the words a model acts on, and contract tests fail if they stop saying
it:

- Every hit carries `is_shown_at`, the item on the holder's site, and the
  holding institution. The search and read tools all say to cite the holding
  institution, not DPLA.
- `get_item` returns `citation_parts`: repository = the holding institution;
  source = the item; the institution's own identifiers (read from the
  harvested record when DPLA's mapping dropped them); the address; the
  rights; the DPLA id as a finder only, because it is a hash of the hub's
  local id and changes when a hub re-platforms.
- `search_items` says DPLA does not search inside books or pages, so no match
  does not mean a name is absent.
- `get_item_images` reads the holder's own IIIF manifest: those images are
  the evidence, and they carry the holder's rights.

## The tool surface

Six tools, as the spec recommends, with these choices made while building:

- **`search_items` and `search_items_advanced`** share one query builder. The
  simple tool keeps its schema small; the advanced one carries every filter.
- **`facet_items`** adds `place` (`sourceResource.spatial.name`) to the
  spec's list, because DPLA's geocoded `county`, `state` and `city` turned out
  to cover few records (mostly Massachusetts; see API-NOTES). Year facets
  return every year, oldest first, since DPLA's newest-first order cut to a
  size would drop the years a genealogist wants.
- **`get_item`** reads at most 20 records, not the spec's 50: a shaped full
  record runs to about 2 KB, and 50 would make a 100 KB answer.
- **`get_item_images`** pages the image list (`first`, `count`), because a
  directory or atlas manifest lists hundreds of canvases.
- **`api_status`** answers even when the key is missing, so the model can say
  what is wrong.
- `refresh` is on the two read tools only. Searches are cached for a week.

## Building a query

- **Local checks first.** Text 2-200 characters, dates `YYYY[-MM[-DD]]`, ids
  up to 32 letters, digits or hyphens, enumerations for type, facet and sort,
  and the page limit. Nothing DPLA would refuse is sent.
- **Paging stops at 100.** DPLA answers page 101 with page 100 again, silently;
  a model would read that as new results. The server refuses with
  `paging_limit` and reports `reachable_max` (`min(total, 100 x limit)`).
- **Escaping.** DPLA runs `q` and every field through Lucene's query parser.
  An unescaped colon silently matches nothing and a slash is a 400, so
  `: / [ ] { } ^ ~ !` are escaped. Quotes, `*`, parentheses, `AND`, `OR` and
  `NOT` are left alone: they are the syntax people mean to use.
- **`rights` is quoted, not escaped:** DPLA validates it as a URL, and a bare
  URI silently matches nothing.
- **`exact`** maps each field to its not-analysed form (`place` becomes
  `sourceResource.spatial.name`). `creator` and `identifier` have none, and an
  exact match on them silently returns nothing, so the combination is refused.
- **`type`** is sent as `filter=sourceResource.type:<value>`, an exact term,
  so `image` does not match `moving image`.
- **`exclude_nara`** appends `NOT provider.name:"..."` to `q`, which DPLA
  honours (verified by counts). It is refused with `match_any`, where it would
  not exclude, and when the query is too long to carry it.
- **`match_any`** with dates is refused: DPLA would OR the date ranges with
  everything else.
- With no words and no sort, results come in storage order; the result says
  so (`order_note`).

## The key

The key is read from `DPLA_API_KEY` (or `.env` in the working directory) on
the first tool call, never echoed in an error and excluded from the config's
`repr`. It is sent only to `https://api.dp.la`, only in the `Authorization`
header: never in a URL, so never in a log line, a cache key or an error. The
API base is not configurable, so no setting can send it elsewhere; a request
hook refuses any other host, and redirects are not followed. No tool takes a
key, and the `POST /v2/api_key/{email}` route is absent, since it sends email
in someone's name.

## Institutions' sites

Manifests are fetched by a second client that holds no key and strips any
`Authorization` or `Cookie` header. The address comes from a DPLA record,
which hundreds of institutions write, so it is checked before the request and
on every redirect: http or https only, a DNS name with a dot, never an IP
address, a local name or DPLA's API host. Redirects are followed (Missouri
Historical Society redirects to another of its hosts; CONTENTdm redirects
twice). Requests to one host are a second apart. A 403, a bot check that
answers HTML, a timeout or a manifest over 15 MB is reported with
`open_in_browser` and never retried or cached. Nothing works around a refusal.

## Courtesy and caching

DPLA has no quota and no rate-limit code path, but every call counts against
the key, and CloudFront fronts the API. One request at a time, at least
`DPLA_MIN_INTERVAL` (0.25 s) apart; identical concurrent calls share one
request; a 429 or 5xx gets one retry honouring `Retry-After` up to 30 s.

The disk cache is keyed by request kind, path and sorted parameters, never
the key. Searches and facets keep 7 days; records and manifests 30, because
DPLA is re-harvested monthly and nothing is safe to keep forever. Records are
cached one per id, so a later multi-id read sends only the ids not already
held. Failures are never cached; an unreadable entry is fetched again.

## Shape of the results

- **Compact.** Search hits leave out empty fields, except `is_shown_at`,
  `institution` and `rights`, whose absence is itself worth knowing. Titles
  are cut at 200 characters in hits. `originalRecord` is never requested on a
  search (`fields=` is always sent) and is returned by `get_item` only on
  request, cut at 4,000 characters.
- **Either cardinality.** Every field is read as string or list, nested or
  flattened (`fields=` flattens), and `dataProvider` as object or legacy
  string.
- **Duplicates flagged, not merged,** within a page of hits: title up to its
  first comma or colon (three words at least) plus the first year.
- **Rights with a label:** rightsstatements.org and Creative Commons URIs get
  their short names.

## Out of scope, by decision

- **Requesting a key** (`POST /v2/api_key/{email}`): it sends email.
- **`/v2/collections`** (gone), **`/v2/random`**, **`/v2/smr`** and the
  **Primary Source Sets** (`/v2/pss/*`, K-12 teaching sets): no research value.
- **A raw-query passthrough:** it would bypass the local checks.
- **Evading bot protection** on any institution's site.

## Later work

- **`download_page_image`** (the spec's phase 2), only once `get_item_images`
  proves useful. It would copy nara-catalog-mcp's rules exactly: create a new
  file only and never overwrite one, name the format from the bytes, stream
  the body, and be annotated neither read-only nor destructive. The image
  address is already in `get_item_images`, so this is a convenience.
- **Bulk data.** DPLA publishes monthly exports to
  `s3://dpla-provider-export` (`YYYY/MM/{hub}.jsonl`, about 32 GB for all of
  it). A `dpla-catalog-mcp index --hub il,mi,ohio` command could load chosen
  hubs into a local SQLite FTS5 file, with an `index_search` tool over it, for
  questions that outrun the API: regular expressions, name-variant sweeps,
  joins against a tree's places. It would still be metadata only.
- **Distance search:** `sort_by=sourceResource.spatial.coordinates` with
  `sort_by_pin`, and the coordinates facet's 100-mile bands. Useful only where
  records are geocoded, which is the minority.
- **Primary Source Sets,** if a use appears.

## Tests

- Everything is mocked with respx against **recorded** responses
  (`tests/fixtures/`): DPLA searches and records made by the server's own
  requests, two real IIIF manifests (v2 and v3), the 403 page a refusing
  site serves, and DPLA's error bodies. Historical subjects only.
- Contract tests pin the surface as a client sees it: names and parameters
  (snapshot), descriptions present, within budget and carrying the evidence
  model, read-only annotations, no tool that raises when DPLA or a site
  fails, unknown parameters refused, no parameter that takes a key, and a
  sweep, derived from the schemas, showing that every search parameter
  changes the request and so the cache key.
- Key hygiene is tested where it could leak: the request URL, cache files and
  their names, error text, `api_status`, the config's `repr`, the schemas,
  and the headers sent to an institution's site.
- `tests/live_check.py` runs by hand with a key: eighteen checks, about
  twenty paced calls, including DPLA's field definitions read from its source
  so that drift fails loudly.
