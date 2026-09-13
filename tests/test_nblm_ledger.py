from datetime import datetime, timedelta, timezone

from nblm import ledger

CLAIM = {"id": "c001", "text": "Filtering improved SSIM.", "doi": "10.1/x",
         "file": "sections/results.tex", "line": 12}


def test_notebook_roundtrip(tmp_path):
    assert ledger.load_notebook(tmp_path)["notebook_id"] is None
    ledger.set_notebook(tmp_path, "nb-1", "scieflow-run", "default")
    assert ledger.load_notebook(tmp_path)["title"] == "scieflow-run"
    assert (tmp_path / "nblm" / "notebook.yml").exists()


def test_record_source_is_idempotent_by_doi(tmp_path):
    ledger.record_source(tmp_path, "10.1/x", "src-1", "sources/a.pdf", "aa")
    ledger.record_source(tmp_path, "10.1/x", "src-2", "sources/a.pdf", "bb")
    ledger.record_source(tmp_path, "10.2/y", "src-3", "sources/b.pdf", "cc")
    rows = ledger.load_notebook(tmp_path)["sources"]
    assert len(rows) == 2
    assert ledger.source_for(tmp_path, "10.1/x")["source_id"] == "src-2"
    assert ledger.source_for(tmp_path, "10.9/none") is None


def test_quota_counts_per_utc_day_and_per_run(tmp_path):
    day1 = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    day2 = day1 + timedelta(hours=2)
    ledger.record_question(tmp_path, 2, now=day1)
    ledger.record_question(tmp_path, 1, now=day2)
    assert ledger.questions_today(tmp_path, now=day1) == 2
    assert ledger.questions_today(tmp_path, now=day2) == 1
    assert ledger.questions_this_run(tmp_path) == 3


def test_quota_day_key_is_utc_not_local(tmp_path):
    late = datetime(2026, 9, 12, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert ledger.utc_day(late) == "2026-09-13"


def test_verdict_cache_key_is_stable_and_specific():
    a = ledger.cache_key("same text", "10.1/x")
    assert a == ledger.cache_key("same text", "10.1/x")
    assert a != ledger.cache_key("same text", "10.2/y")
    assert a != ledger.cache_key("other text", "10.1/x")


def test_verdict_roundtrip_keeps_provenance(tmp_path):
    assert ledger.get_verdict(tmp_path, CLAIM["text"], CLAIM["doi"]) is None
    ledger.record_verdict(tmp_path, CLAIM,
                          {"verdict": "supported", "evidence": "a quote"})
    hit = ledger.get_verdict(tmp_path, CLAIM["text"], CLAIM["doi"])
    assert hit["verdict"] == "supported"
    assert hit["file"] == "sections/results.tex" and hit["line"] == 12
    assert len(ledger.all_verdicts(tmp_path)) == 1
