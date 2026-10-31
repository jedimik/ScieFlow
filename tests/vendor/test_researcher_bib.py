"""Offline tests for researcher_core.bib (D20).

Three groups:

1. The M1 test cases, ported verbatim from ``scripts/tests/test_bib_validator.py``. The
   core parser must not regress a single one of them. The ONE deliberate divergence is
   ``@string`` expansion, which has its own tests below and is documented in the module.
2. The torture fixture: every construct in one file, parsed with zero losses.
3. What M1 does not have: emission from CSLRecord, and the parse -> emit -> parse fixed
   point.
"""

# Ported from researcher v1.0.0 (MIT), core/tests/test_bib.py. Imports are rewritten to
# scieflow._vendor.researcher_core; no test is removed; fixtures are byte copies under
# tests/fixtures/kernel2. See VENDORED.md.

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scieflow._vendor.researcher_core.bib import (
    BibDatabase,
    BibEntry,
    BibParseError,
    citation_key_for,
    emit_bib,
    emit_entry,
    entry_to_record,
    latex_to_text,
    parse_bib,
    parse_bib_file,
    record_to_entry,
    records_from_bib,
    split_bibtex_names,
)
from scieflow._vendor.researcher_core.model import CSLDate, CSLName, CSLRecord

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "kernel2"
TORTURE = FIXTURES / "torture.bib"

# The exact shape that broke the v1 regex parser: the entry's closing brace sits on the
# last field line, so the entry ends in "}}".
COMPACT_SAMPLE = """
@article{mehari2022ssl,
  title={Self-supervised representation learning from 12-lead ECG data},
  author={Mehari, Temesgen and Strodthoff, Nils},
  journal={Computers in Biology and Medicine}, volume={141}, pages={105114}, year={2022},
  doi={10.1016/j.compbiomed.2021.105114}}

@article{sarkar2022ssl,
  title={Self-Supervised ECG Representation Learning for Emotion Recognition},
  author={Sarkar, Pritam and Etemad, Ali},
  journal={IEEE Transactions on Affective Computing}, volume={13}, number={3},
  pages={1541--1554}, year={2022},
  doi={10.1109/taffc.2020.3014842}}
"""


@pytest.fixture()
def torture() -> BibDatabase:
    return parse_bib_file(TORTURE)


# ---------------------------------------------------------------------------
# 1. The M1 cases, ported
# ---------------------------------------------------------------------------


def test_compact_entries_parse() -> None:
    db = parse_bib(COMPACT_SAMPLE)
    assert len(db) == 2
    assert set(db.keys()) == {"mehari2022ssl", "sarkar2022ssl"}
    first = db.by_key("mehari2022ssl")
    assert first is not None
    assert first.get("doi") == "10.1016/j.compbiomed.2021.105114"
    assert first.get("title") == "Self-supervised representation learning from 12-lead ECG data"
    assert first.get("year") == "2022"


def test_citation_audit_example_parses_all_ten() -> None:
    """The acceptance case from D20: the showcased bibliography, all compact entries."""
    example = FIXTURES / "citation-audit.md"
    blocks = re.findall(r"```[a-z]*\n(.*?)```", example.read_text(encoding="utf-8"), re.DOTALL)
    bib_block = next(b for b in blocks if "@article" in b)

    db = parse_bib(bib_block)

    assert len(db) == 10
    assert "kessler2021cmae" in db.keys()  # the seeded fake (D8)
    for entry in db:
        assert entry.get("doi"), f"{entry.key} lost its DOI"
        assert entry.get("title"), f"{entry.key} lost its title"
        assert entry.get("author"), f"{entry.key} lost its authors"
    # And the same entries survive projection into the canonical model.
    records = db.records()
    assert [r.DOI for r in records] == [
        "10.1016/j.compbiomed.2021.105114",
        "10.1371/journal.pcbi.1009862",
        "10.1038/s41597-020-0495-6",
        "10.1109/jbhi.2020.3022989",
        "10.1038/s41467-020-15432-4",
        "10.1038/s41591-018-0268-3",
        "10.1038/s41467-023-39472-8",
        "10.3390/s23094221",
        "10.1109/taffc.2020.3014842",
        "10.1109/tbme.2021.3098765",  # normalized to lowercase by model.normalize_doi
    ]


