# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/). The tool surface is the public
interface: renaming or removing a tool or a parameter is a major release, and
adding one is a minor release. Before 1.0, a minor release may do either.

## [Unreleased]

## [0.1.1] — 2026-10-06

### Fixed

- The MCP Registry refused 0.1.0's listing: `server.json`'s description ran to 104
  characters and the registry takes at most 100. It is shortened, and a test now
  holds the limit. 0.1.0 reached PyPI unchanged; no code differs.

## [0.1.0] — 2026-10-05

First release.

### Added

- Six read-only tools over the Digital Public Library of America:
  `search_items`, `search_items_advanced`, `facet_items`, `get_item`,
  `get_item_images` and `api_status`.
- Hits carry the holding institution, `is_shown_at` (the item at its holder),
  the rights statement with a short label, `has_iiif`, and
  `possible_duplicate_of` for copies of one work held in several places.
- `get_item` returns `citation_parts` for the holding institution's record,
  with the institution's own identifiers read from the harvested record.
- `get_item_images` lists page images from the holder's IIIF manifest
  (Presentation 2 and 3), and reports a refusal with `open_in_browser`.
- Local checks for what DPLA would refuse or silently mishandle: page 101
  (DPLA answers it with page 100 again), colons and slashes in queries, bare
  rights URIs, exact matching on fields that have no exact form.
- The key is sent only to `api.dp.la`, only in a header. Manifests are fetched
  by a separate client that holds no key and refuses private addresses.
- One request at a time, identical calls joined, a disk cache of 7 days for
  searches and 30 for records and manifests, one retry on 429 or 5xx.
