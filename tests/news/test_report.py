import pytest

from scieflow.news.report import REQUIRED_SECTIONS, ExtractionError, assemble_report, extract_block


def test_clean_block_passes_through(make_block):
    block = make_block("Snakemake")
    assert extract_block(block, "Snakemake") == block.strip()


def test_prose_wrapped_block(make_block):
    raw = "Sure! Here is the report:\n\n" + make_block("DuckDB") + "\nHope this helps!"
    out = extract_block(raw, "DuckDB")
    assert out.startswith("## DuckDB")
    assert "Hope this helps!" in out  # trailing prose is kept; header start is what matters


def test_fenced_block(make_block):
    raw = "Here you go:\n```markdown\n" + make_block("DuckDB") + "```\n"
    out = extract_block(raw, "DuckDB")
    assert out.startswith("## DuckDB")
    assert "```" not in out


def test_missing_header(make_block):
    with pytest.raises(ExtractionError, match="no '## DuckDB' header"):
        extract_block(make_block("Snakemake"), "DuckDB")


def test_missing_sections():
    with pytest.raises(ExtractionError, match="missing sections.*Fixes"):
        extract_block("## X\n### News\nstuff\n", "X")


def test_header_prefix_does_not_match(make_block):
    raw = make_block("DuckDB").replace("## DuckDB", "## DuckDB Extended", 1)
    with pytest.raises(ExtractionError, match="no '## DuckDB' header"):
        extract_block(raw, "DuckDB")


def test_section_mentioned_in_body_does_not_count():
    block = (
        "## X\n"
        "### News\n"
        "- discussed ### Updates and ### New Use Cases and ### Fixes and ### Improvements inline\n"
    )
    with pytest.raises(ExtractionError, match="missing sections"):
        extract_block(block, "X")


def run_doc(failures=None):
    return {
        "id": 1,
        "timestamp": "2026-07-17T10:00:00+00:00",
        "agent": "claude",
        "failures": failures or [],
    }


def result_doc(name, markdown, edited=None):
    return {
        "run_id": 1,
        "name": name,
        "window_start": "2026-06-17",
        "window_end": "2026-07-17",
        "markdown": markdown,
        "edited_markdown": edited,
    }


def test_assemble_report(make_block):
    report = assemble_report(
        run_doc(), [result_doc("Snakemake", make_block("Snakemake"))]
    )
    assert report.startswith("# ScieFlow news report — 2026-07-17")
    assert "Agent: `claude`" in report
    assert "*Window: 2026-06-17 → 2026-07-17*" in report
    assert "## Snakemake" in report
    assert "## ⚠ Failed" not in report


def test_assemble_prefers_edited_markdown(make_block):
    report = assemble_report(
        run_doc(), [result_doc("X", make_block("X"), edited="## X\nedited content\n")]
    )
    assert "edited content" in report


def test_assemble_lists_failures(make_block):
    report = assemble_report(run_doc(failures=[{"name": "Y", "reason": "timeout"}]), [])
    assert "## ⚠ Failed" in report
    assert "- **Y** — timeout" in report


def test_fenced_reply_with_inner_code_fence(make_block):
    inner = make_block("DuckDB").replace(
        "- Released 9.9 (2026-07-01) [notes](https://example.com/9.9)",
        "- Released 9.9 (2026-07-01) [notes](https://example.com/9.9)\n```sql\nSELECT 1;\n```",
    )
    raw = "```markdown\n" + inner + "```\n"
    out = extract_block(raw, "DuckDB")
    assert "SELECT 1;" in out
    assert "### Improvements" in out
    assert out.count("```") == 2  # inner pair preserved, outer wrapper gone


def test_unfenced_reply_with_trailing_code_fence(make_block):
    raw = make_block("DuckDB") + "```python\nimport duckdb\n```\n"
    out = extract_block(raw, "DuckDB")
    assert out.endswith("```")  # legitimate closing fence NOT stripped
    assert out.count("```") == 2


def test_extract_block_custom_sections():
    block = "## X\n### Alpha\na\n### Beta\nb\n"
    assert extract_block(block, "X", required_sections=["### Alpha", "### Beta"]) == block.strip()
    with pytest.raises(ExtractionError, match="missing sections.*Gamma"):
        extract_block(block, "X", required_sections=["### Alpha", "### Gamma"])


def test_required_sections_track_tool_template():
    from scieflow.news.templates import required_sections

    assert list(REQUIRED_SECTIONS) == required_sections("tool")
