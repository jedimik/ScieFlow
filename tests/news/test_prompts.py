from datetime import date

from scieflow.news.config import Interest
from scieflow.news.prompts import build_prompt

START, END = date(2026, 6, 17), date(2026, 7, 17)


def test_minimal_prompt_has_name_window_and_skeleton():
    p = build_prompt(Interest(name="DuckDB"), START, END)
    assert '"DuckDB"' in p
    assert "2026-06-17" in p and "2026-07-17" in p
    for section in (
        "## DuckDB",
        "### News",
        "### Updates",
        "### New Use Cases",
        "### Fixes",
        "### Improvements",
        "### 🔥 Highlight",
    ):
        assert section in p
    assert "### Gaps & Blind Spots" not in p
    assert "_Nothing found._" in p
    assert "Do NOT invent news" in p
    assert "source link" in p


def test_hints_are_included():
    interest = Interest(
        name="Snakemake",
        repo="snakemake/snakemake",
        urls=["https://snakemake.readthedocs.io"],
        keywords=["workflow", "bioinformatics"],
    )
    p = build_prompt(interest, START, END)
    assert "https://github.com/snakemake/snakemake" in p
    assert "https://snakemake.readthedocs.io" in p
    assert "workflow, bioinformatics" in p


def test_context_enables_gaps_section():
    interest = Interest(name="Snakemake", context="HPC pipelines with SLURM")
    p = build_prompt(interest, START, END)
    assert "HPC pipelines with SLURM" in p
    assert "### Gaps & Blind Spots" in p


def test_context_adds_gaps_rule_text():
    interest = Interest(name="Snakemake", context="HPC pipelines with SLURM")
    p = build_prompt(interest, START, END)
    assert "Gaps & Blind Spots: given the user's context" in p


def test_context_enables_science_rule():
    p = build_prompt(Interest(name="X", context="ctx"), START, END)
    assert "peer-reviewed papers" in p
    p2 = build_prompt(Interest(name="X"), START, END)
    assert "peer-reviewed papers" not in p2


def test_science_template_prompt():
    interest = Interest(name="Protein LMs", template="science")
    p = build_prompt(interest, START, END)
    assert "### New Papers & Preprints" in p
    assert "### Community & Events" in p
    assert "### News" not in p
    assert "arXiv" in p  # research focus present
    assert "### 🔥 Highlight" in p
    assert "### Gaps & Blind Spots" not in p


def test_template_hints_in_rules():
    interest = Interest(name="X", template="coding")
    p = build_prompt(interest, START, END)
    assert "- Security (CVEs):" in p
    assert "Do NOT invent news" in p
