# DPLA API notes

What the DPLA API actually does, observed against the live service. The
deployed code is [dpla/api](https://github.com/dpla/api) (Scala, akka-http),
not `dpla/dpla-api` (TypeScript), which answers differently and is not what
runs. DPLA's developer pages (`pro.dp.la`) sit behind a bot check; the field
definitions are in `src/main/scala/dpla/api/v2/search/models/DPLAMAPFields.scala`
and the parameter rules in `.../paramValidators/ParamValidator.scala`.

Observed 2026-10-05, about 45 calls to DPLA and 8 to institutions' sites,
paced at least a second apart, with a key. Counts are as of that date.

## Transport and the key

- Base `https://api.dp.la/v2`. Search is `GET /items`; a record is
  `GET /items/{id}`, several are `GET /items/{id},{id},...`.
- The key works in an `Authorization: <key>` header (no `Bearer`). It also
  works as `?api_key=`; this server never uses that, so the key is never in a
  URL.
- No key, a wrong key and a disabled key all get the same answer, before the
  query is looked at: `403 {"error":"invalid_api_key","message":"Invalid or
  inactive API key.", ...}`.
- A key is 32 letters, digits or hyphens (`AuthParamValidator.scala`).

## Searching

| Observation | Detail |
| --- | --- |
| Response | `{count, start, limit, docs, facets}`. `count` is exact. `start` is the 1-based position of the first hit, `(page-1)*page_size+1`; `limit` is the page size. |
| `facets` | An **empty list** when none were asked for, an object keyed by field when they were. Terms facets are `{"_type":"terms","terms":[{"term","count"}]}`; year facets `{"_type":"date_histogram","entries":[{"time","count"}]}`, newest first. |
| `page_size=0` | Counts and facets with no records. |
| `fields=` | Returns **flattened, dotted keys**, and a one-element list becomes a scalar: `"sourceResource.title": "Atlas of ..."`, but `"sourceResource.date.displayDate": ["ca 1730-1735", "1730-1735"]` when there are two. |
| `page` over 100 | **Silently clamped.** `page=150&page_size=2` returned `start: 199` and the same ids as `page=100`. |
| Depth | `page=100&page_size=500` returned 500 records from `start: 49501`; `page=21&page_size=500` also answered. No 10,000-hit window binds. |
| Text length | 2-200 characters, for `q` and every field. `q=a` and a 201-character `q` both gave `400 "q must be between 2 and 200 characters"`. |
| Unknown parameter | `400 "Unrecognized parameter: sourceResource.subject"` (the docs' old name; it is `sourceResource.subject.name` now). |
| A colon in `q` | **Silently matches nothing.** `atlas: Champaign` gave 0; `atlas\: Champaign` and `atlas Champaign` gave 23. Lucene reads `atlas:` as a field name. |
| A slash in `q` | `400 "The q parameter contains invalid search syntax."` (`1913/1929` opens a regular expression). |
| Negation | Works inside `q`. `q=atlas`: 145,017. `provider.name=National Archives and Records Administration&q=atlas`: 3,718. `q=atlas NOT provider.name:"National Archives and Records Administration"`: 141,299, exactly the difference. A `q` that is only the `NOT` clause works beside a field query. |
| Exact match | `exact_field_match=true` is case-sensitive: `dataProvider.name=University of Illinois Urbana-Champaign Library` gave 203, the same in lower case 0. |
| Exact on creator | **Silently 0.** `sourceResource.creator=Brock & Company` gave 97; with `exact_field_match=true`, 0. The field has no exact (not-analysed) form, so DPLA runs a term query on analysed text. The same holds for `sourceResource.identifier`. `sourceResource.spatial.name` does have one (37 for `Champaign County (Ill.)`). |
| Exact splitting | Source: an exact value is split on the substrings `AND` and `OR` (case-sensitive) before matching, so an all-capitals value containing them would be split. |
| `rights` | Validated as a URL. A bare URI matched **0**, the same URI in double quotes 6, and an escaped one was refused: `400 "rights must be a valid URL"`. |
| Item type | `filter=sourceResource.type:text` is an exact, non-scoring filter (3 hits for `atlas Champaign`). A word search on type would let `image` match `moving image`. |
| Sort | `sort_by=sourceResource.date.begin&sort_order=asc` works. With no `q` and no field, DPLA sorts by storage order (`_doc`), which means nothing. |

### Vocabularies (whole of DPLA, 53,488,146 records)

- `sourceResource.type`: text 26.9 M, image 14.4 M, moving image 420 K, sound
  235 K, physical object 133 K, dataset 18.9 K, interactive resource 9.2 K,
  collection 2.3 K, event 309.
- `rights`: NoC-US 23.1 M, CC0 5.1 M, CNE 4.7 M, InC 4.1 M, Public Domain
  Mark 2.7 M, UND 1.1 M, InC-EDU 476 K, NKC 264 K, then Creative Commons
  licences.
- **Geocoded place subfields are sparse.** `sourceResource.spatial.county`:
  Suffolk 280,650, Essex 85,929, Worcester 78,179, Middlesex 67,009, "[Not
  Stated]" 65,532. `sourceResource.spatial.state`: Massachusetts 764,775,
  California 261,112, Alaska 145,895. `q=atlas&sourceResource.spatial.state=Illinois`
  gave 12 hits (Smithsonian and Digital Commonwealth), and none of the
  Illinois county atlases.
- `tags=iiif` marks records with a IIIF manifest: 7,613,843, led by the Portal
  to Texas History (2.43 M), OKHub, the Internet Archive, Digital Commonwealth,
  the Michigan Service Hub, Heartland Hub and the Illinois Digital Heritage Hub.

## Reading records

| Observation | Detail |
| --- | --- |
| One id | `{count: 1, docs: [...]}`. An unknown id: `404 {"error":"not_found", ...}`. |
| Several ids | Sorted by id; **unknown ids are left out**, with a 200. Three real ids and one invented gave `count: 3`. |
| How many | 51 ids answered (321 KB). The source allows 500. This server asks for at most 20. |
| Parameters | None: `fields=` on a fetch is `400 "Unrecognized parameter: fields"`. |
| `originalRecord` | `{"stringValue": "<record>..."}`: the OAI-PMH record the hub harvested, as XML. Seen as qualified Dublin Core (Illinois), MARCXML (HathiTrust, with `035` OCLC numbers), UNTL (Portal to Texas History) and MODS (Dartmouth, with `recordIdentifier`). Returned on every search hit unless `fields=` is sent. |
| `dataProvider` | An object `{name, @id, exactMatch: [wikidata]}`. |
| Places | The University of Illinois's county atlases carry **no `spatial` at all**. HathiTrust's copies carry place names only (`Champaign County (Ill.)`). The Portal's are one string, `United States - Texas - Galveston County - Galveston`. |
| Rights | The Portal to Texas History's records carry **no `rights` URI**, only a free-text `sourceResource.rights`. |
| Enrichment slips | `language: [{"iso639_3": "English", "name": "English"}]` on the Illinois atlas. |

The removed collections route: `GET /v2/collections` gives `404 text/plain
"The requested resource could not be found."`, with no JSON error body. That
shape, not a JSON `not_found`, means a route has gone.

## IIIF manifests on institutions' sites

Fetched with no key, one per host, a second apart.

| Host | Answer |
| --- | --- |
| `texashistory.unt.edu` (Portal to Texas History) | 200, Presentation 2. The 1870 Galveston directory: 152 canvases, 185 KB, with `attribution` and `license`. |
| `collections.dartmouth.edu` | 200, Presentation 3, with `requiredStatement` and `rights`. |
| `api.mohistory.org` (Missouri Historical Society) | 301 to `images.mohistory.org`, another host, then 200, Presentation 2. |
| `cdm17228.contentdm.oclc.org`, `digital-collections.columbuslibrary.org` (CONTENTdm) | Plain `http`, a 301 then a 302, then 200, Presentation 2. |
| `digital.library.illinois.edu` | **403 `text/html`**, 118 bytes, to a scripted request. |
| `ark.digitalcommonwealth.org` | 404 JSON `"No Route Matches This Url"` for a manifest address taken from a live record: stale. |
