"""Turning DPLA records and IIIF manifests into compact, citable results.

DPLA records are uneven. Hundreds of institutions write them, hubs harvest
them, and DPLA enriches them, and the same field is a string in one
contributor's records and a list in another's. A search with ``fields=``
returns flattened, dotted keys (``"sourceResource.title": "..."``) and turns a
one-element list into a scalar; a fetch returns the nested record. These
functions read either form, keep what a researcher acts on -- what the item
is, who holds it, where it is, what may be done with the copy -- and drop the
rest.

Every function here accepts a malformed record without raising. A field that
is missing or the wrong type comes back empty.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from typing import Any

#: The public page for a DPLA record. A finder, not a citation.
DPLA_ITEM_PAGE = "https://dp.la/item/{}"

#: DPLA's name for the National Archives as a hub.
NARA_HUB = "National Archives and Records Administration"

#: Fields requested on every search, so ``originalRecord`` is never pulled.
SUMMARY_FIELDS = (
    "id",
    "sourceResource.title",
    "sourceResource.date.displayDate",
    "sourceResource.creator",
    "sourceResource.spatial",
    "sourceResource.type",
    "sourceResource.format",
    "dataProvider",
    "provider",
    "intermediateProvider",
    "isShownAt",
    "object",
    "iiifManifest",
    "rights",
    "sourceResource.rights",
)

#: Characters of the institution's free-text rights statement in a search hit.
RIGHTS_NOTE_LIMIT = 200

#: Characters of a title in a search hit. ``get_item`` gives the whole title.
TITLE_LIMIT = 200

#: Characters of a description kept by ``get_item``.
DESCRIPTION_LIMIT = 2_000

#: Characters of the raw harvested record returned on request.
ORIGINAL_RECORD_LIMIT = 4_000

#: Institution identifiers kept per record.
IDENTIFIER_LIMIT = 12

#: rightsstatements.org codes, as the statements name themselves.
RIGHTS_STATEMENTS = {
    "InC": "In Copyright",
    "InC-OW-EU": "In Copyright - EU Orphan Work",
    "InC-EDU": "In Copyright - Educational Use Permitted",
    "InC-NC": "In Copyright - Non-Commercial Use Permitted",
    "InC-RUU": "In Copyright - Rights-holder(s) Unlocatable or Unidentifiable",
    "NoC-CR": "No Copyright - Contractual Restrictions",
    "NoC-NC": "No Copyright - Non-Commercial Use Only",
    "NoC-OKLR": "No Copyright - Other Known Legal Restrictions",
    "NoC-US": "No Copyright - United States",
    "CNE": "Copyright Not Evaluated",
    "UND": "Copyright Undetermined",
    "NKC": "No Known Copyright",
}

_DPLA_ID = re.compile(r"[A-Za-z0-9-]{1,32}")
_DPLA_URL = re.compile(r"dp\.la/(?:item|api/items)/([^/?#\s]+)", re.IGNORECASE)
_HEX32 = re.compile(r"[0-9a-fA-F]{32}")
_DATE = re.compile(r"\d{4}(?:-\d{2}(?:-\d{2})?)?")
_YEAR = re.compile(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)")
_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
#: Query-string characters that DPLA's Lucene parser reads as syntax. Verified
#: 2026-10-05: an unescaped ":" silently matched nothing ("atlas: Champaign"
#: gave 0, escaped gave 23), and a "/" was a 400. Quotes, *, ?, parentheses,
#: AND, OR and NOT are left alone: they are the syntax people mean to use.
_LUCENE_LITERAL = re.compile(r"(?<!\\)([:/\[\]{}^~!])")
_RIGHTS_URI = re.compile(r"rightsstatements\.org/(?:vocab|page)/([A-Za-z-]+)/", re.IGNORECASE)
_CC_URI = re.compile(
    r"creativecommons\.org/(?:(licenses)/([a-z-]+)/([\d.]+)|publicdomain/(zero|mark))",
    re.IGNORECASE,
)
_XML_IDENTIFIER = re.compile(
    r"<((?:[\w-]+:)?(?:identifier|shelfLocator|recordIdentifier|callNumber|localIdentifier))"
    r"\b[^>]*>\s*([^<]+?)\s*</",
    re.IGNORECASE,
)
_OAI_HEADER = re.compile(r"<(?:[\w-]+:)?header\b.*?</(?:[\w-]+:)?header>", re.DOTALL)
_MARC_CONTROL_001 = re.compile(r'<(?:[\w-]+:)?controlfield\s+tag="001"[^>]*>\s*([^<]+?)\s*<')
_MARC_DATAFIELD = re.compile(
    r'<(?:[\w-]+:)?datafield\s+tag="(035|050|090|099)"[^>]*>(.*?)</(?:[\w-]+:)?datafield>',
    re.DOTALL,
)
_MARC_SUBFIELD_A = re.compile(r'<(?:[\w-]+:)?subfield\s+code="a"[^>]*>\s*([^<]+?)\s*<')
_JSON_ID_KEY = re.compile(r"identifier|call.?number|shelf|accession|local.?id", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Reading uneven fields
# --------------------------------------------------------------------------- #
def field_value(doc: object, dotted: str) -> Any:
    """A field by its dotted DPLA name, from a flattened or a nested record.

    ``fields=`` searches return ``{"sourceResource.title": ...}``; fetches
    return ``{"sourceResource": {"title": [...]}}``. Lists met on the way
    down are walked, so ``sourceResource.date.displayDate`` on a nested
    record gathers the display date of every date entry.
    """
    if not isinstance(doc, dict):
        return None
    if dotted in doc:
        return doc[dotted]
    nodes: list[Any] = [doc]
    for part in dotted.split("."):
        found: list[Any] = []
        for node in nodes:
            if isinstance(node, dict) and part in node:
                value = node[part]
                found.extend(value if isinstance(value, list) else [value])
        if not found:
            return None
        nodes = found
    return nodes


def values(value: object) -> list[Any]:
    """A field as a flat list, whatever its cardinality."""
    if value is None:
        return []
    if isinstance(value, list):
        out: list[Any] = []
        for v in value:
            out.extend(v if isinstance(v, list) else [v])
        return [v for v in out if v is not None]
    return [value]


def plain(text: object) -> str:
    """Unescape entities, strip stray markup and collapse whitespace."""
    if not isinstance(text, str):
        return ""
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", text))).strip()


def texts(value: object) -> list[str]:
    """Every non-empty string in a field, cleaned, without repeats."""
    seen: dict[str, None] = {}
    for v in values(value):
        if isinstance(v, str) and (t := plain(v)):
            seen.setdefault(t, None)
    return list(seen)


def first_text(value: object) -> str | None:
    """The first non-empty string in a field, or None."""
    found = texts(value)
    return found[0] if found else None


def joined(value: object, limit: int = 0) -> str | None:
    """A field's strings joined with "; ", optionally only the first ``limit``."""
    found = texts(value)
    if limit:
        found = found[:limit]
    return "; ".join(found) or None


