import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = ["-m", "scieflow.research.zotero"]

FAKE_ZOT = """#!/usr/bin/env bash
echo "$@" >> "$ZOT_LOG"
case "$*" in
  *"collection list"*) echo '{"ok": true, "data": [{"key": "COLL1", "name": "Existing", "parent_key": null, "children": []}]}' ;;
  *"collection create"*) echo '{"ok": true, "data": {"key": "COLL2", "name": "ScieFlow/2026-07-test", "parent_key": null, "children": []}}' ;;
  *"add --doi"*) echo '{"ok": true, "data": {"key": "ITEM1"}}' ;;
  *"collection move"*) echo '{"ok": true}' ;;
  *"export"*) echo '@article{stub2024, title={Stub Paper One}}' ;;
  *) echo '{}' ;;
esac
"""


def make_env(tmp_path: Path) -> tuple[Path, Path, dict]:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        "agents:\n  stub: {cmd: x, enabled: true}\n"
    )
    ws = tmp_path / "workspace" / "2026-07-test"
    (ws / "report").mkdir(parents=True)
    (ws / "report" / "selected_dois.txt").write_text("10.0000/stub.1\n\n10.0000/stub.2\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    zot = bindir / "zot"
    zot.write_text(FAKE_ZOT)
    zot.chmod(0o755)
    import os

    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "ZOT_LOG": str(tmp_path / "zot.log")}
    return tmp_path, ws, env


def run(root: Path, ws: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *SCRIPT, "--workspace", str(ws)],
        capture_output=True,
        text=True,
        cwd=root,
        env=env,
    )


def test_full_export_flow(tmp_path):
    root, ws, env = make_env(tmp_path)
    proc = run(root, ws, env)
    assert proc.returncode == 0, proc.stderr
    log = (tmp_path / "zot.log").read_text()
    assert "collection create ScieFlow/2026-07-test" in log
    assert log.count("add --doi") == 2
    assert log.count("collection move ITEM1") == 2
    bib = (ws / "report" / "references.bib").read_text()
    assert bib.count("@article") == 2


def test_group_library_flag_passed(tmp_path):
    root, ws, env = make_env(tmp_path)
    (ws / "config.yml").write_text("zotero:\n  library: group:999\n")
    proc = run(root, ws, env)
    assert proc.returncode == 0, proc.stderr
    assert "--library group:999" in (tmp_path / "zot.log").read_text()


def test_missing_dois_file_fails_cleanly(tmp_path):
    root, ws, env = make_env(tmp_path)
    (ws / "report" / "selected_dois.txt").unlink()
    proc = run(root, ws, env)
    assert proc.returncode != 0
    assert "selected_dois.txt" in proc.stderr


def test_no_doi_tail_is_skipped(tmp_path):
    root, ws, env = make_env(tmp_path)
    (ws / "report" / "selected_dois.txt").write_text(
        "10.0000/stub.1\n10.0000/stub.2\n# no-doi\nSome Paper Without A DOI\n"
    )
    proc = run(root, ws, env)
    assert proc.returncode == 0, proc.stderr
    log = (tmp_path / "zot.log").read_text()
    assert log.count("add --doi") == 2
    bib = (ws / "report" / "references.bib").read_text()
    assert bib.count("@article") == 2
