"""M-C1 t4 (plan 5.3, item 6): query repair before the leak check.

A clean query is unchanged byte for byte. A query with repairable syntax damage (controls, a
backslash, an unbalanced bracket or quote) is repaired, then checked. A query with nothing left
that can be sent is refused (exit 3). Repair never launders a data-shaped query. Synthetic data.
"""

from __future__ import annotations

import pytest

from scieflow.core.review.claims import LeakError
from scieflow.research.lib import providers
from scieflow.research.search import registry

CLEAN = [
    "SNCA AND (RBD OR iRBD)",
    "parkinson*",
    "type 2 diabetes",
    "alpha-synuclein A53T",
    "COVID-19 vaccine",
]


@pytest.mark.parametrize("query", CLEAN)
def test_clean_queries_are_unchanged_byte_for_byte(query):
    assert registry.repair_query(query) == query
    checked = registry.check_query(query)
    assert isinstance(checked, providers.Checked)
    assert str(checked) == query


@pytest.mark.parametrize("raw, repaired", [
    ('"(The LOOP Stu', "The LOOP Stu"),          # stray quote and unbalanced bracket
    ("SNCA\\", "SNCA"),                          # trailing backslash
    ("SNCA\nRBD", "SNCA RBD"),                   # embedded newline
    ("tau\tprotein", "tau protein"),             # tab (C0)
    ("tau\x00protein", "tau protein"),           # NUL (C0)
    ("tau\x7fprotein", "tau protein"),           # DEL
    ("tau\x9bprotein", "tau protein"),           # C1 control (CSI)
    ("SNCA (RBD", "SNCA RBD"),                   # open bracket with no partner
    ("RBD) OR iRBD", "RBD OR iRBD"),             # close bracket with no partner
    ('"alpha synuclein', "alpha synuclein"),     # odd quote
])
def test_repairable_queries_are_repaired_then_checked(raw, repaired):
    checked = registry.check_query(raw)
    assert isinstance(checked, providers.Checked)
    assert str(checked) == repaired


@pytest.mark.parametrize("raw", ['"()"', "()", "(( ))", "\\\\", '"', "   ", "\n\t"])
def test_queries_with_nothing_left_are_refused(raw):
    with pytest.raises(LeakError, match="nothing left that can be sent"):
        registry.check_query(raw)


@pytest.mark.parametrize("raw", [
    "patient 4711 age 63",
    "cohort: RBD043",
    "(patient 4711 age 63)",
    "patient 4711 age 63\\",
    "jane.doe@example.org\n",
    "patient 4 7 1 1 age 6 3",
])
def test_repair_never_launders_a_data_shaped_query(raw):
    with pytest.raises(LeakError):
        registry.check_query(raw)


def test_non_string_is_refused_as_nothing_to_send():
    with pytest.raises(LeakError, match="nothing left"):
        registry.check_query(None)
