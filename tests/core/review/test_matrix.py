"""The review matrix (ticket 11, plan T2.3): `core/review/matrix.py` and
`scieflow review matrix`.

Core builds the skeleton mechanically — every comment of every valid pass plus every
bundle flag — and the coordinator clusters it into review/round-N/matrix.json. The
check validates the filled matrix against the schema and the skeleton (each comment
and flag exactly once, blockers only from blocker flags, quotes verbatim from the
bundle), then renders a deterministic matrix.md. With `scite: off` there is no
bundle and no pass B: the evidence columns say `off`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.project import Project
from scieflow.core.review import matrix as mx
from scieflow.core.review.cli import review

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "tests" / "fixtures" / "review" / "run"
FX = "fixture-run"
R1 = Path("review") / "round-1"
PAIRS = [("agy", "claude-paper"), ("codex-review", "claude-paper"),
         ("claude-paper", "codex-paper"), ("codex-review", "codex-paper"),
         ("codex-paper", "agy"), ("codex-review", "agy")]


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    for name in ("agents.yml", "defaults.yml", "scite-prompts.yml"):
        shutil.copy(ROOT / "config" / name, tmp_path / "config" / name)
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    shutil.copytree(RUN, tmp_path / "workspace" / FX)
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path)


def ws(project) -> Path:
    return project.run_dir(FX)


def rd(project) -> Path:
    return ws(project) / R1


def fixture_matrix() -> dict:
    return json.loads((RUN / R1 / "matrix.json").read_text())


def write_matrix(project, data) -> None:
    (rd(project) / "matrix.json").write_text(json.dumps(data, indent=2) + "\n")


def all_comments() -> list[tuple]:
    out = []
    for rv, au in PAIRS:
        for pass_ in ("blind", "evidence"):
            data = json.loads((RUN / R1 / f"{rv}-on-{au}.{pass_}.json").read_text())
            out += [(rv, au, pass_, c["id"]) for c in data["major"] + data["minor"]]
    return out


def scite_off(project) -> None:
    """The run as it looks with scite off: no bundle, no pass B."""
    shutil.rmtree(rd(project) / "evidence")
    for rv, au in PAIRS:
        (rd(project) / f"{rv}-on-{au}.evidence.json").unlink()


# --- skeleton ---------------------------------------------------------------------------------


def test_skeleton_has_every_comment_of_every_pass_and_every_flag(project):
    sk = mx.skeleton(ws(project), 1)
    got = [(i["member"]["reviewer"], i["member"]["author"], i["member"]["pass"],
            i["member"]["id"]) for i in sk["items"]]
    assert sorted(got) == sorted(all_comments())
    assert len(got) == len(set(got))
    bundle = json.loads((rd(project) / "evidence" / "bundle.json").read_text())
    assert sk["flags"] == bundle["flags"]
    assert {f["kind"] for f in sk["flags"]} >= {"retracted", "contested", "abstract_only"}
    assert sk["scite_status"] == "ok"
    assert sk["bundle_hash"] == hashlib.sha256(
        (rd(project) / "evidence" / "bundle.json").read_bytes()).hexdigest()
    assert sk["missing"] == [] and sk["refused"] == []
    item = next(i for i in sk["items"] if i["ref"] == "codex-review-on-agy:evidence:M1")
    assert item["severity"] == "major" and item["changed_from_blind"] == "strengthened"
    assert item["text"].startswith("The diagnostic-accuracy claim")
    assert item["evidence"][0]["doi"] == "10.5555/rbd.2019.001"
    assert item["recommendation"] == "MAJOR REVISION"


def test_skeleton_is_deterministic_and_written(project):
    path = mx.write_skeleton(ws(project), 1)
    assert path == rd(project) / "matrix.skeleton.json"
    first = path.read_bytes()
    mx.write_skeleton(ws(project), 1)
    assert path.read_bytes() == first
    assert json.loads(first) == mx.skeleton(ws(project), 1)


def test_skeleton_lists_missing_passes(project):
    (rd(project) / "agy-on-claude-paper.evidence.json").unlink()
    sk = mx.skeleton(ws(project), 1)
    assert sk["missing"] == [{"reviewer": "agy", "author": "claude-paper", "pass": "evidence"}]
    assert not [i for i in sk["items"] if i["ref"].startswith("agy-on-claude-paper:evidence:")]


def test_skeleton_refuses_tampered_and_invalid_passes(project):
    path = rd(project) / "codex-paper-on-agy.blind.json"
    data = json.loads(path.read_text())
    data["summary"] = "edited after freezing"
    path.write_text(json.dumps(data, indent=2) + "\n")
    bad = rd(project) / "codex-review-on-agy.evidence.json"
    data = json.loads(bad.read_text())
    data["major"][0]["evidence"][0]["quote"] = "invented"
    bad.write_text(json.dumps(data, indent=2) + "\n")
    sk = mx.skeleton(ws(project), 1)
    refused = {(r["reviewer"], r["author"], r["pass"]) for r in sk["refused"]}
    assert refused == {("codex-paper", "agy", "blind"), ("codex-paper", "agy", "evidence"),
                       ("codex-review", "agy", "evidence")}
    assert all(r["reason"] for r in sk["refused"])
    refs = {i["ref"].rsplit(":", 1)[0] for i in sk["items"]}
    assert not refs & {"codex-paper-on-agy:blind", "codex-paper-on-agy:evidence",
                       "codex-review-on-agy:evidence"}


def test_skeleton_with_scite_off_expects_blind_passes_only(project):
    scite_off(project)
    sk = mx.skeleton(ws(project), 1)
    assert sk["scite_status"] == "off" and sk["flags"] == [] and "bundle_hash" not in sk
    assert sk["missing"] == []
    assert {i["member"]["pass"] for i in sk["items"]} == {"blind"}


def test_skeleton_needs_an_assignment(project):
    with pytest.raises(mx.MatrixError, match="no assignment"):
        mx.skeleton(ws(project), 2)


# --- check of the coordinator-filled matrix ----------------------------------------------------------


def test_fixture_matrix_validates(project):
    data = mx.check(ws(project), 1)
    assert [c["cluster"] for c in data["clusters"]] == ["K1", "K2", "K3", "K4", "K5", "K6", "K7"]


def mutate(project, fn):
    data = fixture_matrix()
    fn(data)
    write_matrix(project, data)


def cluster(data, cid):
    return next(c for c in data["clusters"] if c["cluster"] == cid)


@pytest.mark.parametrize("fn,match", [
    (lambda d: cluster(d, "K4")["members"].clear() or cluster(d, "K4").update(flags=["F9"]),
     "codex-review-on-claude-paper:blind:m1.*not in any cluster"),
    (lambda d: cluster(d, "K3")["members"].append(dict(cluster(d, "K4")["members"][0])),
     "codex-review-on-claude-paper:blind:m1.*more than one cluster"),
    (lambda d: cluster(d, "K3")["members"].append(
        {"reviewer": "agy", "author": "claude-paper", "pass": "blind", "id": "M7"}),
     "agy-on-claude-paper:blind:M7.*not a comment"),
    (lambda d: cluster(d, "K7").update(flags=["F2"]), "F2 .*more than one cluster|F3.*not in any"),
    (lambda d: cluster(d, "K6").update(flags=["F4", "F8"]), "F8.*not a flag"),
    (lambda d: cluster(d, "K1").update(blocker=False) or cluster(d, "K1").pop("blocker_reason"),
     "F1.*retracted.*blocker"),
    (lambda d: cluster(d, "K3").update(blocker=True, blocker_reason="retracted"),
     "K3.*blocker.*no retracted"),
    (lambda d: cluster(d, "K2")["evidence_for"][0].update(quote="Actigraphy fails."),
     "K2.*not a verbatim quote"),
    (lambda d: cluster(d, "K2")["evidence_for"][0].update(doi="10.9999/elsewhere"),
     "K2.*10.9999/elsewhere.*not in the evidence bundle"),
    (lambda d: cluster(d, "K2").update(scite_status="quota"), "K2.*scite_status"),
    (lambda d: cluster(d, "K4").update(agreement="consensus") or cluster(d, "K3").update(
        agreement="single"), "K3.*single.*3 reviewers"),
    (lambda d: d["clusters"].append(copy.deepcopy(cluster(d, "K5"))), "K5.*twice"),
    (lambda d: d.update(round=2), "round"),
    (lambda d: d.update(bundle_hash="0" * 64), "bundle_hash"),
    (lambda d: cluster(d, "K5").update(recommendation="maybe"), "review-matrix schema"),
])
def test_check_refuses(project, fn, match):
    mutate(project, fn)
    with pytest.raises(mx.MatrixError, match=match):
        mx.check(ws(project), 1)


def test_check_reports_every_problem_at_once(project):
    def two(d):
        cluster(d, "K5")["members"].clear()
        cluster(d, "K5").update(flags=["F9"])
        cluster(d, "K2").update(scite_status="off")
    mutate(project, two)
    with pytest.raises(mx.MatrixError) as exc:
        mx.check(ws(project), 1)
    assert "K2" in str(exc.value) and "F9" in str(exc.value)
    assert len(exc.value.problems) >= 3


def test_check_without_a_matrix(project):
    (rd(project) / "matrix.json").unlink()
    with pytest.raises(mx.MatrixError, match="no matrix"):
        mx.check(ws(project), 1)


def test_tampered_pass_comments_may_not_be_clustered(project):
    path = rd(project) / "codex-review-on-claude-paper.blind.json"
    data = json.loads(path.read_text())
    data["summary"] = "edited after freezing"
    path.write_text(json.dumps(data, indent=2) + "\n")
    with pytest.raises(mx.MatrixError, match="codex-review-on-claude-paper:blind:M1.*not a comment"):
        mx.check(ws(project), 1)


# --- markdown ---------------------------------------------------------------------------------


def test_markdown_is_deterministic_and_matches_the_fixture(project):
    data = mx.check(ws(project), 1)
    text = mx.render_md(data)
    assert text == (RUN / R1 / "matrix.md").read_text()
    shuffled = json.loads(json.dumps(data, sort_keys=True))
    for c in shuffled["clusters"]:
        for key in list(c):
            c[key] = c.pop(key)
    assert mx.render_md(shuffled) == text
    assert mx.render_md(copy.deepcopy(data)) == text


def test_markdown_shows_evidence_and_blockers(project):
    text = mx.render_md(mx.check(ws(project), 1))
    lines = text.splitlines()
    header = next(line for line in lines if line.startswith("| Cluster"))
    for col in ("Evidence for", "Evidence against", "Blocker", "scite", "Recommendation"):
        assert col in header
    k1 = next(line for line in lines if line.startswith("| K1 "))
    assert "retracted" in k1 and "10.5555/rbd.2020.014" in k1
    k3 = next(line for line in lines if line.startswith("| K3 "))
    assert "| — |" in k3


def test_markdown_escapes_pipes_and_newlines(project):
    mutate(project, lambda d: cluster(d, "K5").update(title="axis | label\nmissing"))
    text = mx.render_md(mx.check(ws(project), 1))
    k5 = next(line for line in text.splitlines() if line.startswith("| K5 "))
    assert "axis \\| label missing" in k5


def off_matrix(project) -> dict:
    sk = mx.skeleton(ws(project), 1)
    return {"version": 1, "round": 1, "clusters": [
        {"cluster": f"K{n}", "title": i["text"][:40], "members": [i["member"]],
         "agreement": "single", "evidence_for": [], "evidence_against": [], "blocker": False,
         "scite_status": "off", "recommendation": "accept", "rationale": "blind only"}
        for n, i in enumerate(sk["items"], 1)]}


def test_scite_off_matrix_validates_and_shows_off(project):
    scite_off(project)
    write_matrix(project, off_matrix(project))
    data = mx.check(ws(project), 1)
    text = mx.render_md(data)
    rows = [line for line in text.splitlines() if line.startswith("| K")]
    assert rows and all(line.count("| off |") >= 3 for line in rows)   # for, against, scite


def test_scite_off_matrix_may_not_claim_evidence(project):
    scite_off(project)
    data = off_matrix(project)
    data["clusters"][0]["scite_status"] = "ok"
    write_matrix(project, data)
    with pytest.raises(mx.MatrixError, match="K1.*scite_status"):
        mx.check(ws(project), 1)
    data["clusters"][0]["scite_status"] = "off"
    data["clusters"][0]["evidence_for"] = [{"doi": "10.5555/rbd.2019.001", "quote": "x",
                                            "source": "crossref"}]
    write_matrix(project, data)
    with pytest.raises(mx.MatrixError, match="K1.*no evidence bundle"):
        mx.check(ws(project), 1)


# --- CLI ------------------------------------------------------------------------------------------


def test_cli_matrix_writes_the_skeleton(project):
    res = CliRunner().invoke(review, ["matrix", FX, "--round", "1"])
    assert res.exit_code == 0, res.output
    assert (rd(project) / "matrix.skeleton.json").exists()
    assert f"{len(all_comments())} comments" in res.output and "4 flags" in res.output
    assert events.read(ws(project), types=("review.matrix_ready",)) == []


def test_cli_matrix_check_renders_and_emits(project):
    (rd(project) / "matrix.md").unlink()
    res = CliRunner().invoke(review, ["matrix", FX, "--round", "1", "--check"])
    assert res.exit_code == 0, res.output
    assert (rd(project) / "matrix.md").read_text() == (RUN / R1 / "matrix.md").read_text()
    evs = events.read(ws(project), types=("review.matrix_ready",))
    assert len(evs) == 1 and evs[0]["data"]["round"] == 1 and evs[0]["data"]["clusters"] == 7
    assert evs[0]["data"]["blockers"] == ["K1"]


def test_cli_matrix_check_failure_writes_nothing(project):
    (rd(project) / "matrix.md").unlink()
    mutate(project, lambda d: cluster(d, "K2").update(scite_status="quota"))
    res = CliRunner().invoke(review, ["matrix", FX, "--round", "1", "--check"])
    assert res.exit_code != 0 and "scite_status" in res.output
    assert not (rd(project) / "matrix.md").exists()
    assert events.read(ws(project), types=("review.matrix_ready",)) == []


def test_check_refuses_a_round_with_missing_or_refused_passes(project):
    (rd(project) / "codex-review-on-agy.evidence.json").unlink()
    data = fixture_matrix()
    for c in data["clusters"]:
        c["members"] = [m for m in c["members"]
                        if not (m["reviewer"] == "codex-review" and m["author"] == "agy"
                                and m["pass"] == "evidence")]
    write_matrix(project, data)
    with pytest.raises(mx.MatrixError, match="codex-review on agy.*evidence.*missing"):
        mx.check(ws(project), 1)


def test_check_refuses_a_round_with_a_tampered_pass(project):
    path = rd(project) / "codex-review-on-codex-paper.blind.json"
    d = json.loads(path.read_text())
    d["summary"] = "edited after freezing"
    path.write_text(json.dumps(d, indent=2) + "\n")
    data = fixture_matrix()
    for c in data["clusters"]:
        c["members"] = [m for m in c["members"]
                        if not (m["reviewer"] == "codex-review" and m["author"] == "codex-paper")]
    data["clusters"] = [c for c in data["clusters"] if c["members"] or c.get("flags")]
    write_matrix(project, data)
    with pytest.raises(mx.MatrixError, match="codex-review on codex-paper.*changed after it was frozen"):
        mx.check(ws(project), 1)
