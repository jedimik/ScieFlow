from datetime import date

from scieflow.news.gui.reports import escape_markdown_html, history_rows, normalize_edit, result_filename, run_label


class FakeDb:
    def __init__(self, results_by_run):
        self._results_by_run = results_by_run

    def results_for_run(self, run_id):
        return self._results_by_run.get(run_id, [])


def test_escape_markdown_html_neutralizes_tags():
    out = escape_markdown_html('## X\n<script>alert(1)</script>\n**bold** [l](https://e)')
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "**bold**" in out and "[l](https://e)" in out  # markdown syntax untouched


def test_normalize_edit_blank_becomes_none():
    assert normalize_edit("") is None
    assert normalize_edit("   \n\t  ") is None


def test_normalize_edit_keeps_real_text():
    assert normalize_edit("## X\nreal content\n") == "## X\nreal content\n"


def test_run_label():
    run = {"id": 3, "timestamp": "2026-07-17T10:00:00+00:00", "agent": "claude", "failures": []}
    assert run_label(run) == "#3 — 2026-07-17 (claude)"
    run["failures"] = [{"name": "X", "reason": "boom"}]
    assert run_label(run) == "#3 — 2026-07-17 (claude) — 1 failed"


def test_history_rows_maps_runs_newest_first():
    runs = [
        {
            "id": 2,
            "timestamp": "2026-07-17T10:00:00+00:00",
            "agent": "claude",
            "failures": [{"name": "Bad", "reason": "boom"}],
        },
        {
            "id": 1,
            "timestamp": "2026-06-01T08:00:00+00:00",
            "agent": "codex",
            "failures": [],
        },
    ]
    db = FakeDb(
        {
            2: [{"name": "A"}],
            1: [{"name": "A"}, {"name": "B"}],
        }
    )
    rows = history_rows(runs, db)
    assert rows == [
        {"id": 2, "date": "2026-07-17", "agent": "claude", "interests": 1, "failed": 1},
        {"id": 1, "date": "2026-06-01", "agent": "codex", "interests": 2, "failed": 0},
    ]


def test_history_rows_empty_runs():
    assert history_rows([], FakeDb({})) == []


def test_result_filename():
    run = {"id": 4, "timestamp": "2026-07-19T10:00:00+00:00"}
    assert result_filename(run, "Snakemake") == "2026-07-19-Snakemake-run4.md"
    assert result_filename(run, "A/B c") == "2026-07-19-A_B_c-run4.md"