def test_nested_braces_not_truncated() -> None:
    db = parse_bib(
        "@article{key1,\n  title={A {Nested} Title with {Deep {Braces}}},\n  year={2020},\n}"
    )
    assert db.entries[0].get("title") == "A {Nested} Title with {Deep {Braces}}"


def test_quoted_and_bare_values() -> None:
    db = parse_bib('@article{key2, author = "Doe, Jane", year = 2020, pages = {1--10}}')
    entry = db.entries[0]
    assert entry.get("author") == "Doe, Jane"
    assert entry.get("year") == "2020"
    assert entry.get("pages") == "1--10"


def test_string_concatenation() -> None:
    db = parse_bib('@article{key3, title = "Part one " # "and part two", year = {1999}}')
    assert db.entries[0].get("title") == "Part one and part two"


def test_comment_preamble_string_skipped() -> None:
    text = """
@comment{this is not an entry}
@string{jbhi = {IEEE Journal of Biomedical and Health Informatics}}
@preamble{"\\newcommand{\\x}{y}"}
@article{real1, title={Real}, year={2021}}
"""
    db = parse_bib(text)
    assert db.keys() == ["real1"]
    # Skipped as ENTRIES, but captured, not discarded: emission has to round-trip them.
    assert db.comments == ["this is not an entry"]
    assert db.preambles == ['"\\newcommand{\\x}{y}"']
    assert db.strings == {"jbhi": "IEEE Journal of Biomedical and Health Informatics"}


def test_first_author_surname_forms() -> None:
    def surname(field: str) -> str:
        entry = BibEntry("article", "k", {"author": field})
        return entry.authors[0].surname if entry.authors else ""

    assert surname("Doe, Jane and Smith, Ann") == "Doe"
    assert surname("Jane Doe and Ann Smith") == "Doe"
    assert surname("{van der Berg}, Hans") == "van der Berg"
    assert surname("") == ""


# ---------------------------------------------------------------------------
# 2. The torture fixture: zero losses
# ---------------------------------------------------------------------------


def test_torture_parses_every_entry_and_nothing_else(torture: BibDatabase) -> None:
    assert torture.keys() == [
        "nested2020",
        "quoted2021",
        "bare2022",
        "compact2023",
        "trailing2024",
        "concat2016",
        "paren2019",
        "accents2018",
        "corporate2017",
        "upper2014",
        "smith:2019-a",
    ]
    # The @comment/@preamble/@string blocks and the commented-out decoy yield no entries.
    assert "decoy2099" not in torture.keys()
    assert torture.problems == []


def test_torture_loses_no_field(torture: BibDatabase) -> None:
    for entry in torture:
        for name in ("title", "author", "year", "doi"):
            assert entry.get(name), f"{entry.key} lost its {name}"
        for name, value in entry.fields.items():
            assert not value.endswith(("{", ",", "\\")), f"{entry.key}.{name} looks truncated"
    blob = " ".join(v for e in torture for v in e.fields.values())
    assert "noopsort" not in blob  # nothing leaked out of @preamble
    assert "Not an entry" not in blob  # nothing leaked out of @comment
    assert "must never be parsed" not in blob  # nothing leaked out of the % decoy


