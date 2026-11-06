"""T-8 t1: the B2 archived-run browsing design document is complete and matches its plan.

Hermetic: reads only two repository files (the committed plan copy and the spec) and
imports one constant from scieflow.web.files. No network, no real home, no run layout.
"""

import re
from pathlib import Path

from scieflow.web import files as web_files

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs/superpowers/plans/2026-11-03-t-8-archived-run-browsing.md"
SPEC = ROOT / "docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md"

HEADER = (
    "# B2 archived-run browsing — design (Ticket 8; Part A of "
    "docs/superpowers/plans/2026-11-03-t-8-archived-run-browsing.md)"
)
PART_A = "\n## Part A — Spec\n"
PART_B = "\n## Part B — Implementation plan\n"
APPENDIX = "\n## Appendix — line references on base "
MANUSCRIPT = (
    "Manuscript history is not kept in archives. It is rebuilt from the run's drafts "
    "after you restore the run: open its drafts page, or complete a merge round."
)
CONSTANTS = {
    "CONSENT_BYTES": 1073741824,
    "DISK_HEADROOM": 1.05,
    "DISK_RESERVE_BYTES": 1073741824,
    "LIST_CAP": 500,
    "PAGE_ENTRIES": 500,
    "MAX_ARCHIVE_ENTRIES": 100000,
    "MAX_CENTRAL_DIRECTORY_BYTES": 67108864,
    "POINTER_MAX_BYTES": 65536,
    "RECORD_MAX_BYTES": 4096,
    "CONTROL_MAX_BYTES": 262144,
    "INLINE_MAX_BYTES": 200000,
    "INDEX_CACHE": 4,
    "DVC_OUTPUT_TAIL": 65536,
    "MD5_CHUNK_BYTES": 1048576,
    "JOB_RECORD_MAX_BYTES": 65536,
    "LOCKED_EXIT": 3,
}
REASON_CODES = [
    "dvc-missing", "no-remote", "no-credentials", "credentials-refused",
    "remote-unreachable", "missing-on-remote", "disk-full", "interrupted",
    "verify-size", "verify-md5", "locked", "dvc-failed",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str, next_heading: str) -> str:
    start = text.index(heading)
    return text[start:text.index(next_heading, start)]


def _constants_table() -> dict:
    section = _section(_read(SPEC), "### 5.13 ", "### 5.14 ")
    rows = re.findall(r"^\| `([A-Z0-9_]+)` \| ([0-9.]+) \|", section, re.M)
    return {name: float(v) if "." in v else int(v) for name, v in rows}


def test_spec_exists_with_exact_header():
    assert SPEC.is_file(), f"missing {SPEC.relative_to(ROOT)}"
    lines = _read(SPEC).split("\n")
    assert lines[0] == HEADER
    assert lines[1] == ""


def test_part_a_is_byte_equal_to_the_plan_copy():
    plan = "\n" + _read(PLAN)
    spec = "\n" + _read(SPEC)
    plan_block = plan[plan.index(PART_A):plan.index(PART_B)]
    spec_block = spec[spec.index(PART_A):spec.index(APPENDIX)]
    assert plan_block.startswith(PART_A) and len(plan_block) > 10000
    assert spec_block == plan_block


def test_spec_has_sections_5_1_to_5_16_in_order_and_the_appendix():
    spec = _read(SPEC)
    headings = re.findall(r"^### (5\.\d+) ", spec, re.M)
    assert headings == [f"5.{i}" for i in range(1, 17)]
    appendix = re.search(r"^## Appendix — line references on base [0-9a-f]{40}$", spec, re.M)
    assert appendix is not None
    assert appendix.start() > spec.index("### 5.16 ")


def test_constants_table_is_exact():
    assert _constants_table() == CONSTANTS
    assert len(_constants_table()) == 16


def test_inline_max_bytes_equals_web_files_max_inline():
    assert _constants_table()["INLINE_MAX_BYTES"] == web_files.MAX_INLINE == 200000


def test_manuscript_sentence_is_exact():
    spec = " ".join(_read(SPEC).split())
    assert MANUSCRIPT in spec


def test_failure_reason_table_lists_the_twelve_codes_once_each():
    section = _section(_read(SPEC), "### 5.9 ", "### 5.10 ")
    codes = re.findall(r"^\| `([a-z0-9-]+)` \|", section, re.M)
    assert codes == REASON_CODES
    assert len(set(codes)) == len(codes) == 12


def test_spec_has_no_open_markers():
    assert re.search(r"\b(TBD|TODO|FIXME)\b", _read(SPEC)) is None
