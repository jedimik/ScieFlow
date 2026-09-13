from pathlib import Path

import pytest

from nblm import ledger, policy, session, verify

VOCAB = ["supported", "partial", "not-addressed", "unsupported",
         "contradicted", "unparseable"]

PROFILE = policy.Profile(
    name="default",
    session_state="/nonexistent/state.json",
    notebook_prefix="scieflow-",
    allowed_ops=["verify"],
    source_hosts=["arxiv.org"],
    limits={"max_sources_per_notebook": 50, "max_source_mb": 50,
            "max_questions_per_run": 40, "max_questions_per_day": 45,
            "max_claims_per_question": 2, "reask_threshold": "partial"},
)


def claim(n, doi="10.1/x", text=None):
    return {"id": f"c{n:03d}", "text": text or f"Statement number {n}.",
            "doi": doi, "file": "sections/results.tex", "line": n}


class FakeSession:
    """Answers come from a scripted list; records every question asked."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.questions = []

    def ask(self, notebook_id, question, source_ids=None):
        self.questions.append(question)
        text = self.answers.pop(0) if self.answers else ""
        return session.Answer(text=text, citations=[])


def with_source(tmp_path, doi="10.1/x"):
    ledger.record_source(tmp_path, doi, "src-1", "sources/10.1-x.pdf", "ab")
    return tmp_path


def test_build_question_names_the_single_admissible_source():
    question = verify.build_question([claim(1)], "10.1/x", "a.pdf", VOCAB)
    assert "10.1/x" in question and "a.pdf" in question
    assert "CLAIM c001: Statement number 1." in question
    fmt = [ln for ln in question.splitlines() if ln.startswith("CLAIM <id>:")][0]
    assert "unparseable" not in fmt          # never offered as an answer
    assert "contradicted" in fmt


def test_parse_answer_reads_the_line_format():
    text = 'CLAIM c001: supported | evidence: "we observed a 12% gain" | locator: Sec 3'
    got = verify.parse_answer(text, [claim(1)], VOCAB)["c001"]
    assert got["verdict"] == "supported"
    assert got["evidence"] == "we observed a 12% gain"
    assert got["locator"] == "Sec 3"


def test_parse_answer_tolerates_bullets_and_stray_prose():
    text = ("Here is my analysis.\n"
            "- CLAIM c001: contradicted | evidence: \"the opposite holds\" | locator: p4\n"
            "Hope that helps!")
    assert verify.parse_answer(text, [claim(1)], VOCAB)["c001"]["verdict"] == "contradicted"


def test_missing_or_unreadable_lines_become_unparseable_not_a_guess():
    got = verify.parse_answer("CLAIM c001: probably fine | evidence: NONE",
                              [claim(1), claim(2)], VOCAB)
    assert got["c001"]["verdict"] == "unparseable"
    assert "unreadable verdict" in got["c001"]["note"]
    assert got["c002"]["verdict"] == "unparseable"
    assert "no CLAIM line" in got["c002"]["note"]


def test_positive_verdict_without_a_quote_is_downgraded():
    got = verify.parse_answer("CLAIM c001: supported | evidence: NONE",
                              [claim(1)], VOCAB)["c001"]
    assert got["verdict"] == "unsupported"
    assert "no quote" in got["note"]


def test_run_batches_by_source_and_caches(tmp_path):
    with_source(tmp_path)
    answers = ['CLAIM c001: supported | evidence: "q1"\n'
               'CLAIM c002: supported | evidence: "q2"']
    fake = FakeSession(answers)
    claims = [claim(1), claim(2)]
    summary = verify.run(fake, tmp_path, PROFILE, "nb-1", claims, VOCAB)

    assert summary["asked"] == 1 and summary["verified"] == 2
    assert ledger.questions_this_run(tmp_path) == 1

    again = verify.run(FakeSession([]), tmp_path, PROFILE, "nb-1", claims, VOCAB)
    assert again["cached"] == 2 and again["asked"] == 0
    assert ledger.questions_this_run(tmp_path) == 1


def test_run_reasks_doubtful_claims_once(tmp_path):
    with_source(tmp_path)
    fake = FakeSession([
        'CLAIM c001: supported | evidence: "solid"\n'
        'CLAIM c002: partial | evidence: "weaker"',
        'CLAIM c002: unsupported | evidence: "actually no" | locator: p9',
    ])
    summary = verify.run(fake, tmp_path, PROFILE, "nb-1",
                         [claim(1), claim(2)], VOCAB)
    assert summary["reasked"] == 1
    assert len(fake.questions) == 2
    assert "CLAIM c002" in fake.questions[1] and "CLAIM c001" not in fake.questions[1]

    second = ledger.get_verdict(tmp_path, claim(2)["text"], "10.1/x")
    assert second["verdict"] == "unsupported" and second["reasked"] is True


def test_run_skips_claims_with_no_uploaded_source(tmp_path):
    with_source(tmp_path, "10.1/x")
    fake = FakeSession([])
    summary = verify.run(fake, tmp_path, PROFILE, "nb-1",
                         [claim(1, doi="10.9/missing")], VOCAB)
    assert summary["skipped"][0]["reason"] == "no source uploaded for this DOI"
    assert fake.questions == []


def test_run_stops_at_the_daily_ceiling_without_spending_more(tmp_path):
    with_source(tmp_path)
    tight = policy.Profile(**{**PROFILE.__dict__,
                              "limits": {**PROFILE.limits,
                                         "max_questions_per_day": 1,
                                         "max_claims_per_question": 1}})
    fake = FakeSession(['CLAIM c001: supported | evidence: "q"'] * 3)
    summary = verify.run(fake, tmp_path, tight, "nb-1",
                         [claim(1), claim(2)], VOCAB)
    assert summary["asked"] == 1
    assert "max_questions_per_day" in summary["limit"]
    assert len(fake.questions) == 1
    assert ledger.questions_today(tmp_path) == 1


def test_render_report_has_the_required_sections_worst_first(tmp_path):
    with_source(tmp_path)
    ledger.set_notebook(tmp_path, "nb-1", "scieflow-run", "default")
    verify.run(FakeSession([
        'CLAIM c001: supported | evidence: "good"\n'
        'CLAIM c002: contradicted | evidence: "bad" | locator: p2',
        'CLAIM c002: contradicted | evidence: "bad" | locator: p2',
    ]), tmp_path, PROFILE, "nb-1", [claim(1), claim(2)], VOCAB)

    report = verify.render_report(tmp_path, VOCAB, slug="run")
    for section in ("## Summary", "## Findings", "## Provenance"):
        assert section in report
    assert report.index("### contradicted") < report.index("### supported")
    assert "sections/results.tex:2" in report
    assert "advisory" in report.lower()


def test_rendered_report_passes_the_claim_audit_schema(tmp_path):
    import validate
    with_source(tmp_path)
    ledger.set_notebook(tmp_path, "nb-1", "scieflow-run", "default")
    verify.run(FakeSession(['CLAIM c001: contradicted | evidence: "no" | locator: p1',
                            'CLAIM c001: contradicted | evidence: "no" | locator: p1']),
               tmp_path, PROFILE, "nb-1", [claim(1)], VOCAB)
    report = verify.render_report(tmp_path, VOCAB, slug="run")
    assert validate.validate_claim_audit(report) == []


def test_question_tells_the_model_to_judge_only_this_source_s_part():
    """Live finding: without this, a multi-source sentence returns
    'not-addressed' from every source it cites, because no single paper
    supports the whole compound sentence."""
    question = verify.build_question([claim(1)], "10.1/x", "a.pdf", VOCAB)
    assert "cites SEVERAL sources at once" in question
    assert "that is 'partial', not" in question
    assert "nothing to do with ANY part" in question


def test_every_question_is_scoped_to_its_own_source(tmp_path):
    """Unscoped, the model can answer from another paper in the notebook —
    a false 'supported'."""
    ledger.record_source(tmp_path, "10.1/x", "src-x", "sources/x.pdf", "aa")
    ledger.record_source(tmp_path, "10.2/y", "src-y", "sources/y.pdf", "bb")

    scopes = []

    class ScopeRecordingSession(FakeSession):
        def ask(self, notebook_id, question, source_ids=None):
            scopes.append(source_ids)
            return super().ask(notebook_id, question)

    fake = ScopeRecordingSession([
        'CLAIM c001: supported | evidence: "q1"',
        'CLAIM c002: contradicted | evidence: "q2"',
        'CLAIM c002: contradicted | evidence: "q2"',
    ])
    verify.run(fake, tmp_path, PROFILE, "nb-1",
               [claim(1, doi="10.1/x"), claim(2, doi="10.2/y")], VOCAB)

    assert scopes[0] == ["src-x"]
    assert scopes[1] == ["src-y"]
    assert scopes[2] == ["src-y"]            # the re-ask stays scoped too
