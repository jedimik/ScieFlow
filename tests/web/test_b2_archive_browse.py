"""T-9 t5: browsing a ready archive, member responses (T9-D11), read-only GETs and the credential spy (5.11 items 1-3)."""

import builtins
import io
import os
import socket
import subprocess

from scieflow.core import archives as a
from tests.fixtures.b2_archives import tree_snapshot
from tests.fixtures.b2_web import arch_project, dvc_bin, pointer_only, ready_archive, web, zip_bytes  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def test_directory_navigation(arch_project, web):
    ready_archive(arch_project, "r1", zip_bytes({"notes/log.md": b"hi\n", "status.yml": b"kind: loop\n"}))
    root = web.get("/archives/r1/files").text
    assert 'href="/archives/r1/files?path=notes"' in root
    assert "/archives/r1/file?path=status.yml" in root
    inner = web.get("/archives/r1/files", params={"path": "notes"}).text
    assert "/archives/r1/file?path=notes/log.md" in inner or "/archives/r1/file?path=notes%2Flog.md" in inner


def test_entries_paginate_and_say_so(arch_project, web):
    members = {f"f{i:04d}.txt": b"x" for i in range(a.PAGE_ENTRIES + 1)}
    ready_archive(arch_project, "r1", zip_bytes(members))
    body = web.get("/archives/r1/files").text
    assert "entries 1-500 of 501" in body
    assert "page=2" in body


def test_text_member_is_inline_and_autoescaped(arch_project, web):
    ready_archive(arch_project, "r1")
    r = web.get("/archives/r1/file", params={"path": "notes/log.md"})
    assert r.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert "<script>alert(1)" not in r.text


def test_png_member_is_inline_with_nosniff(arch_project, web):
    ready_archive(arch_project, "r1", zip_bytes({"figs/a.png": PNG}))
    r = web.get("/archives/r1/file", params={"path": "figs/a.png"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert "content-disposition" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content == PNG


def test_svg_member_is_an_attachment(arch_project, web):
    ready_archive(arch_project, "r1", zip_bytes({"figs/a.svg": b"<svg onload=alert(1)></svg>"}))
    r = web.get("/archives/r1/file", params={"path": "figs/a.svg"})
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith('attachment; filename="a.svg"')
    assert r.headers["x-content-type-options"] == "nosniff"


def test_download_flag_forces_an_attachment(arch_project, web):
    ready_archive(arch_project, "r1")
    r = web.get("/archives/r1/file", params={"path": "notes/log.md", "download": "1"})
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith('attachment; filename="log.md"')
    assert r.content == b"<script>alert(1)</script>\n"


def test_missing_member_is_404_with_the_sentence(arch_project, web):
    ready_archive(arch_project, "r1")
    r = web.get("/archives/r1/file", params={"path": "nope.txt"})
    assert r.status_code == 404
    assert "There is no such member in this archive." in r.text


def test_corrupt_member_gives_the_corrupt_sentence(arch_project, web):
    data = bytearray(zip_bytes({"notes/log.md": b"alpha beta gamma\n"}))
    data[data.index(b"alpha")] = ord("x")          # stored member: its CRC no longer matches
    ready_archive(arch_project, "r1", bytes(data))
    r = web.get("/archives/r1/file", params={"path": "notes/log.md"})
    assert "damaged in the archive" in r.text
    assert "Traceback" not in r.text


def test_browse_before_ready_says_not_ready(arch_project, web):
    pointer_only(arch_project, "r1")
    assert "This archive is not downloaded and verified yet." in web.get("/archives/r1/files").text


def test_api_reads_return_the_service_dicts(arch_project, web):
    ready_archive(arch_project, "r1")
    assert web.get("/api/v1/archives").json()["rows"][0]["slug"] == "r1"
    assert web.get("/api/v1/archives/r1").json()["state"] == "ready"
    assert web.get("/api/v1/archives/r1/entries").json()["total"] >= 1


def test_gets_write_nothing_start_nothing_and_never_open_credentials(arch_project, web, monkeypatch):
    ready_archive(arch_project, "r1", zip_bytes({"notes/log.md": b"hi\n", "data.yml": b"version: 1\n"}))
    (arch_project.root / ".env").write_text("SECRET=fake-marker\n")
    (arch_project.root / ".dvc").mkdir(exist_ok=True)
    (arch_project.root / ".dvc" / "config").write_text("[core]\nremote = fake\n")
    root, state = arch_project.root, arch_project.state_dir      # paths captured before any patch
    root_before = tree_snapshot(root)
    state_before = tree_snapshot(state)
    assert state_before                                          # the record the page reads exists

    opened: list[str] = []
    real_io_open = io.open

    def spy(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            opened.append(os.fspath(file))
        return real_io_open(file, *args, **kwargs)

    def no_process(*args, **kwargs):
        raise AssertionError("a GET started a process")

    def no_socket(*args, **kwargs):
        raise AssertionError("a GET opened a socket")

    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    monkeypatch.setattr(socket.socket, "connect", no_socket)

    bodies = [web.get(path).text for path in (
        "/", "/archives/r1", "/archives/r1/files", "/archives/r1/file?path=notes/log.md",
        "/api/v1/archives", "/api/v1/archives/r1", "/api/v1/archives/r1/entries")]

    assert tree_snapshot(root) == root_before
    assert tree_snapshot(state) == state_before
    assert not [p for p in opened if p.endswith(".env") or "/.dvc/config" in p]
    assert all("SECRET=fake-marker" not in body for body in bodies)