def test_torture_hard_values(torture: BibDatabase) -> None:
    by_key = {entry.key: entry for entry in torture.entries}

    # Nested braces and a comma inside a braced value survive whole.
    assert by_key["nested2020"].get("title") == "A {Nested {Deep}} Title, with comma"

    # A quoted value carrying BOTH a comma and braces is not cut at either.
    assert by_key["quoted2021"].get("title") == "Commas, {Braces}, and Quotes All in One Value"
    assert by_key["quoted2021"].get("year") == "2021"  # bare numeric

    # Bare numeric value.
    assert by_key["bare2022"].get("volume") == "26"

    # Compact entry closed by }} on its last field line.
    assert by_key["compact2023"].entry_type == "inproceedings"
    assert by_key["compact2023"].get("doi") == "10.1234/compact"

    # Trailing comma before the closing brace.
    assert by_key["trailing2024"].get("publisher") == "Addison-Wesley"

    # Parenthesis-delimited entry.
    assert by_key["paren2019"].entry_type == "article"
    assert by_key["paren2019"].get("journal") == "Journal of Delimiters"
    assert by_key["paren2019"].get("year") == "2019"

    # Uppercase @ARTICLE: types are case-insensitive, keys are not.
    assert by_key["upper2014"].entry_type == "article"

    # A key with punctuation, and fields no CSL variable maps to.
    assert by_key["smith:2019-a"].get("annote").startswith("An unmapped field")
    assert by_key["smith:2019-a"].get("langid") == "english"


def test_line_numbers_are_recorded(torture: BibDatabase) -> None:
    """Diagnostics need to name a line; equality must not care about one."""
    lines = [entry.line for entry in torture]
    assert all(line > 0 for line in lines)
    assert lines == sorted(lines)
    # Line numbers do not participate in equality: a round-tripped entry is the same entry.
    assert BibEntry("article", "k", {"year": "2020"}, line=3) == BibEntry(
        "article", "k", {"year": "2020"}, line=99
    )


# ---------------------------------------------------------------------------
# The @string decision (the deliberate divergence from M1)
# ---------------------------------------------------------------------------


def test_string_macros_are_expanded_by_default(torture: BibDatabase) -> None:
    """Core EXPANDS @string macros. M1 does not, and left 'journal = jbhi' as "jbhi".

    This matters because core feeds verification: comparing the macro NAME "jbhi" against
    real index metadata would manufacture a mismatch on axis (a) out of thin air.
    """
    by_key = {entry.key: entry for entry in torture.entries}
    assert by_key["bare2022"].get("journal") == "IEEE Journal of Biomedical and Health Informatics"
    assert torture.strings["jbhi"] == "IEEE Journal of Biomedical and Health Informatics"
    # And the expanded value is what reaches the canonical record.
    record = entry_to_record(by_key["bare2022"])
    assert record.container_title == "IEEE Journal of Biomedical and Health Informatics"


def test_string_macros_left_literal_when_expansion_is_off() -> None:
    """The M1 reading is still available, and is exactly the M1 result."""
    db = parse_bib_file(TORTURE, expand_strings=False)
    assert db.by_key("bare2022").get("journal") == "jbhi"


def test_macro_concatenated_with_a_literal(torture: BibDatabase) -> None:
    assert torture.by_key("concat2016").get("journal") == (
        "IEEE Journal of Biomedical and Health Informatics: Special Issue on Determinism"
    )


def test_braced_value_is_never_treated_as_a_macro() -> None:
    """Only a BARE token can name a macro. {jbhi} is the literal string "jbhi"."""
    db = parse_bib("@string{jbhi = {Expanded}}\n@article{k, journal = {jbhi}, year = 2020}")
    assert db.by_key("k").get("journal") == "jbhi"


def test_builtin_month_macros_expand_and_reach_the_record() -> None:
    db = parse_bib("@article{k, title={T}, month = jul, year = 2020}")
    assert db.by_key("k").get("month") == "July"
    assert db.by_key("k").to_record().issued == CSLDate(year=2020, month=7)


def test_unknown_macro_stays_literal_and_is_reported() -> None:
    """An undefined macro keeps the M1 behavior (literal text), but is never silent."""
    db = parse_bib("@article{k, title={T}, journal = nosuchmacro, year = 2020}")
    assert db.by_key("k").get("journal") == "nosuchmacro"
    assert db.unresolved_macros == ["nosuchmacro"]
    assert "2020" not in db.unresolved_macros  # a bare number is not a macro


# ---------------------------------------------------------------------------
# LaTeX decoding and names
# ---------------------------------------------------------------------------


