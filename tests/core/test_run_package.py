import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_importing_the_package_does_no_io(tmp_path):
    # A fresh interpreter outside any repo: importing must not look for one.
    done = subprocess.run([sys.executable, "-c", "import scieflow.core.run.status"],
                          cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_new_status_carries_a_stable_id():
    from scieflow.core.run import status

    st = status.new_status("run-a", "autonomous")
    assert len(st["id"]) == 26
    assert status.PHASES == list(st["phases"])


def test_ensure_id_adds_one_to_an_old_run_and_keeps_it(tmp_path):
    from scieflow.core.run import status

    old = status.new_status("old", "per-campaign")
    del old["id"]
    status.write_status(tmp_path, old)
    first = status.ensure_id(tmp_path)
    assert status.ensure_id(tmp_path) == first
    assert status.read_status(tmp_path)["id"] == first


def test_legacy_imports_are_the_package_modules():
    sys.path.insert(0, str(ROOT / "scripts"))
    import budget
    import checkpoint
    import status

    from scieflow.core.run import budget as b2, checkpoint as c2, status as s2

    assert status is s2 and budget is b2 and checkpoint is c2


def test_legacy_scripts_still_run_as_commands(tmp_path):
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate.py"), "--help"],
                          capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0 and "--schema" in done.stdout