def name_of(value: object) -> str | None:
    """The name of an agent field: ``{"name": ...}`` objects or legacy strings."""
    for v in values(value):
        if isinstance(v, dict) and (name := plain(v.get("name"))):
            return name
        if isinstance(v, str) and (name := plain(v)):
            return name
    return None


def clip(text: str, limit: int) -> tuple[str, bool]:
    """Cut text to ``limit`` characters at a word boundary. Returns (text, truncated)."""
    if len(text) <= limit:
        return text, False
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + " …", True


# --------------------------------------------------------------------------- #
# Identifiers and query text
# --------------------------------------------------------------------------- #
def dpla_id(value: object) -> str | None:
    """A DPLA record id from the id itself or a dp.la address, or None.

    Accepts ``4917d2c8…``, ``https://dp.la/item/4917d2c8…`` and the
    ``http://dp.la/api/items/4917d2c8…#SourceResource`` form a record carries
    as ``@id``. DPLA allows 1-32 letters, digits and hyphens; its own ids
    are 32 lower-case hex digits.
    """
    text = str(value or "").strip()
    if match := _DPLA_URL.search(text):
        text = match.group(1)
    if not _DPLA_ID.fullmatch(text):
        return None
    return text.lower() if _HEX32.fullmatch(text) else text


def dpla_url(item_id: str | None) -> str | None:
    """DPLA's public page for a record."""
    return DPLA_ITEM_PAGE.format(item_id) if item_id else None


def is_date(text: str) -> bool:
    """True for ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``: what DPLA accepts."""
    return bool(_DATE.fullmatch(text))


