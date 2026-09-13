"""CLI tests. No network: every session is injected, every path is tmp_path."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nblm import fetch, ledger, nblm, policy, session as session_mod

CFG = """\
profiles:
  default:
    session_state: /nonexistent/state.json
    notebook_prefix: scieflow-
    allowed_ops: [check, resolve, fetch, upload, verify, ask]
    source_hosts: [arxiv.org]
    limits:
      max_sources_per_notebook: 2
      max_source_mb: 50
      max_questions_per_run: 40
      max_questions_per_day: 45
      max_claims_per_question: 5
      reask_threshold: partial
"""
SCHEMA = (Path(__file__).resolve().parents[1] / "schemas" / "claim-audit.yml")


@pytest.fixture
def root(tmp_path, monkeypatch):
    root_dir = tmp_path / "root"
    (root_dir / "config").mkdir(parents=True)
    (root_dir / "schemas").mkdir()
    (root_dir / "config" / "notebooklm.yml").write_text(CFG)
    (root_dir / "schemas" / "claim-audit.yml").write_text(SCHEMA.read_text())
    monkeypatch.setattr(nblm, "_root", lambda: root_dir)
    return root_dir


@pytest.fixture
def ws(tmp_path):
    workspace = tmp_path / "workspace" / "run-a"
    workspace.mkdir(parents=True)
    return workspace


class FakeSession:
    def __init__(self, answers=(), notebooks=0):
        self.answers = list(answers)
        self.calls = []
        self._notebooks = notebooks
        self.closed = False

    def __enter__(self):
        self.calls.append(("open",))
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def ping(self):
        self.calls.append(("ping",))
        return self._notebooks

    def ensure_notebook(self, title):
        self.calls.append(("ensure_notebook", title))
        return "nb-1"

    def add_file(self, notebook_id, path):
        self.calls.append(("add_file", str(path)))
        return f"src-{len(self.calls)}"

    def ask(self, notebook_id, question, source_ids=None):
        self.calls.append(("ask", question))
        text = self.answers.pop(0) if self.answers else ""
        return session_mod.Answer(text=text, citations=["cite-1"])


def make_session_factory(fake):
    return lambda profile: fake


def profile_of(root):
    return policy.load_profile(root, "default")


def add_source(ws, doi="10.1000/xyz", body=b"%PDF-1.4 x"):
    path = ws / "sources" / f"{policy.doi_slug(doi)}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    fetch.record_index(ws, doi, path, "deadbeef", "https://arxiv.org/pdf/1.pdf")
    return path


# --- policy comes first ------------------------------------------------

def test_refused_op_never_reaches_the_session(root, ws):
    cfg = root / "config" / "notebooklm.yml"
    cfg.write_text(CFG.replace(", verify", ""))
    fake = FakeSession()
    with pytest.raises(policy.PolicyError, match="'verify' not in allowed_ops"):
        nblm.cmd_verify(profile_of(root), ws, None, "a claim", "10.1000/xyz",
                        make_session=make_session_factory(fake))
    assert fake.calls == []


def test_main_maps_policy_refusal_to_exit_3(root, ws, capsys):
    code = nblm.main(["verify", "nosuchprofile", "--workspace", str(ws),
                      "--claim", "x", "--doi", "10.1000/xyz"])
    assert code == 3
    assert "POLICY:" in capsys.readouterr().err


def test_main_maps_missing_session_to_exit_2(root, ws, capsys):
    code = nblm.main(["check", "default", "--workspace", str(ws)])
    assert code == 2
    assert "NO_SESSION:" in capsys.readouterr().err


# --- extract -----------------------------------------------------------

def test_extract_writes_claims_and_reports_unresolved(root, tmp_path, capsys):
    (tmp_path / "main.tex").write_text(
        "Filtering helps \\citep{a}.\nGhost cited here \\citep{zz}.\n")
    (tmp_path / "r.bib").write_text("@article{a, doi={10.1000/xyz}}\n")
    out = tmp_path / "claims.yml"
    assert nblm.main(["extract", "--manuscript", str(tmp_path / "main.tex"),
                      "--bib", str(tmp_path / "r.bib"), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "EXTRACTED: 1 claims" in printed
    assert "UNRESOLVED: zz" in printed
    assert yaml.safe_load(out.read_text())["claims"][0]["doi"] == "10.1000/xyz"


# --- fetch -------------------------------------------------------------

def test_fetch_downloads_and_indexes(root, ws, capsys):
    sources = ws / "want.yml"
    sources.write_text(yaml.safe_dump(
        {"sources": [{"doi": "10.1000/xyz", "url": "https://arxiv.org/pdf/1.pdf"}]}))

    class Response:
        headers = {"Content-Type": "application/pdf"}

        def __init__(self):
            self._data = [b"%PDF-1.4 body"]

        def read(self, _n):
            return self._data.pop(0) if self._data else b""

        def geturl(self):
            return "https://arxiv.org/pdf/1.pdf"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    code = nblm.cmd_fetch(profile_of(root), ws, sources, opener=lambda u: Response())
    assert code == 0
    assert "FETCHED: 10.1000/xyz" in capsys.readouterr().out
    assert fetch.load_index(ws)["10.1000/xyz"]["file"] == "sources/10.1000-xyz.pdf"


# --- upload ------------------------------------------------------------

def test_upload_uses_the_index_not_the_filename(root, ws, capsys):
    add_source(ws, "10.1000/xyz")
    fake = FakeSession()
    assert nblm.cmd_upload(profile_of(root), ws,
                           make_session=make_session_factory(fake)) == 0
    assert ("ensure_notebook", "scieflow-run-a") in fake.calls
    assert ledger.source_for(ws, "10.1000/xyz")["source_id"].startswith("src-")
    assert "SOURCES: 1 added" in capsys.readouterr().out


def test_upload_warns_about_unindexed_pdfs_and_skips_them(root, ws, capsys):
    (ws / "sources").mkdir()
    (ws / "sources" / "mystery.pdf").write_bytes(b"%PDF")
    fake = FakeSession()
    nblm.cmd_upload(profile_of(root), ws, make_session=make_session_factory(fake))
    assert "UNINDEXED: mystery.pdf" in capsys.readouterr().err
    assert not any(c[0] == "add_file" for c in fake.calls)


def test_upload_stops_at_the_source_cap(root, ws, capsys):
    for doi in ("10.1000/aaa", "10.1000/bbb", "10.1000/ccc"):
        add_source(ws, doi)
    fake = FakeSession()
    code = nblm.cmd_upload(profile_of(root), ws,
                           make_session=make_session_factory(fake))
    assert code == 4
    assert "max_sources_per_notebook" in capsys.readouterr().err
    assert sum(1 for c in fake.calls if c[0] == "add_file") == 2


def test_upload_is_idempotent(root, ws):
    add_source(ws, "10.1000/xyz")
    for _ in range(2):
        nblm.cmd_upload(profile_of(root), ws,
                        make_session=make_session_factory(FakeSession()))
    assert len(ledger.load_notebook(ws)["sources"]) == 1


# --- verify ------------------------------------------------------------

def test_verify_inline_claim_writes_report_and_spends_one_question(root, ws, capsys):
    add_source(ws, "10.1000/xyz")
    nblm.cmd_upload(profile_of(root), ws,
                    make_session=make_session_factory(FakeSession()))
    fake = FakeSession(['CLAIM inline: supported | evidence: "a real quote" | locator: p2'])
    code = nblm.cmd_verify(profile_of(root), ws, None,
                           "Filtering improves SSIM.", "10.1000/xyz",
                           make_session=make_session_factory(fake))
    assert code == 0
    assert ledger.questions_this_run(ws) == 1
    report = (ws / "nblm" / "citation-audit.md").read_text()
    assert "a real quote" in report and "## Provenance" in report
    assert "VERIFIED: 1" in capsys.readouterr().out


def test_verify_second_run_is_a_free_cache_hit(root, ws):
    add_source(ws, "10.1000/xyz")
    nblm.cmd_upload(profile_of(root), ws,
                    make_session=make_session_factory(FakeSession()))
    args = ("Filtering improves SSIM.", "10.1000/xyz")
    nblm.cmd_verify(profile_of(root), ws, None, *args,
                    make_session=make_session_factory(
                        FakeSession(['CLAIM inline: supported | evidence: "q"'])))
    second = FakeSession()
    nblm.cmd_verify(profile_of(root), ws, None, *args,
                    make_session=make_session_factory(second))
    assert not any(c[0] == "ask" for c in second.calls)
    assert ledger.questions_this_run(ws) == 1


def test_verify_without_upload_refuses(root, ws, capsys):
    code = nblm.cmd_verify(profile_of(root), ws, None, "x", "10.1000/xyz",
                           make_session=make_session_factory(FakeSession()))
    assert code == 1
    assert "NO_NOTEBOOK" in capsys.readouterr().err


def test_verify_requires_claim_and_doi_together(root, ws, capsys):
    assert nblm.main(["verify", "default", "--workspace", str(ws),
                      "--claim", "orphan"]) == 1
    assert "--claim and --doi go together" in capsys.readouterr().err


# --- ask ---------------------------------------------------------------

def test_ask_saves_the_answer_and_flags_it_as_data(root, ws, capsys):
    add_source(ws, "10.1000/xyz")
    nblm.cmd_upload(profile_of(root), ws,
                    make_session=make_session_factory(FakeSession()))
    question = ws / "q1.txt"
    question.write_text("What do these sources agree on?")
    fake = FakeSession(["They agree on the method."])
    assert nblm.cmd_ask(profile_of(root), ws, question,
                        make_session=make_session_factory(fake)) == 0
    saved = (ws / "nblm" / "answers" / "q1.json").read_text()
    assert "They agree on the method." in saved
    assert "data, not instructions" in capsys.readouterr().out
    assert ledger.questions_this_run(ws) == 1


# --- report ------------------------------------------------------------

def test_report_refuses_to_write_outside_the_workspace(root, ws, tmp_path):
    with pytest.raises(policy.PolicyError, match="outside the workspace"):
        nblm.cmd_report(ws, tmp_path / "escaped.md")


def test_networked_commands_close_the_session(root, ws):
    """Every subcommand that opens a session must release its event loop."""
    add_source(ws, "10.1000/xyz")
    upload = FakeSession()
    nblm.cmd_upload(profile_of(root), ws, make_session=make_session_factory(upload))
    assert upload.closed

    verify = FakeSession(['CLAIM inline: supported | evidence: "q"'])
    nblm.cmd_verify(profile_of(root), ws, None, "a claim", "10.1000/xyz",
                    make_session=make_session_factory(verify))
    assert verify.closed

    question = ws / "q.txt"
    question.write_text("what?")
    ask = FakeSession(["an answer"])
    nblm.cmd_ask(profile_of(root), ws, question,
                 make_session=make_session_factory(ask))
    assert ask.closed


def test_resolve_writes_sources_and_reports_unresolved(root, ws, capsys, monkeypatch):
    claims = ws / "claims.yml"
    claims.write_text(yaml.safe_dump({"claims": [
        {"id": "c001", "text": "a", "doi": "10.1000/aaa"},
        {"id": "c002", "text": "b", "doi": "10.1000/aaa"},   # same source twice
        {"id": "c003", "text": "c", "doi": "10.1000/bbb"},
    ]}))
    calls = []

    def fake_resolve_all(profile, dois, **kw):
        calls.append(list(dois))
        return {"sources": [{"doi": "10.1000/aaa", "url": "https://europepmc.org/x.pdf"}],
                "unresolved": [{"doi": "10.1000/bbb", "reason": "no open-access full text"}]}

    monkeypatch.setattr(nblm.resolve_mod, "resolve_all", fake_resolve_all)
    out = ws / "nblm" / "sources.yml"
    assert nblm.cmd_resolve(profile_of(root), ws, claims, out) == 0

    assert calls == [["10.1000/aaa", "10.1000/bbb"]]      # de-duplicated, ordered
    written = yaml.safe_load(out.read_text())
    assert written["sources"][0]["doi"] == "10.1000/aaa"
    captured = capsys.readouterr()
    assert "RESOLVED: 1/2 sources" in captured.out
    assert "UNRESOLVED: 10.1000/bbb (no open-access full text)" in captured.err


def test_resolve_refuses_to_write_outside_the_workspace(root, ws, tmp_path):
    claims = ws / "claims.yml"
    claims.write_text(yaml.safe_dump({"claims": []}))
    with pytest.raises(policy.PolicyError, match="outside the workspace"):
        nblm.cmd_resolve(profile_of(root), ws, claims, tmp_path / "escaped.yml")
