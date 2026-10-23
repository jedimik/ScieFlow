"""scripts/check_ownership.sh (ticket 05, plan T0.8): the branch never touches files
owned by the ScieFlowDev session (web) or the K16 files.

Each case builds a throwaway git repository with the script, a base branch and a
feature branch, then runs the script against the base.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_ownership.sh"

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=base"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout


def write(repo: Path, rel: str, text: str = "x\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / "scripts").mkdir()
    shutil.copy(SCRIPT, r / "scripts" / "check_ownership.sh")
    for rel in ("src/scieflow/web/app.py", "src/scieflow/core/service.py",
                "src/scieflow/core/events.py", "config/defaults.yml", "README.md"):
        write(r, rel, "line 1\nline 2\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    git(r, "checkout", "-qb", "feature")
    return r


def check(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/check_ownership.sh", *args], cwd=repo,
                          capture_output=True, text=True)


def commit(repo: Path, msg: str = "change") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)


def test_passes_for_owned_files(repo):
    write(repo, "src/scieflow/core/review/service.py")
    write(repo, "tests/core/review/test_x.py")
    commit(repo)
    out = check(repo, "base")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "ok" in out.stdout


@pytest.mark.parametrize("rel", [
    "src/scieflow/web/app.py",
    "src/scieflow/web/templates/new.html",
    "tests/web/test_new.py",
    "src/scieflow/core/agent_run.py",
    "src/scieflow/core/service.py",
    "src/scieflow/core/jobs.py",
    "src/scieflow/core/sandbox.py",
    "src/scieflow/core/provenance.py",
    "src/scieflow/core/harness_commands.py",
    "src/scieflow/core/turn_router.py",
    "src/scieflow/core/run/conversation.py",
])
def test_fails_when_a_scieflowdev_or_k16_file_is_committed(repo, rel):
    write(repo, rel, "changed\n")
    commit(repo)
    out = check(repo, "base")
    assert out.returncode == 1
    assert rel in out.stdout


def test_fails_on_uncommitted_and_untracked_changes(repo):
    write(repo, "src/scieflow/core/service.py", "edited\n")
    out = check(repo, "base")
    assert out.returncode == 1 and "src/scieflow/core/service.py" in out.stdout
    git(repo, "checkout", "-q", "--", "src/scieflow/core/service.py")
    write(repo, "tests/web/test_new.py")
    out = check(repo, "base")
    assert out.returncode == 1 and "tests/web/test_new.py" in out.stdout


def test_deleting_or_renaming_an_owned_file_fails(repo):
    git(repo, "rm", "-q", "src/scieflow/web/app.py")
    commit(repo)
    out = check(repo, "base")
    assert out.returncode == 1 and "src/scieflow/web/app.py" in out.stdout


def test_similar_names_outside_the_owned_paths_pass(repo):
    write(repo, "src/scieflow/core/review/service.py")
    write(repo, "src/scieflow/webhooks.py")
    write(repo, "tests/core/test_service.py")
    write(repo, "src/scieflow/core/run/conversation_notes.md")
    commit(repo)
    out = check(repo, "base")
    assert out.returncode == 0, out.stdout


def test_shared_files_with_deleted_lines_warn_but_pass(repo):
    write(repo, "config/defaults.yml", "line 1\nline 3\n")
    write(repo, "src/scieflow/core/events.py", "line 1\nline 2\nline 3\n")
    commit(repo)
    out = check(repo, "base")
    assert out.returncode == 0
    assert "warning" in out.stdout and "config/defaults.yml" in out.stdout
    assert "src/scieflow/core/events.py" not in out.stdout   # purely additive


def test_unknown_base_is_an_error(repo):
    out = check(repo, "no-such-branch")
    assert out.returncode == 2
    assert "no-such-branch" in out.stdout + out.stderr


def test_this_branch_passes():
    # The live-tree guard protects the scite branches from K/web files. Once K is integrated
    # into main (K integration, G6 accepted 2026-10-23), the stale default base
    # dev/cloud-continuation makes every integrated K file look "touched" by construction, so
    # this live check is limited to scite branches. Unit tests above still cover the script.
    branch = subprocess.run(["git", "symbolic-ref", "--short", "-q", "HEAD"],
                            cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not branch.startswith("dev/scite"):
        pytest.skip(f"live ownership check applies to scite branches only (HEAD is {branch or 'detached'})")
    probe = subprocess.run(["git", "rev-parse", "--verify", "-q", "dev/cloud-continuation"],
                           cwd=ROOT, capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("dev/cloud-continuation is not available in this checkout")
    out = subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("rel", ["src/scieflow/web/é.py", 'src/scieflow/web/a"b.py',
                                 "src/scieflow/web/a\tb.py", "tests/web/with space.py"])
def test_quoted_paths_are_still_caught(repo, rel):
    write(repo, rel)
    out = check(repo, "base")                     # untracked
    assert out.returncode == 1, out.stdout
    commit(repo)
    out = check(repo, "base")                     # committed
    assert out.returncode == 1, out.stdout