def test_latex_accents_and_escapes_decode() -> None:
    assert latex_to_text("Ant{\\^o}nio") == "Antônio"
    assert latex_to_text("Erd{\\H o}s") == "Erdős"
    assert latex_to_text("Sk{\\l}odowska") == "Skłodowska"
    assert latex_to_text("{\\'E}tudes") == "Études"
    assert latex_to_text("Deep Learning \\& Signal") == "Deep Learning & Signal"
    assert latex_to_text("Stra{\\ss}e") == "Straße"
    assert latex_to_text("A {Protected} Title") == "A Protected Title"
    assert latex_to_text("100\\% of the \\{set\\}") == "100% of the {set}"


def test_record_from_accented_entry_is_comparable(torture: BibDatabase) -> None:
    """The CSL projection is the form that gets compared against index metadata."""
    record = entry_to_record(torture.by_key("accents2018"))
    assert record.title == "Deep Learning & Signal Processing for the Erdős Problem"
    assert record.first_author_surname == "Ribeiro"
    assert record.author[0].given == "Antônio H."
    assert record.author[1].surname == "Skłodowska"
    assert record.author[2].surname == "others"  # BibTeX's et al. marker, not dropped
    assert record.container_title == "Journal of Études"


def test_names_split_on_and_at_brace_depth_zero() -> None:
    assert split_bibtex_names("Doe, Jane and Roe, Richard") == ["Doe, Jane", "Roe, Richard"]
    # A corporate name is ONE name: splitting it would invent an author.
    assert split_bibtex_names("{Barnes and Noble}, Inc. and Doe, Jane") == [
        "{Barnes and Noble}, Inc.",
        "Doe, Jane",
    ]
    assert split_bibtex_names("Anderson, Ann") == ["Anderson, Ann"]  # 'and' inside a word
    assert split_bibtex_names("") == []


def test_braced_corporate_author_stays_literal(torture: BibDatabase) -> None:
    record = entry_to_record(torture.by_key("corporate2017"))
    assert record.author == [CSLName(literal="World Health Organization")]
    assert record.first_author_surname == "World Health Organization"
    assert record.publisher == "World Health Organization"  # from institution


# ---------------------------------------------------------------------------
# 3. Emission and the round-trip fixed point
# ---------------------------------------------------------------------------


def test_round_trip_is_a_fixed_point(torture: BibDatabase) -> None:
    """parse -> emit -> parse is stable, in the data AND in the bytes."""
    once = emit_bib(torture)
    reparsed = parse_bib(once)

    assert reparsed.entries == torture.entries
    assert reparsed.strings == torture.strings
    assert reparsed.preambles == torture.preambles
    assert reparsed.comments == torture.comments
    assert reparsed == torture

    twice = emit_bib(reparsed)
    assert twice == once  # byte-identical: emission is deterministic


def test_round_trip_of_the_citation_audit_example() -> None:
    example = FIXTURES / "citation-audit.md"
    blocks = re.findall(r"```[a-z]*\n(.*?)```", example.read_text(encoding="utf-8"), re.DOTALL)
    bib_block = next(b for b in blocks if "@article" in b)

    db = parse_bib(bib_block)
    reparsed = parse_bib(emit_bib(db))
    assert reparsed.entries == db.entries
    assert emit_bib(reparsed) == emit_bib(db)


def test_emitted_entry_shape() -> None:
    entry = BibEntry(
        "article",
        "doe2020",
        {"year": "2020", "title": "A Title", "author": "Doe, Jane", "doi": "10.1/x"},
    )
    assert emit_entry(entry) == (
        "@article{doe2020,\n"
        "  author = {Doe, Jane},\n"
        "  title = {A Title},\n"
        "  year = {2020},\n"
        "  doi = {10.1/x}\n"
        "}"
    )


def test_emission_field_order_is_deterministic_for_unknown_fields() -> None:
    """Fields outside FIELD_ORDER are emitted alphabetically, so output never wobbles."""
    entry = BibEntry("misc", "k", {"zeta": "1", "alpha": "2", "title": "T"})
    body = [line.strip() for line in emit_entry(entry).splitlines()[1:-1]]
    assert body == ["title = {T},", "alpha = {2},", "zeta = {1}"]


