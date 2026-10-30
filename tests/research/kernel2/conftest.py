"""M-C2 t2 fixtures: a synthetic project, a fake HTTP session (no network) and a fresh cache home.

Every transport here is a fake `requests`-style session. No test reaches a real service, and every
address used is built at run time under the reserved example.org domain.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scieflow.integrations.scite.cache import Cache
from scieflow.research.lib import providers

REPO = Path(__file__).resolve().parents[3]


class Resp:
    """The part of a requests response that the Gateway reads."""

    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeSession:
    """A fake session: records every call, always answers with the same status and body."""

    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = body

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "params": kwargs.get("params")})
        return Resp(self.status, self.body)


def no_sleep(_seconds):
    pass


@pytest.fixture
def project(tmp_path):
    """(root, run workspace, home for the cache and the day ledger)."""
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    return root, ws, tmp_path / "home"


@pytest.fixture
def fake_session():
    """The FakeSession class: `fake_session(status=..., body=...)` builds one."""
    return FakeSession


@pytest.fixture
def make_gateway(project):
    root, ws, home = project

    def make(session, *, cfg=None, policy=None, **kw):
        if cfg is not None:
            (root / "config" / "integrations.yml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        pol = policy if policy is not None else providers.load(root)
        kw.setdefault("sleep", no_sleep)
        kw.setdefault("retries", 1)
        return providers.Gateway(ws, pol, cache=Cache(home / "cache", pol.cache_ttl_days),
                                 ledger_dir=home / "day", session=session, **kw)

    return make


@pytest.fixture
def helpers(project):
    root, ws, _home = project

    def declare_l2():
        (ws / "data.yml").write_text(yaml.safe_dump({
            "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
            "released": [], "imports": []}, sort_keys=False), encoding="utf-8")

    def write_network(mode):
        (ws / "network.yml").write_text(yaml.safe_dump({
            "version": 1, "mode": mode, "set_by": "human", "set_at": "2026-10-30T10:00:00Z",
            "reason": "test"}), encoding="utf-8")

    def write_policy(text):
        (root / "config" / "data-policy.yml").write_text(text, encoding="utf-8")

    return SimpleNamespace(declare_l2=declare_l2, write_network=write_network,
                           write_policy=write_policy)