def escape_query(text: str) -> str:
    """Escape the characters DPLA's query parser would read as syntax.

    Colons, slashes, brackets, braces, carets, tildes and exclamation marks
    become literal. Already-escaped characters are left alone.
    """
    return _LUCENE_LITERAL.sub(r"\\\1", text)


# --------------------------------------------------------------------------- #
# Rights
# --------------------------------------------------------------------------- #
def rights_label(uri: str | None) -> str | None:
    """A rights URI's short name: "No Copyright - United States", "CC BY 4.0"."""
    if not uri:
        return None
    if match := _RIGHTS_URI.search(uri):
        code = match.group(1)
        for known, label in RIGHTS_STATEMENTS.items():
            if known.lower() == code.lower():
                return label
        return code
    if match := _CC_URI.search(uri):
        if match.group(4):
            return "CC0" if match.group(4).lower() == "zero" else "Public Domain Mark"
        return f"CC {match.group(2).upper()} {match.group(3)}"
    return None


# --------------------------------------------------------------------------- #
# Places
# --------------------------------------------------------------------------- #
def places(spatial: object, *, detail: bool = False, limit: int = 0) -> list[dict]:
    """The places a record names, as written and as DPLA geocoded them."""
    keys = ("name", "county", "state") + (("city", "country", "coordinates") if detail else ())
    out: list[dict] = []
    seen: set[tuple] = set()
    for p in values(spatial):
        if isinstance(p, str):
            entry = {"name": plain(p)} if plain(p) else {}
        elif isinstance(p, dict):
            entry = {k: v for k in keys if (v := first_text(p.get(k)))}
        else:
            entry = {}
        marker = tuple(sorted(entry.items()))
        if entry and marker not in seen:
            seen.add(marker)
            out.append(entry)
    return out[:limit] if limit else out


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
def item_summary(doc: object) -> dict:
    """One record as a search hit: what it is, who holds it, where, and its rights.

    ``is_shown_at``, ``institution`` and ``rights`` are always present, even
    when empty, because their absence is itself worth knowing. Other empty
    fields are left out to keep a page of hits small.
    """
    if not isinstance(doc, dict):
        return {}
    item_id = first_text(field_value(doc, "id"))
    rights = first_text(field_value(doc, "rights"))
    label = rights_label(rights)
    note, _ = clip(" ".join(texts(field_value(doc, "sourceResource.rights"))), RIGHTS_NOTE_LIMIT)
    if label and note.lower() == label.lower():
        note = ""
    title, _ = clip(first_text(field_value(doc, "sourceResource.title")) or "", TITLE_LIMIT)
    out: dict[str, Any] = {
        "id": item_id,
        "title": title or None,
        "display_date": joined(field_value(doc, "sourceResource.date.displayDate")),
        "creator": joined(field_value(doc, "sourceResource.creator"), limit=3),
        "places": places(field_value(doc, "sourceResource.spatial"), limit=5),
        "type": joined(field_value(doc, "sourceResource.type")),
        "format": joined(field_value(doc, "sourceResource.format"), limit=3),
        "institution": name_of(field_value(doc, "dataProvider")),
        "hub": name_of(field_value(doc, "provider")),
        "intermediate_provider": first_text(field_value(doc, "intermediateProvider")),
        "is_shown_at": first_text(field_value(doc, "isShownAt")),
        "thumbnail": first_text(field_value(doc, "object")),
        "has_iiif": bool(first_text(field_value(doc, "iiifManifest"))),
        "rights": rights,
        "rights_label": label,
        "rights_note": note or None,
        "dpla_url": dpla_url(item_id),
    }
    keep = {"id", "title", "institution", "is_shown_at", "rights", "has_iiif"}
    return {k: v for k, v in out.items() if k in keep or v not in (None, [], "")}


def _dates(doc: dict) -> list[dict]:
    out = []
    for d in values(field_value(doc, "sourceResource.date")):
        if isinstance(d, dict):
            entry = {
                "as_written": first_text(d.get("displayDate")),
                "begin": first_text(d.get("begin")),
                "end": first_text(d.get("end")),
            }
            if any(entry.values()):
                out.append(entry)
        elif isinstance(d, str) and plain(d):
            out.append({"as_written": plain(d), "begin": None, "end": None})
    return out