def test_emit_from_csl_records() -> None:
    """bib.py is an EMITTER over the canonical model (D4), not just a reader."""
    record = CSLRecord(
        type="paper-conference",
        title="Attention Is All You Need",
        author=[CSLName(family="Vaswani", given="Ashish"), CSLName(family="Shazeer", given="Noam")],
        issued=CSLDate(year=2017),
        container_title="Advances in Neural Information Processing Systems",
        DOI="10.5555/3295222.3295349",
        page="5998-6008",
    )
    text = emit_bib([record])
    assert text.startswith("@inproceedings{vaswani2017attention,")

    back = parse_bib(text).entries[0]
    assert back.entry_type == "inproceedings"
    assert back.key == "vaswani2017attention"
    assert back.get("author") == "Vaswani, Ashish and Shazeer, Noam"
    assert back.get("booktitle") == "Advances in Neural Information Processing Systems"
    assert back.get("pages") == "5998--6008"  # CSL page ranges become BibTeX en-dashes
    assert back.get("doi") == "10.5555/3295222.3295349"

    # And the emitted entry re-projects onto an equal record.
    assert back.to_record(keep_source=False).title == record.title


def test_emitted_record_key_is_generated_when_the_id_is_a_doi() -> None:
    record = CSLRecord(
        title="Self-supervised representation learning from 12-lead ECG data",
        author=[CSLName(family="Mehari", given="Temesgen")],
        issued=CSLDate(year=2022),
        DOI="10.1016/j.compbiomed.2021.105114",
    )
    assert record.id == "10.1016/j.compbiomed.2021.105114"  # not a legal citation key
    assert citation_key_for(record) == "mehari2022selfsupervised"
    assert record_to_entry(record).key == "mehari2022selfsupervised"


def test_corporate_author_survives_emission() -> None:
    record = CSLRecord(
        title="Global Report",
        author=[CSLName(literal="World Health Organization")],
        issued=CSLDate(year=2017),
    )
    entry = record_to_entry(record)
    assert entry.get("author") == "{World Health Organization}"
    # Re-reading must not split it back into a given/family pair.
    reread = parse_bib(emit_bib([entry])).entries[0]
    assert reread.to_record(keep_source=False).author == [
        CSLName(literal="World Health Organization")
    ]


def test_bib_to_csl_to_bib_is_lossless(torture: BibDatabase) -> None:
    """The CSL projection cleans values (that is the point), so the raw entry rides along
    under custom.bibtex and is restored verbatim. Even brace protection survives."""
    for entry in torture:
        record = entry_to_record(entry)
        assert record_to_entry(record) == entry
    assert emit_bib([entry_to_record(e) for e in torture.entries]) == emit_bib(torture.entries)


def test_csl_projection_carries_the_raw_entry() -> None:
    entry = parse_bib("@article{k, title={A {Deep} Title}, year=2020}").entries[0]
    record = entry_to_record(entry)
    assert record.title == "A Deep Title"  # cleaned: comparable against an index
    assert record.extra["bibtex"]["fields"]["title"] == "A {Deep} Title"  # raw: reversible
    assert entry_to_record(entry, keep_source=False).extra == {}


def test_issn_and_keywords_ride_under_custom_not_in_the_csl_variables() -> None:
    """record.schema.json types ISSN and keyword as string-or-number (as upstream CSL does),
    while model.py types them as lists and serializes JSON arrays. Emitting either from a
    .bib would produce a record the contract rejects, so bib.py keeps both out of the
    standard namespace and carries them, losslessly, under custom.bibtex instead."""
    entry = parse_bib(
        "@article{k, title={T}, year=2020, issn={1234-5678}, keywords={a, b}}"
    ).entries[0]

    record = entry_to_record(entry)
    csl = record.to_csl_json()
    assert "ISSN" not in csl
    assert "keyword" not in csl
    assert csl["custom"]["bibtex"]["fields"]["issn"] == "1234-5678"
    assert csl["custom"]["bibtex"]["fields"]["keywords"] == "a, b"

    # Without the raw entry they still survive, under the leftover-fields slot.
    lean = entry_to_record(entry, keep_source=False)
    assert lean.extra["bibtex_extra_fields"] == {"issn": "1234-5678", "keywords": "a, b"}
    # And nothing is lost on the way back out.
    assert record_to_entry(record) == entry


