"""Dry-run the lit-review mechanical pipeline with 3 stub agents. No tokens, no network."""

import itertools
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS = ["stub_a", "stub_b", "stub_c"]


def sh(argv, cwd, env=None):
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, env=env)
    assert proc.returncode == 0, f"{argv}: {proc.stderr}"
    return proc


def make_root(tmp_path: Path) -> tuple[Path, Path, dict]:
    stub = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"
    agents_yml = "agents:\n" + "".join(
        f'  {a}: {{cmd: "{stub}", enabled: true, timeout_min: 1}}\n' for a in AGENTS
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(agents_yml)

    ws = tmp_path / "workspace" / "2026-07-e2e"
    for d in ("prompts", "logs", "findings", "reviews", "report"):
        (ws / d).mkdir(parents=True)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_zot = bindir / "zot"
    fake_zot.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  *"collection list"*) echo "[]" ;;\n'
        '  *"add --doi"*) echo \'{"key": "K1"}\' ;;\n'
        '  *"export"*) echo "@article{stub, title={Stub Paper One}}" ;;\n'
        "  *) echo '{}' ;;\n"
        "esac\n"
    )
    fake_zot.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    return tmp_path, ws, env


def test_lit_review_pipeline(tmp_path):
    root, ws, env = make_root(tmp_path)
    rel = ws.relative_to(root)

    # Phase 2: search fan-out
    for a in AGENTS:
        prompt = ws / "prompts" / f"search-{a}.md"
        prompt.write_text(f"search task\noutput: {rel}/findings/{a}.json\nkind: findings\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", a, str(prompt),
            str(ws / "logs" / f"search-{a}.log")], cwd=root)
        sh([sys.executable, "-m", "scieflow.research.validate",
            str(ws / "findings" / f"{a}.json")], cwd=root)

    # Phase 3: cross-review, every ordered pair
    for r, a in itertools.permutations(AGENTS, 2):
        prompt = ws / "prompts" / f"review-{r}-on-{a}.md"
        prompt.write_text(f"review task\noutput: {rel}/reviews/{r}-on-{a}.json\nkind: review\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", r, str(prompt),
            str(ws / "logs" / f"review-{r}-on-{a}.log")], cwd=root)
        sh([sys.executable, "-m", "scieflow.research.validate",
            str(ws / "reviews" / f"{r}-on-{a}.json"), "--schema", "review"], cwd=root)
    assert len(list((ws / "reviews").glob("*.json"))) == 6

    # Phase 4 output (synthesis itself is agent judgment; its file contract is:)
    (ws / "report" / "selected_dois.txt").write_text("10.0000/stub.1\n")

    # Phase 5: export via fake zot
    sh([sys.executable, "-m", "scieflow.research.zotero",
        "--workspace", str(ws)], cwd=root, env=env)
    bib = (ws / "report" / "references.bib").read_text()
    assert "@article" in bib
