import subprocess

import scieflow.news.gui.app as app_mod


def test_is_wsl_true(monkeypatch, tmp_path):
    fake = tmp_path / "version"
    fake.write_text("Linux version 6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setattr(app_mod, "Path", lambda _: fake)
    assert app_mod.is_wsl() is True


def test_is_wsl_false_when_missing(monkeypatch, tmp_path):
    fake = tmp_path / "nope"
    monkeypatch.setattr(app_mod, "Path", lambda _: fake)
    assert app_mod.is_wsl() is False


def test_open_browser_wsl_prefers_wslview(monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod, "is_wsl", lambda: True)
    monkeypatch.setattr(app_mod.shutil, "which", lambda name: "/usr/bin/wslview")
    monkeypatch.setattr(
        app_mod.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd)
    )
    app_mod.open_browser("http://x")
    assert calls == [["wslview", "http://x"]]


def test_open_browser_wsl_falls_back_to_explorer(monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod, "is_wsl", lambda: True)
    monkeypatch.setattr(app_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        app_mod.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd)
    )
    app_mod.open_browser("http://x")
    assert calls == [["explorer.exe", "http://x"]]


def test_open_browser_non_wsl_uses_webbrowser(monkeypatch):
    opened = []
    monkeypatch.setattr(app_mod, "is_wsl", lambda: False)
    monkeypatch.setattr(app_mod.webbrowser, "open", lambda url: opened.append(url))
    app_mod.open_browser("http://x")
    assert opened == ["http://x"]


def test_open_browser_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no display")

    monkeypatch.setattr(app_mod, "is_wsl", lambda: False)
    monkeypatch.setattr(app_mod.webbrowser, "open", boom)
    app_mod.open_browser("http://x")  # must not raise
