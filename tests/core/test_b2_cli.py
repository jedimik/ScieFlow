"""T-9 t4: the terminal commands `scieflow workspace archives` and `workspace archive …` (B2:5.11; T9-D7).

The commands call the service of the same name and print its result; refusals print the service's
message and exit 1. Project discovery is pointed at a temporary project.
"""

import hashlib
import io
import json
import sys
import types
import zipfile

import click
import pytest
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import archives as a
from scieflow.core import jobs, service
from scieflow.core.project import Project
from tests.fixtures.b2_archives import make_project, stat_record, write_pointer, write_record, write_zip, zip_path

MEMBERS = {"status.yml": b"kind: loop\nstate: running\nphase: p1\niteration: 2\n"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = make_project(tmp_path)
    monkeypatch.setattr(Project, "discover", lambda *args, **kwargs: proj)
    return proj


def ready(project, slug="r1"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in MEMBERS.items():
            zf.writestr(name, data)
    data = buf.getvalue()
    write_zip(project, slug, data)
    md5 = hashlib.md5(data).hexdigest()
    write_pointer(project, slug, md5=md5, size=len(data))
    write_record(project, slug, stat_record(zip_path(project, slug), pointer_md5=md5))


def run(*args):
    return CliRunner().invoke(main, ["workspace", *args])


def test_archives_json_lists_every_pointer(project):
    ready(project)
    result = run("archives", "--json")
    assert result.exit_code == 0, result.output
    assert [row["slug"] for row in json.loads(result.output)["rows"]] == ["r1"]


def test_archives_text_has_one_line_per_archive(project):
    ready(project)
    result = run("archives")
    assert result.exit_code == 0
    assert result.output.splitlines()[0].startswith("r1  zip")
    assert "ready" in result.output and "archived-only" in result.output


def test_show_prints_the_manuscript_sentence(project):
    ready(project)
    result = run("archive", "show", "r1")
    assert result.exit_code == 0, result.output
    assert "Manuscript history is not kept in archives." in result.output


def test_ls_lists_the_members_of_a_ready_archive(project):
    ready(project)
    result = run("archive", "ls", "r1")
    assert result.exit_code == 0 and "status.yml" in result.output


def test_cat_prints_a_text_member(project):
    ready(project)
    result = run("archive", "cat", "r1", "status.yml")
    assert result.exit_code == 0, result.output
    assert result.output == MEMBERS["status.yml"].decode()


def test_fetch_at_the_threshold_prints_the_command_and_starts_nothing(project, monkeypatch):
    write_pointer(project, "r1", size=a.CONSENT_BYTES)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/dvc")
    monkeypatch.setattr(a, "disk_check", lambda root, receivers: a.DiskCheck(True, []))

    def boom(*args, **kwargs):
        raise AssertionError("a refused download started a job")
    monkeypatch.setattr(jobs, "start", boom)

    result = run("archive", "fetch", "r1")
    assert result.exit_code == 1
    assert "scieflow workspace archive fetch r1 --yes" in result.output


def test_restore_inside_a_dispatch_refuses(project, monkeypatch):
    ready(project)
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    result = run("archive", "restore", "r1", "--yes")
    assert result.exit_code == 1
    assert "dispatch" in result.output


def test_cancel_with_nothing_running_says_so(project):
    ready(project)
    result = run("archive", "cancel", "r1")
    assert result.exit_code == 0
    assert "No archive job is running for this run." in result.output


def test_an_unknown_slug_is_an_error_exit(project):
    result = run("archive", "show", "nope")
    assert result.exit_code != 0
    assert "no archived run nope" in result.output


def ready_members(project, members, slug="r1"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    data = buf.getvalue()
    write_zip(project, slug, data)
    md5 = hashlib.md5(data).hexdigest()
    write_pointer(project, slug, md5=md5, size=len(data))
    write_record(project, slug, stat_record(zip_path(project, slug), pointer_md5=md5))


def test_fetch_yes_reaches_the_service_through_the_cli(project, monkeypatch):
    calls = []

    def fake(proj, slug, **kwargs):
        calls.append((slug, kwargs))
        return {"slug": slug, "action": "fetch", "state": "ready", "started": True, "job_id": "j",
                "outcome": "ready", "reason": None, "exit_code": 0, "log": "x",
                "message": "Downloaded and verified."}
    monkeypatch.setattr(service, "fetch_archive", fake)

    result = run("archive", "fetch", "r1", "--yes", "--replace")

    assert result.exit_code == 0, result.output
    assert "Downloaded and verified." in result.output
    assert calls == [("r1", {"replace": True, "yes": True, "wait": True})]


def test_cat_refuses_a_binary_member_on_a_terminal_without_out(project, monkeypatch):
    from scieflow.core.archives_cli import cat_cmd
    ready_members(project, {"data.bin": b"\x00\xff\x10"})
    tty = types.SimpleNamespace(isatty=lambda: True, buffer=io.BytesIO())
    monkeypatch.setattr(sys, "stdout", tty)

    with pytest.raises(click.ClickException, match="this member is binary: use --out FILE"):
        cat_cmd.main(["r1", "data.bin"], standalone_mode=False)
    assert tty.buffer.getvalue() == b""


def test_cat_out_writes_the_member_bytes(project, tmp_path):
    ready_members(project, {"data.bin": b"\x00\xff\x10"})
    target = tmp_path / "copy.bin"

    result = run("archive", "cat", "r1", "data.bin", "--out", str(target))

    assert result.exit_code == 0, result.output
    assert target.read_bytes() == b"\x00\xff\x10"