def test_records_from_bib_is_the_verification_entry_point() -> None:
    records = records_from_bib(COMPACT_SAMPLE)
    assert [r.id for r in records] == ["mehari2022ssl", "sarkar2022ssl"]
    assert records[1].DOI == "10.1109/taffc.2020.3014842"
    assert records[1].issue == "3"  # BibTeX 'number' is the journal issue for an article
    assert records[1].page == "1541-1554"
    assert records[1].year == 2022
    assert records[1].first_author_surname == "Sarkar"


def test_emit_of_an_empty_database_is_empty() -> None:
    assert emit_bib(BibDatabase()) == ""
    assert emit_bib([]) == ""


def test_emit_can_drop_the_non_entry_blocks(torture: BibDatabase) -> None:
    text = emit_bib(torture, include_preamble=False, include_strings=False, include_comments=False)
    assert "@preamble" not in text
    assert "@string" not in text
    assert "@comment" not in text
    assert len(parse_bib(text)) == len(torture)


# ---------------------------------------------------------------------------
# Malformed input: tolerant by default, strict on request
# ---------------------------------------------------------------------------


def test_a_bad_entry_does_not_cost_the_good_ones() -> None:
    db = parse_bib(
        "@article{good1, title={Fine}, year={2020}}\n"
        "@article{bad, title=, year={2021}}\n"
        "@article{good2, title={Also fine}, year={2022}}\n"
    )
    assert db.keys() == ["good1", "bad", "good2"]
    assert db.by_key("good2").get("title") == "Also fine"


def test_strict_mode_raises_on_a_malformed_entry() -> None:
    text = "@article{bad, title {No equals sign}, year={2021}}"
    assert parse_bib(text).problems  # tolerant: recorded, not raised
    with pytest.raises(BibParseError):
        parse_bib(text, strict=True)


def test_unterminated_entry_is_reported_not_hung() -> None:
    db = parse_bib("@article{cut, title={Never closed}, year={2020}")
    assert db.keys() == ["cut"]
    assert any("unterminated" in problem for problem in db.problems)


def test_emission_never_produces_unparseable_bibtex() -> None:
    """A value with unmatched braces cannot exist in BibTeX (it would swallow the entry),
    so emission drops the unmatched ones rather than writing a file that will not parse."""
    entry = BibEntry("article", "k", {"title": "Broken { open", "year": "2020"})
    text = emit_bib([entry])
    reparsed = parse_bib(text)
    assert reparsed.problems == []
    assert reparsed.entries[0].get("title") == "Broken open"
    assert reparsed.entries[0].get("year") == "2020"  # the next field was not swallowed


def test_parse_accepts_text_a_path_and_a_path_string() -> None:
    from_path = parse_bib(TORTURE)
    from_string = parse_bib(str(TORTURE))
    from_text = parse_bib(TORTURE.read_text(encoding="utf-8"))
    assert from_path.entries == from_text.entries
    assert from_string.entries == from_text.entries


# ---------------------------------------------------------------------------
# The schema is the contract
# ---------------------------------------------------------------------------


def test_every_torture_record_validates_against_record_schema(torture: BibDatabase) -> None:
    """Whatever bib.py hands the rest of the kernel must be a valid CSLRecord (D4).

    jsonschema is a DEV dependency: schema validation is a test-time concern, never a
    runtime import.
    """
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (FIXTURES / "record.schema.json").read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema)
    for record in torture.records():
        errors = sorted(validator.iter_errors(record.to_csl_json()), key=str)
        assert not errors, f"{record.id}: {[e.message for e in errors]}"
