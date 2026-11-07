"""T-9 t1: the classifier (B2:5.9), the sentences and the disk rule (T9-D13).

Fixture lines are the DVC/botocore wordings named in B2:5.9 (not exercised against a live remote).
"""

import re
from collections import namedtuple
from pathlib import Path

import pytest

from scieflow.core import archives as a

SPEC = (Path(__file__).resolve().parents[2] / "docs" / "superpowers" / "specs"
        / "2026-11-03-b2-archived-run-browsing-design.md")

Usage = namedtuple("Usage", "total used free")


def _spec_sentences() -> dict[str, str]:
    section = SPEC.read_text(encoding="utf-8").split("### 5.9 Failure reasons", 1)[1]
    section = section.split("### 5.10", 1)[0]
    out = {}
    for line in section.splitlines():
        match = re.match(r'^\| `([a-z0-9-]+)` \| "(.*)" \| ', line)
        if match:
            out[match.group(1)] = match.group(2)
    return out


def test_sentences_equal_the_spec_table_word_for_word():
    spec = _spec_sentences()
    assert set(spec) == {"dvc-missing", "no-remote", "no-credentials", "credentials-refused",
                         "remote-unreachable", "missing-on-remote", "disk-full", "interrupted",
                         "verify-size", "verify-md5", "locked", "dvc-failed"}
    for code, text in spec.items():
        assert a.SENTENCES[code] == text, code


def test_sentence_fills_the_three_placeholders():
    assert a.sentence("dvc-failed", n=2, log_path="x/y.err") == (
        "dvc failed (exit 2); its output is in x/y.err on this machine.")
    assert a.sentence("missing-on-remote", md5="0123456789abcdef0123456789abcdef") == (
        "The remote does not have this archive (pointer md5 0123456789ab).")


@pytest.mark.parametrize("tail,code", [
    (b"ERROR: no remote specified", "no-remote"),
    (b"ERROR: remote doesn't exist", "no-remote"),
    (b"config file error: bad", "no-remote"),
    (b"Unable to locate credentials", "no-credentials"),
    (b"botocore.exceptions.NoCredentialsError: x", "no-credentials"),
    (b"no credentials", "no-credentials"),
    (b"An error occurred (InvalidAccessKeyId)", "credentials-refused"),
    (b"SignatureDoesNotMatch", "credentials-refused"),
    (b"AccessDenied", "credentials-refused"),
    (b"Access Denied", "credentials-refused"),
    (b"Forbidden", "credentials-refused"),
    (b"HTTP 403", "credentials-refused"),
    (b"ExpiredToken", "credentials-refused"),
    (b"Could not connect to the endpoint URL", "remote-unreachable"),
    (b"Name or service not known", "remote-unreachable"),
    (b"Temporary failure in name resolution", "remote-unreachable"),
    (b"Connection refused", "remote-unreachable"),
    (b"Network is unreachable", "remote-unreachable"),
    (b"read timed out", "remote-unreachable"),
    (b"missing cache files: abc", "missing-on-remote"),
    (b"do not exist neither locally nor on remote", "missing-on-remote"),
    (b"not found on remote", "missing-on-remote"),
    (b"write failed: No space left on device", "disk-full"),
    (b"something unknown happened", "dvc-failed"),
])
def test_each_b2_5_9_row_matches_its_fixture_line(tail, code):
    assert a.classify(1, tail) == code


def test_matching_is_case_insensitive():
    assert a.classify(1, b"NO SPACE LEFT ON DEVICE") == "disk-full"
    assert a.classify(1, b"nO cReDeNtIaLs") == "no-credentials"


def test_first_matching_row_wins_in_table_order():
    # no-remote is above credentials-refused in the table
    assert a.classify(1, b"no remote specified; AccessDenied") == "no-remote"


def test_only_the_last_dvc_output_tail_bytes_are_considered():
    old = b"no space left on device" + b"x" * a.DVC_OUTPUT_TAIL
    assert a.classify(1, old) == "dvc-failed"
    assert a.classify(1, b"x" * 10 + b"no space left on device") == "disk-full"


def test_exit_127_is_dvc_missing_whatever_the_output():
    assert a.classify(127, b"") == "dvc-missing"


def test_none_or_a_signal_is_interrupted():
    assert a.classify(None, b"no space left on device") == "interrupted"
    assert a.classify(-9, b"") == "interrupted"


def test_locked_exit_status_is_locked_and_unmatched_is_dvc_failed():
    assert a.classify(a.LOCKED_EXIT, b"") == "locked"
    assert a.classify(5, b"") == "dvc-failed"


def test_disk_rule_one_filesystem_boundary(tmp_path, monkeypatch):
    cache = tmp_path / ".dvc" / "cache"
    arch = tmp_path / "workspace" / "_archives"
    arch.mkdir(parents=True)
    monkeypatch.setattr(a, "_device", lambda path: 1)
    needed = a.disk_needed(200)
    monkeypatch.setattr(a.shutil, "disk_usage", lambda path: Usage(10**12, 0, needed))
    check = a.disk_check(tmp_path, [(cache, 100), (arch, 100)])
    assert check.ok is True
    assert len(check.groups) == 1
    assert check.groups[0]["received"] == 200 and check.groups[0]["needed"] == needed
    monkeypatch.setattr(a.shutil, "disk_usage", lambda path: Usage(10**12, 0, needed - 1))
    assert a.disk_check(tmp_path, [(cache, 100), (arch, 100)]).ok is False


def test_disk_rule_two_filesystems_each_group_is_checked_alone(tmp_path, monkeypatch):
    cache = tmp_path / "cache-fs"
    arch = tmp_path / "arch-fs"
    cache.mkdir()
    arch.mkdir()
    devs = {str(cache): 1, str(arch): 2}
    monkeypatch.setattr(a, "_device", lambda path: devs[str(path)])
    frees = {1: a.disk_needed(100), 2: a.disk_needed(100) - 1}
    monkeypatch.setattr(a.shutil, "disk_usage",
                        lambda path: Usage(10**12, 0, frees[devs[str(path)]]))
    check = a.disk_check(tmp_path, [(cache, 100), (arch, 100)])
    assert len(check.groups) == 2
    assert check.ok is False
    refused = [g for g in check.groups if g["free"] < g["needed"]]
    assert len(refused) == 1 and refused[0]["path"] == str(arch)


def test_a_receiver_that_does_not_exist_uses_its_nearest_existing_ancestor(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(a, "_device", lambda path: 7)

    def usage(path):
        seen.append(str(path))
        return Usage(10**12, 0, 10**12)

    monkeypatch.setattr(a.shutil, "disk_usage", usage)
    check = a.disk_check(tmp_path, [(tmp_path / "not" / "yet" / ".dvc" / "cache", 5)])
    assert check.ok is True
    assert seen == [str(tmp_path)]
