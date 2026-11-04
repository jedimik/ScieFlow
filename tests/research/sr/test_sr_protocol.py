"""M-H t2 (plan 5.2): the locked protocol. require_locked refuses every unlocked, modified, broken or
non-systematic-review state with its message; a locked protocol returns version, hash and review.
Synthetic data only; the lock is approved through the service hook, as in tests/core/design."""

import shutil
from pathlib import Path

import pytest
import yaml

from scieflow.core import service
from scieflow.core.design import lock
from scieflow.research.search import registry
from scieflow.research.sr import protocol
from scieflow.research.sr.errors import SRError

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "design"


def _approve(proj, ws):
    gate = lock.lock(proj, ws)
    service.answer_gate(proj, "r1", gate["id"], "approve")
    return gate


def _edit(ws, mutate):
    path = ws / "design" / "design.yml"
    design = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(design)
    path.write_text(yaml.safe_dump(design, sort_keys=False), encoding="utf-8")


def test_no_design_is_refused_with_the_lock_hint(srproj, srws):
    (srws / "design" / "design.yml").unlink()
    with pytest.raises(SRError, match="lock the protocol first: scieflow design lock r1"):
        protocol.require_locked(srproj, srws)


def test_a_draft_is_refused_with_the_lock_hint(srproj, srws):
    with pytest.raises(SRError, match="lock the protocol first"):
        protocol.require_locked(srproj, srws)


def test_a_pending_gate_is_refused_with_the_lock_hint(srproj, srws):
    lock.lock(srproj, srws)
    with pytest.raises(SRError, match="lock the protocol first"):
        protocol.require_locked(srproj, srws)


def test_a_modified_protocol_is_refused_with_the_amend_hint(srproj, srws):
    _approve(srproj, srws)
    _edit(srws, lambda d: d["review"]["eligibility"]["include"][0].update(text="Changed text"))
    with pytest.raises(SRError, match="differs from its lock.*scieflow design amend"):
        protocol.require_locked(srproj, srws)


def test_another_study_type_is_refused(srproj, srws):
    shutil.copy(FIXTURES / "rct.yml", srws / "design" / "design.yml")
    (srws / "design" / "power").mkdir()
    shutil.copy(FIXTURES / "power-rct-ttest.json", srws / "design" / "power" / "rct-ttest.json")
    _approve(srproj, srws)
    with pytest.raises(SRError, match="study type is rct, not systematic-review"):
        protocol.require_locked(srproj, srws)


def test_a_broken_ledger_is_refused_with_the_problem(srproj, srws):
    _approve(srproj, srws)
    (srws / "design" / "lock.jsonl").write_text("not json\n", encoding="utf-8")
    with pytest.raises(SRError, match="line 1 is not JSON"):
        protocol.require_locked(srproj, srws)


def test_a_locked_protocol_returns_version_hash_and_review(srproj, srws):
    _approve(srproj, srws)
    locked = protocol.require_locked(srproj, srws)
    assert locked.version == 1
    assert locked.content_hash == lock.state(srproj, srws).in_force_hash
    assert locked.review["screening"]["batch_size"] == 50
    assert [c["id"] for c in locked.criteria_include] == ["i1"]
    assert [c["id"] for c in locked.criteria_exclude] == ["e1", "e2"]


def test_protocol_show_returns_version_hash_and_the_review_block(srproj, srws):
    _approve(srproj, srws)
    doc = protocol.show(srproj, srws)
    assert doc["version"] == 1
    assert doc["content_hash"]
    assert doc["review"]["eligibility"]["exclude"][1]["id"] == "e2"


def test_protocol_check_reports_a_refused_query_and_sends_nothing(srproj, srws, monkeypatch):
    _approve(srproj, srws)
    checked_queries = []

    def fake_check(query):
        checked_queries.append(query)
        raise registry.LeakError("refused: test leak rule")

    monkeypatch.setattr(registry, "check_query", fake_check)
    doc = protocol.check(srproj, srws)
    assert checked_queries == ["synthetic protocol query"]
    assert doc["strategies"] == [{"id": "s1", "status": "refused", "reason": "refused: test leak rule"}]
