"""Shaping DPLA records and IIIF manifests: uneven fields, citations, rights, pages."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from dpla_catalog_mcp.shape import (
    ORIGINAL_RECORD_LIMIT,
    TITLE_LIMIT,
    citation_parts,
    dpla_id,
    escape_query,
    facet_values,
    field_value,
    institution_identifiers,
    item_detail,
    item_summary,
    manifest_pages,
    mark_duplicates,
    places,
    rights_label,
    texts,
    values,
)

from .conftest import ATLAS_1929, DARTMOUTH_1793, GALVESTON_1870, HATHI_1929, fixture, recorded_docs

DOCS = recorded_docs()


def _hits(name: str) -> list[dict]:
    return fixture(name)["docs"]


# --------------------------------------------------------------------------- #
# Uneven fields
# --------------------------------------------------------------------------- #
def test_a_field_reads_from_flattened_and_nested_records_alike():
    flat = {"sourceResource.title": "Atlas", "sourceResource.date.displayDate": ["1929"]}
    nested = {"sourceResource": {"title": ["Atlas"], "date": [{"displayDate": "1929"}]}}
    for doc in (flat, nested):
        assert texts(field_value(doc, "sourceResource.title")) == ["Atlas"]
        assert texts(field_value(doc, "sourceResource.date.displayDate")) == ["1929"]


def test_a_nested_list_is_walked_on_the_way_down():
    doc = {"sourceResource": {"date": [{"displayDate": "ca. 1900"}, {"displayDate": "1901"}]}}
    assert texts(field_value(doc, "sourceResource.date.displayDate")) == ["ca. 1900", "1901"]


def test_values_flattens_strings_and_lists():
    assert values(None) == [] and values("a") == ["a"] and values(["a", ["b"], None]) == ["a", "b"]


def test_texts_cleans_and_drops_repeats():
    assert texts(["  Brock &amp; Company ", "Brock & Company", "", 7]) == ["Brock & Company"]


# --------------------------------------------------------------------------- #
# Identifiers and query text
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    [
        ATLAS_1929,
        ATLAS_1929.upper(),
        f"https://dp.la/item/{ATLAS_1929}",
        f"http://dp.la/api/items/{ATLAS_1929}#SourceResource",
        f"  {ATLAS_1929}\n",
    ],
)
def test_every_form_of_a_dpla_id_reduces_to_the_id(value):
    assert dpla_id(value) == ATLAS_1929


@pytest.mark.parametrize(
    "value", ["", None, "a" * 33, "4917d2c8 281726bc", "../../etc/passwd", "id;drop", "é"]
)
def test_what_is_not_a_dpla_id_is_none(value):
    assert dpla_id(value) is None


@pytest.mark.parametrize(
    ("raw", "sent"),
    [
        ("atlas: Champaign", r"atlas\: Champaign"),
        ("atlas 1913/1929", r"atlas 1913\/1929"),
        ("[Map of Champaign County]", r"\[Map of Champaign County\]"),
        ("Remember the Alamo!", r"Remember the Alamo\!"),
        (r"already\: escaped", r"already\: escaped"),
        ('"city directory" OR almanac* NOT Dallas', '"city directory" OR almanac* NOT Dallas'),
    ],
)
def test_query_syntax_characters_are_escaped_and_operators_kept(raw, sent):
    assert escape_query(raw) == sent


# --------------------------------------------------------------------------- #
# Rights
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("uri", "label"),
    [
        ("http://rightsstatements.org/vocab/NoC-US/1.0/", "No Copyright - United States"),
        (
            "http://rightsstatements.org/vocab/InC-EDU/1.0/",
            "In Copyright - Educational Use Permitted",
        ),
        ("http://rightsstatements.org/vocab/CNE/1.0/", "Copyright Not Evaluated"),
        ("http://creativecommons.org/publicdomain/zero/1.0/", "CC0"),
        ("http://creativecommons.org/publicdomain/mark/1.0/", "Public Domain Mark"),
        ("http://creativecommons.org/licenses/by-nc-nd/4.0/", "CC BY-NC-ND 4.0"),
        ("https://texashistory.unt.edu/terms-of-use/", None),
        (None, None),
    ],
)
def test_rights_uris_get_their_short_names(uri, label):
    assert rights_label(uri) == label


# --------------------------------------------------------------------------- #
# Search hits
# --------------------------------------------------------------------------- #
def test_a_search_hit_says_what_it_is_who_holds_it_and_where():
    [first] = [d for d in _hits("search_galveston_directory.json") if d["id"] == GALVESTON_1870]
    hit = item_summary(first)
    assert hit["title"] == "Galveston City Directory, 1870"
    assert hit["display_date"] == "1870"
    assert hit["institution"] == "Rosenberg Library"
    assert hit["hub"] == "The Portal to Texas History"
    assert hit["is_shown_at"] == "https://texashistory.unt.edu/ark:/67531/metapth636853/"
    assert hit["has_iiif"] is True
    assert hit["places"] == [{"name": "United States - Texas - Galveston County - Galveston"}]
    assert hit["dpla_url"] == f"https://dp.la/item/{GALVESTON_1870}"


def test_the_portal_to_texas_history_gives_no_rights_uri_only_a_statement():
    hit = item_summary(_hits("search_galveston_directory.json")[0])
    assert hit["rights"] is None and "rights_label" not in hit
    assert hit["rights_note"].startswith("The contents of The Portal to Texas History")


def test_a_rights_note_that_repeats_the_label_is_dropped():
    [hit] = [item_summary(d) for d in _hits("search_champaign_atlas.json") if d["id"] == ATLAS_1929]
    assert hit["rights_label"] == "No Copyright - United States"
    assert "rights_note" not in hit


def test_a_hit_never_carries_the_original_record():
    full = DOCS[ATLAS_1929]
    assert "originalRecord" in full
    hit = item_summary(full)
    assert not any("original" in key.lower() for key in hit)


def test_a_hit_reads_the_same_from_a_search_and_from_a_fetch():
    """Flattened search keys and the nested record must shape alike."""
    [searched] = [d for d in _hits("search_champaign_atlas.json") if d["id"] == ATLAS_1929]
    assert item_summary(searched) == item_summary(DOCS[ATLAS_1929])


def test_the_uiuc_atlas_names_no_place_at_all():
    """Why `place` misses records: recorded, the 1929 atlas has no spatial field."""
    assert "places" not in item_summary(DOCS[ATLAS_1929])
    assert item_detail(DOCS[ATLAS_1929])["places"] == []


def test_an_intermediate_provider_comes_through():
    hit = item_summary(_hits("search_south_dakota_plat.json")[0])
    assert hit["intermediate_provider"] == "Digital Library of South Dakota"
    assert hit["hub"] == "Minnesota Digital Library"
    assert hit["institution"] == "University of South Dakota"


def test_a_long_title_is_clipped_in_a_hit_and_whole_in_the_record():
    doc = _hits("search_south_dakota_plat.json")[0]
    assert len(item_summary(doc)["title"]) <= TITLE_LIMIT + 2
    assert item_detail(doc)["title"].endswith("civil government, etc.")


def test_a_legacy_string_data_provider_still_names_the_institution():
    doc = copy.deepcopy(DOCS[HATHI_1929])
    doc["dataProvider"] = "University of Illinois"
    assert item_summary(doc)["institution"] == "University of Illinois"
    assert citation_parts(doc)["holding_institution"] == "University of Illinois"


def test_a_record_without_is_shown_at_says_so():
    doc = copy.deepcopy(DOCS[HATHI_1929])
    del doc["isShownAt"]
    hit = item_summary(doc)
    assert "is_shown_at" in hit and hit["is_shown_at"] is None


def test_string_or_list_cardinality_shapes_alike():
    doc = copy.deepcopy(DOCS[HATHI_1929])
    doc["sourceResource"]["title"] = doc["sourceResource"]["title"][0]
    doc["rights"] = [doc["rights"]]
    assert item_summary(doc) == item_summary(DOCS[HATHI_1929])


def test_places_keep_county_and_state_when_geocoded():
    spatial = [{"name": "Suffolk County (Mass.)", "county": "Suffolk", "state": "Massachusetts"}]
    assert places(spatial) == [
        {"name": "Suffolk County (Mass.)", "county": "Suffolk", "state": "Massachusetts"}
    ]
    assert places(["Galveston", "Galveston"]) == [{"name": "Galveston"}]


def test_copies_of_one_work_are_flagged_as_possible_duplicates():
    hits = {
        h["id"]: h
        for h in mark_duplicates([item_summary(d) for d in _hits("search_champaign_atlas.json")])
    }
    # The 1929 atlas: UIUC's scan and HathiTrust's copy of the same book.
    assert hits[ATLAS_1929]["possible_duplicate_of"] == [HATHI_1929]
    assert hits[HATHI_1929]["possible_duplicate_of"] == [ATLAS_1929]
    # The 1913 edition is a different book.
    assert "possible_duplicate_of" not in hits["83690f4313d1804c1631c46f038a0366"]


def test_short_generic_titles_are_never_grouped():
    hits = [
        {"id": "a", "title": "Map", "display_date": "1900"},
        {"id": "b", "title": "Map", "display_date": "1900"},
    ]
    assert all("possible_duplicate_of" not in h for h in mark_duplicates(hits))


# --------------------------------------------------------------------------- #
# Full records and citations
# --------------------------------------------------------------------------- #
def test_citation_parts_name_the_holder_and_its_record():
    parts = item_detail(DOCS[ATLAS_1929])["citation_parts"]
    assert parts["holding_institution"] == "University of Illinois Urbana-Champaign Library"
    assert parts["title"] == "Standard atlas of Champaign County, Illinois"
    assert parts["display_date"] == "1929"
    assert parts["is_shown_at"].startswith("https://digital.library.illinois.edu/items/")
    assert parts["rights"] == "http://rightsstatements.org/vocab/NoC-US/1.0/"
    assert parts["dpla_id"] == ATLAS_1929
    assert parts["retrieved"] == date.today().isoformat()


def test_the_holders_own_id_comes_from_the_original_record_before_the_harvest_id():
    ids = institution_identifiers(DOCS[ATLAS_1929])
    assert ids[0] == {"value": "99247290812205899", "source": "dc:identifier"}
    assert ids[-1]["source"] == "OAI harvest id"
    assert all(i["value"] != DOCS[ATLAS_1929]["isShownAt"] for i in ids)


def test_hathitrust_identifiers_include_the_oclc_number():
    values_ = [i["value"] for i in institution_identifiers(DOCS[HATHI_1929])]
    assert "(OCoLC)13824847" in values_


def test_marc_identifiers_are_read_when_dpla_dropped_them():
    doc = copy.deepcopy(DOCS[HATHI_1929])
    del doc["sourceResource"]["identifier"]
    found = institution_identifiers(doc)
    assert {"value": "(OCoLC)13824847", "source": "MARC 035"} in found


def test_the_records_own_thumbnail_and_manifest_are_not_identifiers():
    found = [i["value"] for i in institution_identifiers(DOCS[GALVESTON_1870])]
    assert "ark:/67531/metapth636853" in found
    assert not any(v.endswith(("/small/", "/manifest/")) for v in found)


def test_mods_record_identifiers_are_read():
    found = institution_identifiers(DOCS[DARTMOUTH_1793])
    assert {
        "value": "nh-cities-towns-northumberland-1793",
        "source": "mods:recordIdentifier",
    } in found


def test_a_json_original_record_is_searched_for_identifiers():
    doc = {
        "id": "x",
        "originalRecord": {
            "metadata": {"callNumber": "G1408.C4 B7 1929", "nested": [{"localIdentifier": "L-7"}]},
            "title": "not an identifier",
        },
    }
    assert institution_identifiers(doc) == [
        {"value": "G1408.C4 B7 1929", "source": "callNumber"},
        {"value": "L-7", "source": "localIdentifier"},
    ]


def test_the_original_record_is_returned_only_when_asked_and_cut():
    assert "original_record" not in item_detail(DOCS[HATHI_1929])
    full = item_detail(DOCS[HATHI_1929], include_original_record=True)
    assert full["original_record"].startswith("<record>")
    assert full["original_record_truncated"] is False
    short = item_detail(DOCS[HATHI_1929], include_original_record=True, original_limit=100)
    assert len(short["original_record"]) == 100 and short["original_record_truncated"] is True
    assert (
        short["original_record_chars"] == full["original_record_chars"] > ORIGINAL_RECORD_LIMIT // 4
    )


def test_a_full_record_carries_subjects_identifiers_and_dates():
    d = item_detail(DOCS[HATHI_1929])
    assert d["subjects"] == ["Real property--Illinois--Champaign County", "Champaign County (Ill.)"]
    assert "(OCoLC)13824847" in d["identifiers"]
    assert d["dates"] == [{"as_written": "1929", "begin": "1929", "end": "1929"}]
    assert d["rights_label"] == "Public Domain Mark"
    assert d["institution_wikidata"] == "http://www.wikidata.org/entity/Q457281"


# --------------------------------------------------------------------------- #
# Facets
# --------------------------------------------------------------------------- #
def test_terms_facets_become_values_and_counts():
    payload = fixture("facet_institution_plat_book.json")
    found = facet_values(payload["facets"], "dataProvider.name")
    assert found[0] == {"value": "Minnesota Historical Society", "count": 417}
    assert len(found) == 10


def test_year_facets_come_back_oldest_first():
    payload = fixture("facet_year_galveston_directory.json")
    years = [v["value"] for v in facet_values(payload["facets"], "sourceResource.date.begin.year")]
    assert years == sorted(years) and years[0] == "1859"


def test_no_facets_is_an_empty_list_not_an_object():
    """DPLA sends "facets": [] when none were asked for."""
    assert fixture("search_champaign_atlas.json")["facets"] == []
    assert facet_values([], "provider.name") == []
    assert facet_values({}, "provider.name") == []


# --------------------------------------------------------------------------- #
# IIIF manifests
# --------------------------------------------------------------------------- #
def test_a_v2_manifest_lists_its_pages():
    parsed = manifest_pages(fixture("manifest_unt_galveston_1870_v2.json"))
    assert parsed["iiif_version"] == 2
    assert parsed["label"] == "Galveston City Directory, 1870"
    assert parsed["license"] == "https://texashistory.unt.edu/terms-of-use/"
    assert parsed["attribution"].startswith("Galveston & Texas History Center")
    assert len(parsed["pages"]) == 8
    assert parsed["pages"][0] == {
        "n": 1,
        "label": "Front Cover",
        "image_url": "https://texashistory.unt.edu/iiif/ark:/67531/metapth636853/m1/1/full/max/0/default.jpg",
        "iiif_image_service": "https://texashistory.unt.edu/iiif/ark:/67531/metapth636853/m1/1",
    }


def test_a_v3_manifest_lists_its_pages():
    parsed = manifest_pages(fixture("manifest_dartmouth_northumberland_v3.json"))
    assert parsed["iiif_version"] == 3
    assert parsed["label"] == "Map of Northumberland"
    assert parsed["license"] == "http://rightsstatements.org/vocab/NoC-US/1.0/"
    assert parsed["attribution"] == "Courtesy of Digital by Dartmouth Libraries"
    [page] = parsed["pages"]
    assert page["image_url"].endswith("/full/max/0/default.jpg")
    assert page["iiif_image_service"].endswith("nhct-northumberland-1793-001.tif")
    assert page["label"] == "nhct-northumberland-1793-001.tif"


def test_a_choice_of_images_takes_the_default():
    v2 = {
        "@context": "http://iiif.io/api/presentation/2/context.json",
        "sequences": [
            {
                "canvases": [
                    {
                        "label": [{"@value": "Plate 7", "@language": "en"}],
                        "images": [
                            {
                                "resource": {
                                    "@type": "oa:Choice",
                                    "default": {
                                        "@id": "https://x.org/a.jpg",
                                        "service": [{"@id": "https://x.org/a/"}],
                                    },
                                }
                            }
                        ],
                    }
                ]
            }
        ],
    }
    [page] = manifest_pages(v2)["pages"]
    assert page == {
        "n": 1,
        "label": "Plate 7",
        "image_url": "https://x.org/a.jpg",
        "iiif_image_service": "https://x.org/a",
    }


def test_a_collection_has_no_pages():
    collection = {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "type": "Collection",
        "items": [{"id": "https://x.org/m1", "type": "Manifest"}],
    }
    assert manifest_pages(collection)["pages"] == []


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "func",
    [item_summary, item_detail, citation_parts, institution_identifiers, manifest_pages, places],
)
@pytest.mark.parametrize(
    "junk",
    [
        None,
        "text",
        7,
        [],
        {"id": 5, "sourceResource": "x", "dataProvider": [None, 3], "originalRecord": 9},
        {"sequences": [None, {"canvases": "x"}], "items": None},
        {"items": [{"type": "Canvas", "items": [{"items": [{"body": [None]}]}]}]},
    ],
)
def test_malformed_input_never_raises(func, junk):
    func(junk)
