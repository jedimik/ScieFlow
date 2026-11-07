"""T-9 t1: the constants of core/archives.py equal the committed B2 spec table (B2:5.13).

The spec table is parsed here, not copied, so the spec stays the single source.
"""

import math
import re
from decimal import Decimal
from pathlib import Path

from scieflow.core import archives as a
from scieflow.web import files

SPEC = (Path(__file__).resolve().parents[2] / "docs" / "superpowers" / "specs"
        / "2026-11-03-b2-archived-run-browsing-design.md")

ROW = re.compile(r"^\| `([A-Z0-9_]+)` \| ([0-9.]+) \| ")


def _spec_table() -> dict[str, float]:
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("### 5.13 Constants", 1)[1].split("### 5.14", 1)[0]
    rows = {}
    for line in section.splitlines():
        match = ROW.match(line)
        if match:
            rows[match.group(1)] = float(match.group(2)) if "." in match.group(2) else int(match.group(2))
    return rows


def test_spec_table_has_the_sixteen_names_and_each_equals_the_module_attribute():
    table = _spec_table()
    assert len(table) == 16
    for name, value in table.items():
        assert getattr(a, name) == value, name
        assert type(getattr(a, name)) is type(value), name


def test_state_subdirectory_and_job_kinds():
    assert a.ARCHIVE_STATE == "archives"
    assert a.JOB_KINDS == ("archive-fetch", "archive-restore")


def test_inline_bound_is_the_web_inline_bound():
    assert a.INLINE_MAX_BYTES == files.MAX_INLINE


def test_disk_headroom_is_exact_decimal_so_no_float_error():
    # 1000 * 1.05 is 1050 exactly; a float ceil could make it 1051.
    assert a.disk_needed(1000) == 1050 + a.DISK_RESERVE_BYTES
    assert a.disk_needed(1001) == math.ceil(Decimal("1001") * Decimal("1.05")) + a.DISK_RESERVE_BYTES
    assert a.disk_needed(0) == a.DISK_RESERVE_BYTES