def item_detail(
    doc: object,
    *,
    include_original_record: bool = False,
    retrieved: str | None = None,
    original_limit: int = ORIGINAL_RECORD_LIMIT,
) -> dict:
    """One record in full, with the parts a citation of the holder's record needs."""
    if not isinstance(doc, dict):
        return {}
    out = item_summary(doc)
    out["title"] = first_text(field_value(doc, "sourceResource.title"))
    out["places"] = places(field_value(doc, "sourceResource.spatial"), detail=True)
    description, truncated = clip(
        " ".join(texts(field_value(doc, "sourceResource.description"))), DESCRIPTION_LIMIT
    )
    titles = texts(field_value(doc, "sourceResource.title"))
    extra: dict[str, Any] = {
        "other_titles": titles[1:],
        "dates": _dates(doc),
        "description": description or None,
        "description_truncated": truncated or None,
        "subjects": texts(field_value(doc, "sourceResource.subject.name"))[:30],
        "collections": texts(field_value(doc, "sourceResource.collection.title")),
        "publisher": joined(field_value(doc, "sourceResource.publisher")),
        "language": joined(field_value(doc, "sourceResource.language.name")),
        "extent": joined(field_value(doc, "sourceResource.extent")),
        "temporal": joined(field_value(doc, "sourceResource.temporal.displayDate")),
        "identifiers": texts(field_value(doc, "sourceResource.identifier")),
        "institution_wikidata": first_text(field_value(doc, "dataProvider.exactMatch")),
        "iiif_manifest": first_text(field_value(doc, "iiifManifest")),
        "rights_statement": joined(field_value(doc, "sourceResource.rights")),
    }
    out.update({k: v for k, v in extra.items() if v not in (None, [], "")})
    out.pop("rights_note", None)
    out["citation_parts"] = citation_parts(doc, retrieved=retrieved)
    if include_original_record:
        text, total = original_record_text(doc.get("originalRecord"))
        clipped = text[:original_limit]
        out["original_record"] = clipped or None
        out["original_record_truncated"] = total > len(clipped)
        out["original_record_chars"] = total
    return out


def citation_parts(doc: object, *, retrieved: str | None = None) -> dict:
    """What a citation of the holding institution's record is built from.

    The repository is the institution (``dataProvider``), not DPLA and not
    the hub. ``dpla_id`` is kept only so the description can be found again;
    it is minted from the hub's local id and changes when a hub re-platforms.
    """
    if not isinstance(doc, dict):
        return {}
    return {
        "holding_institution": name_of(field_value(doc, "dataProvider")),
        "title": first_text(field_value(doc, "sourceResource.title")),
        "display_date": joined(field_value(doc, "sourceResource.date.displayDate")),
        "institution_identifiers": institution_identifiers(doc),
        "is_shown_at": first_text(field_value(doc, "isShownAt")),
        "rights": first_text(field_value(doc, "rights")),
        "dpla_id": first_text(field_value(doc, "id")),
        "retrieved": retrieved or date.today().isoformat(),
    }


def institution_identifiers(doc: object) -> list[dict]:
    """The holder's own identifiers: call numbers, local ids, OCLC numbers.

    Read from ``sourceResource.identifier`` and from ``originalRecord``, the
    hub's raw harvested metadata, which often holds a call number or local
    id that DPLA's mapping dropped. Each value names where it was found. The
    record's own addresses (the item page, thumbnail and manifest) and the
    DPLA id are left out. The hub's harvest id, from the OAI header, goes
    last: it names the record in the hub's feed, not in the holder's
    catalogue.
    """
    if not isinstance(doc, dict):
        return []
    skip = {
        first_text(field_value(doc, name)) or ""
        for name in ("isShownAt", "id", "object", "iiifManifest")
    }
    found: list[dict] = []
    harvest: list[dict] = []
    seen: set[str] = set()

    def add(value: object, source: str) -> None:
        text = plain(value) if isinstance(value, str) else ""
        if not text or len(text) > 200 or text in skip or text in seen:
            return
        seen.add(text)
        if source == "OAI harvest id" or text.lower().startswith("oai:"):
            harvest.append({"value": text, "source": "OAI harvest id"})
        else:
            found.append({"value": text, "source": source})

    for value in texts(field_value(doc, "sourceResource.identifier")):
        add(value, "sourceResource.identifier")
    original = doc.get("originalRecord")
    raw = original.get("stringValue") if isinstance(original, dict) else original
    if isinstance(raw, str):
        headers = _OAI_HEADER.findall(raw)
        body = _OAI_HEADER.sub(" ", raw)
        for element, value in _XML_IDENTIFIER.findall(body):
            add(html.unescape(value), element)
        for value in _MARC_CONTROL_001.findall(body):
            add(html.unescape(value), "MARC 001")
        for tag, fields in _MARC_DATAFIELD.findall(body):
            for value in _MARC_SUBFIELD_A.findall(fields):
                add(html.unescape(value), f"MARC {tag}")
        for header in headers:
            for _, value in _XML_IDENTIFIER.findall(header):
                add(html.unescape(value), "OAI harvest id")
    elif isinstance(original, dict):
        for key, value in _json_identifiers(original):
            add(value, key)
    return (found + harvest)[:IDENTIFIER_LIMIT]


