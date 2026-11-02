"""latexdiff for `scieflow manuscript diff` (M-G 5.9, 5.16): fixed argv, cwd, minimal env, timeout, capped output.

latexdiff is not installed on this host, so the subprocess is replaced by a fake that writes its stdout and
stderr into the files it is handed (the code passes files, never pipes, so output is never held in memory).
The argv, working folder, environment, timeout and the output cap are the things under test. The real-binary
test skips where latexdiff is absent, looking on the same minimal PATH the code uses.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

import pytest

from scieflow.research.manuscript import diff as diff_mod
from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.points.point_support import cli

TINY_MAIN = "\\documentclass{article}\n\\begin{document}\nAlpha line.\n\\end{document}\n"
NEW_MAIN = TINY_MAIN.replace("Alpha line.", "Alpha changed.")
ARGV = ["latexdiff", "--encoding=utf8", "old.tex", "new.tex"]


@pytest.fixture
def pair(synth):
    synth.ms.joinpath("main.tex").write_text(TINY_MAIN, encoding="utf-8")
    assert cli("point", "add", RUN, "v1").exit_code == 0
    synth.ms.joinpath("main.tex").write_text(NEW_MAIN, encoding="utf-8")
    return synth


def scratch(synth):
    return synth.run_dir / "scratch" / "manuscript-diff"


def found(monkeypatch, location="/usr/bin/latexdiff"):
    monkeypatch.setattr(diff_mod.shutil, "which", lambda name, path=None: location)


def completed(stdout=b"% latexdiff output\n", stderr=b"", code=0):
    """A subprocess.run stand-in: writes the bytes into the stdout and stderr files the code passes in."""

    def fake(argv, **kwargs):
        kwargs["stdout"].write(stdout)
        kwargs["stderr"].write(stderr)
        return subprocess.CompletedProcess(args=argv, returncode=code, stdout=None, stderr=None)

    return fake


def test_argv_cwd_env_and_timeout_are_fixed(pair, monkeypatch):
    found(monkeypatch)
    calls = []
    real = completed()

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return real(argv, **kwargs)

    monkeypatch.setattr(diff_mod.subprocess, "run", fake_run)
    result = diff_mod.diff(pair.run_dir, "v1", "current")
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ARGV
    assert kwargs["cwd"] == scratch(pair)
    assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "HOME": str(scratch(pair))}
    assert kwargs["timeout"] == 120
    assert not kwargs.get("shell", False)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is not subprocess.PIPE and kwargs["stderr"] is not subprocess.PIPE
    assert result.latexdiff is True


def test_the_two_flattened_sides_are_written_for_latexdiff(pair, monkeypatch):
    found(monkeypatch)
    seen = {}
    real = completed()

    def fake_run(argv, **kwargs):
        seen["old"] = (kwargs["cwd"] / "old.tex").read_text(encoding="utf-8")
        seen["new"] = (kwargs["cwd"] / "new.tex").read_text(encoding="utf-8")
        return real(argv, **kwargs)

    monkeypatch.setattr(diff_mod.subprocess, "run", fake_run)
    diff_mod.diff(pair.run_dir, "v1", "current")
    assert "Alpha line." in seen["old"]
    assert "Alpha changed." in seen["new"]


def test_the_latexdiff_output_is_written_as_diff_tex(pair, monkeypatch):
    found(monkeypatch)
    monkeypatch.setattr(diff_mod.subprocess, "run", completed(stdout=b"\\DIFadd{x}\n"))
    assert cli("diff", RUN, "--from", "v1").exit_code == 0
    written = (pair.run_dir / "report" / "diff" / "v1..current" / "diff.tex").read_bytes()
    assert written == b"\\DIFadd{x}\n"
    assert not any(scratch(pair).glob("latexdiff.*"))


def test_absent_latexdiff_runs_no_subprocess(pair, monkeypatch):
    monkeypatch.setattr(diff_mod.shutil, "which", lambda name, path=None: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("no subprocess when latexdiff is absent")

    monkeypatch.setattr(diff_mod.subprocess, "run", forbidden)
    result = diff_mod.diff(pair.run_dir, "v1", "current")
    assert result.latexdiff is False
    assert result.notes == ["latexdiff-not-installed"]


def test_a_nonzero_exit_is_refused_with_the_last_20_stderr_lines_capped_at_60(pair, monkeypatch):
    found(monkeypatch)
    stderr = "".join(f"line {index} " + "x" * 100 + "\n" for index in range(30)).encode("utf-8")
    monkeypatch.setattr(diff_mod.subprocess, "run", completed(stderr=stderr, code=1))
    with pytest.raises(diff_mod.DiffError) as caught:
        diff_mod.diff(pair.run_dir, "v1", "current")
    assert caught.value.rule == "latexdiff-failed"
    message = caught.value.message
    shown = re.findall(r"line (\d+) x", message)
    assert shown == [str(index) for index in range(10, 30)]
    assert "x" * 61 not in message


def test_a_nonzero_exit_is_exit_1_with_the_stderr_tail_on_the_console(pair, monkeypatch):
    found(monkeypatch)
    monkeypatch.setattr(diff_mod.subprocess, "run", completed(stderr=b"! Undefined control sequence.\n", code=1))
    result = cli("diff", RUN, "--from", "v1")
    assert result.exit_code == 1, result.output
    assert "diff refused [latexdiff-failed]" in result.output
    assert "! Undefined control sequence." in result.output
    assert not (pair.run_dir / "report" / "diff" / "v1..current").exists()


def test_a_failed_latexdiff_writes_no_report(pair, monkeypatch):
    found(monkeypatch)
    monkeypatch.setattr(diff_mod.subprocess, "run", completed(stderr=b"! bad\n", code=1))
    with pytest.raises(diff_mod.DiffError):
        diff_mod.diff(pair.run_dir, "v1", "current")
    out = pair.run_dir / "report" / "diff" / "v1..current"
    assert not (out / "changes.json").exists()
    assert not (out / "diff.txt").exists()
    assert not (out / "diff.tex").exists()


def test_a_timeout_is_refused(pair, monkeypatch):
    found(monkeypatch)

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(diff_mod.subprocess, "run", slow)
    with pytest.raises(diff_mod.DiffError) as caught:
        diff_mod.diff(pair.run_dir, "v1", "current")
    assert caught.value.rule == "latexdiff-timeout"
    assert not any(scratch(pair).glob("latexdiff.*"))


def test_output_over_20_mb_is_refused(pair, monkeypatch):
    found(monkeypatch)
    big = b"%" * (20 * 1024 * 1024 + 1)
    monkeypatch.setattr(diff_mod.subprocess, "run", completed(stdout=big))
    with pytest.raises(diff_mod.DiffError) as caught:
        diff_mod.diff(pair.run_dir, "v1", "current")
    assert caught.value.rule == "latexdiff-output"
    assert not (pair.run_dir / "report" / "diff" / "v1..current" / "diff.tex").exists()
    assert not any(scratch(pair).glob("latexdiff.*"))


@pytest.mark.parametrize("name", [diff_mod.OUT_NAME, diff_mod.ERR_NAME])
def test_a_planted_link_at_a_latexdiff_output_is_refused_and_the_victim_is_untouched(pair, monkeypatch, tmp_path,
                                                                                   name):
    found(monkeypatch)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    assert pair.run_dir.resolve() not in victim.resolve().parents
    scratch(pair).mkdir(parents=True, exist_ok=True)
    (scratch(pair) / name).symlink_to(victim)
    monkeypatch.setattr(diff_mod.subprocess, "run", completed(stdout=b"PATCHED\n", stderr=b"PATCHED\n"))
    with pytest.raises(diff_mod.DiffError) as caught:
        diff_mod.diff(pair.run_dir, "v1", "current")
    assert caught.value.rule == "symlink"
    assert name in caught.value.message
    assert victim.read_text(encoding="utf-8") == "keep me\n"
    assert not (pair.run_dir / "report" / "diff" / "v1..current").exists()


def test_latexdiff_runs_under_a_file_size_limit_of_the_output_cap_plus_one_byte(pair, monkeypatch):
    found(monkeypatch)
    probe = ("import resource, sys; soft = resource.getrlimit(resource.RLIMIT_FSIZE)[0]; "
             "sys.stderr.write('FSIZE=%d' % soft); sys.exit(1)")
    monkeypatch.setattr(diff_mod, "LATEXDIFF_ARGV", [sys.executable, "-c", probe])
    with pytest.raises(diff_mod.DiffError) as caught:
        diff_mod.diff(pair.run_dir, "v1", "current")
    assert caught.value.rule == "latexdiff-failed"
    assert f"FSIZE={diff_mod.LATEXDIFF_STDOUT_CAP + 1}" in caught.value.message


def test_output_written_past_the_cap_while_latexdiff_runs_is_refused_and_leaves_nothing(pair, monkeypatch):
    found(monkeypatch)
    probe = f"import sys; sys.stdout.write('%' * {diff_mod.LATEXDIFF_STDOUT_CAP + 1024 * 1024})"
    monkeypatch.setattr(diff_mod, "LATEXDIFF_ARGV", [sys.executable, "-c", probe])
    with pytest.raises(diff_mod.DiffError) as caught:
        diff_mod.diff(pair.run_dir, "v1", "current")
    assert caught.value.rule == "latexdiff-output"
    assert not (pair.run_dir / "report" / "diff" / "v1..current" / "diff.tex").exists()
    assert not any(scratch(pair).glob("latexdiff.*"))


@pytest.mark.skipif(shutil.which("latexdiff", path=diff_mod.MINIMAL_PATH) is None,
                    reason="latexdiff is not installed on the minimal PATH")
def test_real_latexdiff_marks_the_change(pair):
    result = diff_mod.diff(pair.run_dir, "v1", "current")
    assert result.latexdiff is True
    assert (pair.run_dir / "report" / "diff" / "v1..current" / "diff.tex").is_file()
