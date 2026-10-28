"""M-D1 t4 (plan 5.5, 5.7, 5.9): lock, amend, the ledger, state and the CLI. Synthetic data only."""

from __future__ import annotations

import json
import os
import shutil

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import gates, service
from scieflow.core.design import cli as design_cli
from scieflow.core.design import lock, model


def edit_design(ws, mutate) -> None:
    path = ws / "design" / "design.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))


def gate_file(ws, gate_id):
    return ws / "gates" / f"{gate_id}.json"


def read_gate(ws, gate_id) -> dict:
    return json.loads(gate_file(ws, gate_id).read_text())


def write_gate(ws, gate: dict) -> None:
    gate_file(ws, gate["id"]).write_text(json.dumps(gate, indent=2))


def ledger_records(ws) -> list[dict]:
    text = (ws / "design" / "lock.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_ledger(ws, records: list[dict]) -> None:
    (ws / "design" / "lock.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))


def locked(proj, ws):
    gate = lock.lock(proj, ws)
    service.answer_gate(proj, "r1", gate["id"], "approve")
    return gate


# --- lock -----------------------------------------------------------------------------------

def test_lock_opens_a_human_only_gate_and_writes_pending(lproj, lws):
    gate = lock.lock(lproj, lws)
    assert gate["kind"] == "design-lock" and gate["requires_human"] is True
    assert gate["options"] == ["approve", "reject"] and gate["state"] == "open"
    pending = json.loads((lws / "design" / "pending.json").read_text())
    assert pending["gate"] == gate["id"] and pending["kind"] == "lock"
    assert pending["content_hash"][:12] in gate["question"]
    assert lock.state(lproj, lws).status == "pending"


def test_human_approval_locks_the_design_and_its_power_file(lproj, lws):
    gate = lock.lock(lproj, lws)
    expected = model.content_hash({
        "design": yaml.safe_load((lws / "design" / "design.yml").read_text()),
        "power": json.loads((lws / "design" / "power" / "rct-ttest.json").read_text()),
    })
    service.answer_gate(lproj, "r1", gate["id"], "approve")
    records = ledger_records(lws)
    assert len(records) == 1
    rec = records[0]
    assert (rec["seq"], rec["kind"], rec["version"]) == (1, "lock", "1.0")
    assert rec["content_hash"] == expected
    assert (rec["previous_hash"], rec["previous_version"]) == ("", "")
    assert rec["gate"] == gate["id"] and rec["approved_by"] == "human"
    assert rec["copy"] == "design/locked/v1" and "charter_version" in rec
    copy = lws / "design" / "locked" / "v1"
    assert (copy / "design.yml").read_bytes() == (lws / "design" / "design.yml").read_bytes()
    assert (copy / "power.json").is_file()
    assert not (lws / "design" / "pending.json").exists()
    st = lock.state(lproj, lws)
    assert st.status == "locked" and st.version == "1.0" and st.current_hash == expected
    assert lock.verify_ledger(lws) is None


def test_an_agent_cannot_lock_or_approve_a_design(lproj, lws):
    gate = lock.lock(lproj, lws)
    with pytest.raises(gates.GateError, match="human answer"):
        gates.answer(lproj, lws, gate["id"], "approve", actor="agent", rationale="looks fine")
    with pytest.raises(gates.GateError):
        lock.on_answer(lproj, lws, gate, "approve", "agent")
    assert not (lws / "design" / "lock.jsonl").exists()
    assert read_gate(lws, gate["id"])["state"] == "open"


def test_an_edit_after_the_gate_opened_keeps_it_open_and_refuses_approval(lproj, lws):
    gate = lock.lock(lproj, lws)
    edit_design(lws, lambda d: d.update(notes="Synthetic edit after the gate opened."))
    with pytest.raises(service.ServiceError, match="changed since the gate opened"):
        service.answer_gate(lproj, "r1", gate["id"], "approve")
    assert read_gate(lws, gate["id"])["state"] == "open"
    assert not (lws / "design" / "lock.jsonl").exists()
    service.answer_gate(lproj, "r1", gate["id"], "reject")
    assert not (lws / "design" / "pending.json").exists()
    assert lock.state(lproj, lws).status == "draft"


def test_a_second_lock_is_refused_with_the_amend_hint(lproj, lws):
    locked(lproj, lws)
    with pytest.raises(lock.LockError, match="scieflow design amend"):
        lock.lock(lproj, lws)


def test_a_design_with_errors_cannot_be_locked(lproj, lws):
    edit_design(lws, lambda d: d.update(question="TODO: fill the question"))
    with pytest.raises(lock.LockError, match="todo"):
        lock.lock(lproj, lws)
    assert not (lws / "design" / "pending.json").exists()
    gates_dir = lws / "gates"
    assert not gates_dir.exists() or not list(gates_dir.glob("*.json"))


def test_a_second_lock_while_its_gate_is_open_is_refused(lproj, lws):
    gate = lock.lock(lproj, lws)
    with pytest.raises(lock.LockError, match=gate["id"]):
        lock.lock(lproj, lws)


@pytest.mark.parametrize("how", ["withdrawn", "missing"])
def test_a_stale_pending_gate_is_cleared_by_the_next_lock(lproj, lws, how):
    first = lock.lock(lproj, lws)
    if how == "withdrawn":
        stale = read_gate(lws, first["id"])
        stale["state"] = "withdrawn"
        write_gate(lws, stale)
    else:
        gate_file(lws, first["id"]).unlink()
    second = lock.lock(lproj, lws)
    assert second["id"] != first["id"] and second["state"] == "open"
    assert json.loads((lws / "design" / "pending.json").read_text())["gate"] == second["id"]


# --- amend ----------------------------------------------------------------------------------

def test_amend_without_a_lock_is_refused(lproj, lws):
    with pytest.raises(lock.LockError, match="not locked"):
        lock.amend(lproj, lws, "change notes", "why")


def test_amend_of_an_unchanged_design_is_refused(lproj, lws):
    locked(lproj, lws)
    with pytest.raises(lock.LockError, match="nothing to amend"):
        lock.amend(lproj, lws, "summary", "rationale")


@pytest.mark.parametrize("summary,rationale", [("", "why"), ("change", "   ")])
def test_amend_needs_a_summary_and_a_rationale(lproj, lws, summary, rationale):
    locked(lproj, lws)
    edit_design(lws, lambda d: d.update(notes="Synthetic changed note."))
    with pytest.raises(lock.LockError, match="summary and a rationale"):
        lock.amend(lproj, lws, summary, rationale)


def test_an_amendment_is_a_diff_gate_and_approval_appends_version_2(lproj, lws):
    locked(lproj, lws)
    v1_hash = lock.state(lproj, lws).in_force_hash
    edit_design(lws, lambda d: d.update(notes="Synthetic amended note."))
    gate = lock.amend(lproj, lws, "Clarify the note", "Reviewer asked for it")
    assert gate["kind"] == "design-lock" and gate["state"] == "open"
    assert "to version 2.0" in gate["question"]
    diff = (lws / "design" / "amendments" / "v2.diff").read_text()
    assert "Synthetic amended note." in diff
    pending = json.loads((lws / "design" / "pending.json").read_text())
    assert pending["kind"] == "amendment" and pending["gate"] == gate["id"]
    service.answer_gate(lproj, "r1", gate["id"], "approve")
    records = ledger_records(lws)
    assert len(records) == 2
    rec = records[1]
    assert (rec["seq"], rec["kind"], rec["version"]) == (2, "amendment", "2.0")
    assert rec["previous_hash"] == v1_hash and rec["previous_version"] == "1.0"
    assert rec["summary"] == "Clarify the note" and rec["rationale"] == "Reviewer asked for it"
    assert rec["copy"] == "design/locked/v2"
    st = lock.state(lproj, lws)
    assert st.status == "locked" and st.version == "2.0"
    assert st.in_force_hash == rec["content_hash"] and len(st.trail) == 2
    assert "Synthetic amended note." in (lws / "design" / "locked" / "v2" / "design.yml").read_text()
    assert "Synthetic amended note." not in (lws / "design" / "locked" / "v1" / "design.yml").read_text()
    assert lock.verify_ledger(lws) is None


def test_a_second_amendment_cannot_open_while_one_is_pending(lproj, lws):
    locked(lproj, lws)
    edit_design(lws, lambda d: d.update(notes="Synthetic changed note."))
    first = lock.amend(lproj, lws, "one", "reason")
    with pytest.raises(lock.LockError, match=first["id"]):
        lock.amend(lproj, lws, "two", "reason")


# --- ledger verification (tamper detection, fail closed) ------------------------------------

def _tamper_record(field, value):
    def apply(lws):
        records = ledger_records(lws)
        records[0][field] = value
        write_ledger(lws, records)
    return apply


def _tamper_gate(field, value):
    def apply(lws):
        gate = read_gate(lws, ledger_records(lws)[0]["gate"])
        gate[field] = value
        write_gate(lws, gate)
    return apply


def _remove_gate(lws):
    gate_file(lws, ledger_records(lws)[0]["gate"]).unlink()


def _remove_copy(lws):
    shutil.rmtree(lws / "design" / "locked" / "v1")


def _edit_copy(lws):
    """Change a value of the locked copy (the title, not the comment above it: comments are not content)."""
    path = lws / "design" / "locked" / "v1" / "design.yml"
    path.write_text(path.read_text().replace("Synthetic two-arm", "Changed two-arm", 1))


def _symlink_ledger(lws):
    real = lws / "design" / "real.jsonl"
    (lws / "design" / "lock.jsonl").rename(real)
    (lws / "design" / "lock.jsonl").symlink_to(real)


def _not_json(lws):
    (lws / "design" / "lock.jsonl").write_text("not json\n")


def _extra_key(lws):
    records = ledger_records(lws)
    records[0]["extra"] = "x"
    write_ledger(lws, records)


TAMPERS = {
    "edited-content-hash": _tamper_record("content_hash", "0" * 64),
    "wrong-version": _tamper_record("version", "9.0"),
    "seq-gap": _tamper_record("seq", 2),
    "approved-by-agent": _tamper_record("approved_by", "agent"),
    "copy-path-moved": _tamper_record("copy", "design/locked/v9"),
    "extra-key": _extra_key,
    "not-json": _not_json,
    "symlinked-ledger": _symlink_ledger,
    "missing-copy": _remove_copy,
    "edited-copy": _edit_copy,
    "gate-deleted": _remove_gate,
    "gate-rejected": _tamper_gate("answer", "reject"),
    "gate-answered-by-agent": _tamper_gate("answered_by", "agent"),
    "gate-wrong-kind": _tamper_gate("kind", "scope-change"),
}


@pytest.mark.parametrize("name", sorted(TAMPERS))
def test_a_tampered_ledger_is_invalid_and_fails_closed(lproj, lws, name):
    locked(lproj, lws)
    assert lock.verify_ledger(lws) is None
    TAMPERS[name](lws)
    assert lock.verify_ledger(lws) is not None
    assert lock.state(lproj, lws).status == "ledger-invalid"


def test_a_removed_first_record_breaks_the_chain(lproj, lws):
    locked(lproj, lws)
    edit_design(lws, lambda d: d.update(notes="Synthetic changed note."))
    gate = lock.amend(lproj, lws, "one", "reason")
    service.answer_gate(lproj, "r1", gate["id"], "approve")
    records = ledger_records(lws)
    write_ledger(lws, records[1:])
    assert lock.state(lproj, lws).status == "ledger-invalid"


def test_the_crash_window_is_invalid_until_a_human_approves_again_idempotently(lproj, lws):
    gate = locked(lproj, lws)
    stale = read_gate(lws, gate["id"])
    stale.update(state="open", answer=None, answered=None, answered_by=None)
    write_gate(lws, stale)
    assert lock.verify_ledger(lws) is not None
    service.answer_gate(lproj, "r1", gate["id"], "approve")
    assert len(ledger_records(lws)) == 1
    assert lock.verify_ledger(lws) is None and lock.state(lproj, lws).status == "locked"


def test_an_edit_to_the_locked_power_file_or_a_deleted_design_is_modified(lproj, lws):
    locked(lproj, lws)
    power = lws / "design" / "power" / "rct-ttest.json"
    doc = json.loads(power.read_text())
    doc["result"]["n1_required"] = 65
    power.write_text(json.dumps(doc))
    assert lock.state(lproj, lws).status == "modified"
    power.write_text(json.dumps(json.loads((lws / "design" / "locked" / "v1" / "power.json").read_text())))
    assert lock.state(lproj, lws).status == "locked"
    os.remove(lws / "design" / "design.yml")
    assert lock.state(lproj, lws).status == "modified"


# --- review r1 fixes (M-D1 t4): removed ledger lines, symlinked copies, stale copy folders --------

def test_a_removed_last_record_is_detected(lproj, lws):
    locked(lproj, lws)
    edit_design(lws, lambda d: d.update(notes="Synthetic changed note."))
    gate = lock.amend(lproj, lws, "one", "reason")
    service.answer_gate(lproj, "r1", gate["id"], "approve")
    assert lock.verify_ledger(lws) is None
    write_ledger(lws, ledger_records(lws)[:-1])
    assert lock.verify_ledger(lws) is not None
    assert lock.state(lproj, lws).status == "ledger-invalid"


def test_a_removed_only_record_is_detected(lproj, lws):
    locked(lproj, lws)
    write_ledger(lws, [])
    assert lock.verify_ledger(lws) is not None
    assert lock.state(lproj, lws).status == "ledger-invalid"


def test_an_emptied_ledger_is_invalid_and_cannot_be_locked_again(lproj, lws):
    locked(lproj, lws)
    (lws / "design" / "lock.jsonl").write_text("")
    assert lock.verify_ledger(lws) is not None
    assert lock.state(lproj, lws).status == "ledger-invalid"
    with pytest.raises(lock.LockError, match="does not verify"):
        lock.lock(lproj, lws)


def test_a_deleted_ledger_is_invalid_while_its_approved_gate_remains(lproj, lws):
    locked(lproj, lws)
    (lws / "design" / "lock.jsonl").unlink()
    assert lock.verify_ledger(lws) is not None
    assert lock.state(lproj, lws).status == "ledger-invalid"


def test_a_symlinked_locked_folder_is_refused_at_approval(lproj, lws, lroot):
    outside = lroot / "outside"
    outside.mkdir()
    (lws / "design" / "locked").symlink_to(outside, target_is_directory=True)
    gate = lock.lock(lproj, lws)
    with pytest.raises(service.ServiceError, match="symlink"):
        service.answer_gate(lproj, "r1", gate["id"], "approve")
    assert read_gate(lws, gate["id"])["state"] == "open"
    assert list(outside.iterdir()) == []
    assert not (lws / "design" / "lock.jsonl").exists()


def test_a_symlinked_locked_folder_after_a_lock_is_ledger_invalid(lproj, lws, lroot):
    locked(lproj, lws)
    real = lws / "design" / "locked"
    moved = lroot / "moved"
    real.rename(moved)
    real.symlink_to(moved, target_is_directory=True)
    assert lock.verify_ledger(lws) is not None
    assert lock.state(lproj, lws).status == "ledger-invalid"


def test_a_stale_copy_folder_is_replaced_not_reused(lproj, lws):
    edit_design(lws, lambda d: d.update(
        sample_size={"method": "fixed", "n": 60, "rationale": "Synthetic fixed size."}))
    stale = lws / "design" / "locked" / "v1"
    stale.mkdir(parents=True)
    (stale / "power.json").write_text(json.dumps({"stale": True}))
    (stale / "leftover.txt").write_text("left by an interrupted approval")
    gate = lock.lock(lproj, lws)
    service.answer_gate(lproj, "r1", gate["id"], "approve")
    assert sorted(p.name for p in stale.iterdir()) == ["design.yml"]
    assert lock.verify_ledger(lws) is None
    assert lock.state(lproj, lws).status == "locked"


def test_an_approval_retried_after_a_partial_copy_still_completes(lproj, lws):
    gate = lock.lock(lproj, lws)
    copy = lws / "design" / "locked" / "v1"
    copy.mkdir(parents=True)
    (copy / "design.yml").write_bytes((lws / "design" / "design.yml").read_bytes())
    (copy / "power.json").write_bytes((lws / "design" / "power" / "rct-ttest.json").read_bytes())
    service.answer_gate(lproj, "r1", gate["id"], "approve")
    assert len(ledger_records(lws)) == 1
    assert lock.verify_ledger(lws) is None


# --- CLI ------------------------------------------------------------------------------------

@pytest.fixture
def cli_root(lroot, monkeypatch):
    monkeypatch.setattr(design_cli, "_root", lambda: lroot)
    return lroot


def _cli(*args):
    return CliRunner().invoke(design_cli.design, list(args))


def test_cli_lock_show_and_the_refusals(cli_root, lws):
    first = _cli("lock", "r1")
    assert first.exit_code == 0, first.output
    again = _cli("lock", "r1")
    assert again.exit_code == 1 and "answer or reject" in again.output
    shown = json.loads(_cli("show", "r1", "--json").output)
    assert shown["status"] == "pending" and shown["pending_gate"]
    assert _cli("amend", "r1", "--summary", "x").exit_code == 2     # --rationale is required
    refused = _cli("amend", "r1", "--summary", "x", "--rationale", "y")
    assert refused.exit_code == 1 and "not locked" in refused.output


def test_cli_init_force_is_refused_once_a_ledger_exists(cli_root, lws, lproj):
    locked(lproj, lws)
    refused = _cli("init", "r1", "--type", "rct", "--force")
    assert refused.exit_code == 1 and "locked" in refused.output
    shown = json.loads(_cli("show", "r1", "--json").output)
    assert shown["status"] == "locked" and shown["version"] == "1.0"
    assert shown["in_force_hash"] and shown["trail"][0]["seq"] == 1