def _json_identifiers(node: object, depth: int = 0) -> list[tuple[str, str]]:
    """Identifier-like string values in a JSON original record, with their keys."""
    out: list[tuple[str, str]] = []
    if depth > 6:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and _JSON_ID_KEY.search(key):
                for v in values(value):
                    if isinstance(v, str):
                        out.append((key, v))
                    elif isinstance(v, dict):
                        for inner in ("#text", "$", "value", "_"):
                            if isinstance(v.get(inner), str):
                                out.append((key, v[inner]))
            if isinstance(value, dict | list):
                out.extend(_json_identifiers(value, depth + 1))
    elif isinstance(node, list):
        for value in node:
            out.extend(_json_identifiers(value, depth + 1))
    return out


def original_record_text(original: object) -> tuple[str, int]:
    """The raw harvested record as text, and its full length."""
    if original is None:
        return "", 0
    raw = original.get("stringValue") if isinstance(original, dict) else None
    if isinstance(raw, str):
        text = raw.strip()
    elif isinstance(original, str):
        text = original.strip()
    else:
        text = json.dumps(original, ensure_ascii=False, separators=(",", ":"))
    return text, len(text)


# --------------------------------------------------------------------------- #
# Result sets
# --------------------------------------------------------------------------- #
def _title_stem(title: str | None) -> str:
    """A title's main part: lower case, cut at the first , : ; / ( or [."""
    if not title:
        return ""
    stem = re.split(r"[,:;/(\[]", title.lower(), maxsplit=1)[0]
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", stem).split())


def mark_duplicates(summaries: list[dict]) -> list[dict]:
    """Flag hits in one page that look like copies of the same work.

    One county history is often in DPLA three times: a HathiTrust copy, an
    Internet Archive copy and a state hub's scan. Matching is on the title
    up to its first comma or colon, at least three words, plus the first year
    of the date, so it is a hint ("possible"), not a merge. A hit in a group
    gains ``possible_duplicate_of``: the ids of the others.
    """

    def key(s: dict) -> tuple[str, str] | None:
        stem = _title_stem(s.get("title"))
        years = _YEAR.findall(s.get("display_date") or "")
        if len(stem.split()) < 3 or not years:
            return None
        return stem, years[0]

    groups: dict[tuple, list[str]] = {}
    for s in summaries:
        if (k := key(s)) and s.get("id"):
            groups.setdefault(k, []).append(s["id"])
    for s in summaries:
        k = key(s)
        others = [i for i in groups.get(k, []) if i != s.get("id")] if k else []
        if others:
            s["possible_duplicate_of"] = others
    return summaries


def facet_values(facets: object, name: str) -> list[dict]:
    """One facet's values as ``{value, count}``, from DPLA's facet object.

    ``facets`` is an object keyed by field when facets were asked for, and an
    empty list when they were not. Terms facets carry ``terms[{term,count}]``;
    date facets carry ``entries[{time,count}]``, which come back newest first
    and are returned oldest first.
    """
    if not isinstance(facets, dict):
        return []
    facet = facets.get(name)
    if not isinstance(facet, dict):
        return []
    out = []
    if isinstance(facet.get("terms"), list):
        for t in facet["terms"]:
            if isinstance(t, dict) and t.get("term") is not None:
                out.append({"value": str(t["term"]), "count": _int(t.get("count"))})
    elif isinstance(facet.get("entries"), list):
        for e in facet["entries"]:
            if isinstance(e, dict) and e.get("time") is not None:
                out.append({"value": str(e["time"]), "count": _int(e.get("count"))})
        out.sort(key=lambda v: v["value"])
    return out


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# IIIF manifests
# --------------------------------------------------------------------------- #
def _iiif_text(value: object) -> str | None:
    """Text from a IIIF label: a string, a v2 ``@value`` list, or a v3 language map."""
    if isinstance(value, str):
        return plain(value) or None
    if isinstance(value, list):
        parts = [t for v in value if (t := _iiif_text(v))]
        return "; ".join(dict.fromkeys(parts)) or None
    if isinstance(value, dict):
        if "@value" in value:
            return _iiif_text(value["@value"])
        if "value" in value and "label" in value:
            return _iiif_text(value["value"])
        for lang in ("en", "none", *value.keys()):
            if lang in value:
                text = _iiif_text(value[lang])
                if text:
                    return text
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _service_id(service: object) -> str | None:
    for s in values(service):
        if isinstance(s, dict):
            sid = s.get("@id") or s.get("id")
            if isinstance(sid, str) and sid.strip():
                return sid.strip().rstrip("/")
    return None


def _resource(resource: object) -> dict:
    """The painted image of a v2 annotation or v3 body, through any Choice."""
    for r in values(resource):
        if not isinstance(r, dict):
            continue
        kind = str(r.get("@type") or r.get("type") or "")
        if kind in ("oa:Choice", "Choice"):
            inner = r.get("default") or r.get("items") or r.get("item")
            return _resource(inner)
        return r
    return {}


def manifest_pages(manifest: object) -> dict:
    """The pages of a IIIF Presentation manifest, version 2 or 3.

    Returns ``{iiif_version, label, attribution, license, pages}`` where each
    page is ``{n, label, image_url, iiif_image_service}``. A collection, or
    anything else without canvases, has no pages.
    """
    if not isinstance(manifest, dict):
        return {"iiif_version": None, "pages": []}
    context = json.dumps(manifest.get("@context") or "")
    version = 3 if "presentation/3" in context else 2 if "presentation/2" in context else None
    kind = str(manifest.get("@type") or manifest.get("type") or "")
    pages: list[dict] = []
    if version != 3 and isinstance(manifest.get("sequences"), list):
        version = version or 2
        sequence = next((s for s in manifest["sequences"] if isinstance(s, dict)), {})
        for canvas in values(sequence.get("canvases")):
            if not isinstance(canvas, dict):
                continue
            image = next((i for i in values(canvas.get("images")) if isinstance(i, dict)), {})
            resource = _resource(image.get("resource"))
            pages.append(_page(len(pages) + 1, canvas.get("label"), resource))
    elif isinstance(manifest.get("items"), list):
        version = version or 3
        for canvas in manifest["items"]:
            if not isinstance(canvas, dict) or canvas.get("type") not in (None, "Canvas"):
                continue
            body: dict = {}
            for page in values(canvas.get("items")):
                annotation = next(
                    (
                        a
                        for a in values(page.get("items") if isinstance(page, dict) else None)
                        if isinstance(a, dict)
                    ),
                    {},
                )
                body = _resource(annotation.get("body"))
                if body:
                    break
            pages.append(_page(len(pages) + 1, canvas.get("label"), body))
    statement = manifest.get("requiredStatement")
    return {
        "iiif_version": version,
        "kind": kind or None,
        "label": _iiif_text(manifest.get("label")),
        "attribution": _iiif_text(manifest.get("attribution"))
        or (_iiif_text(statement.get("value")) if isinstance(statement, dict) else None),
        "license": _iiif_text(manifest.get("license")) or _iiif_text(manifest.get("rights")),
        "pages": pages,
    }


def _page(n: int, label: object, resource: dict) -> dict:
    image = resource.get("@id") or resource.get("id")
    return {
        "n": n,
        "label": _iiif_text(label),
        "image_url": image.strip() if isinstance(image, str) and image.strip() else None,
        "iiif_image_service": _service_id(resource.get("service")),
    }


def as_dict(value: Any) -> dict:
    """``value`` if it is a dict, else an empty one."""
    return value if isinstance(value, dict) else {}
