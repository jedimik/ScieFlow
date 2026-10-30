"""M-C2 t2 fixtures: a synthetic project, a fake HTTP session (no network) and a fresh cache home.

Every transport here is a fake `requests`-style session. No test reaches a real service, and every
address used is built at run time under the reserved example.org domain.
"""

from __future__ import annotations

import json
import xml.sax.saxutils
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


# --- M-C2 t3: search-source helpers ----------------------------------------------------------

SOURCE_DIR = REPO / "tests" / "fixtures" / "kernel2" / "sources"


def recorded(source: str, key: str) -> dict:
    """One converted researcher answer, found by the first characters of its request key."""
    found = sorted((SOURCE_DIR / source).glob(f"{key}*.json"))
    assert len(found) == 1, found
    return json.loads(found[0].read_text(encoding="utf-8"))


@pytest.fixture
def cli_project(tmp_path, monkeypatch):
    """A synthetic project for the search commands; the cache home lives under tmp_path."""
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.chdir(root)
    return root, ws


@pytest.fixture
def fast_send(monkeypatch):
    """One attempt per request and no limiter waits (the real retry and limiter logic still runs)."""
    real = providers.http.send

    def send(*args, **kwargs):
        kwargs["retries"] = 1
        kwargs["sleep"] = lambda seconds: None
        return real(*args, **kwargs)

    monkeypatch.setattr(providers.http, "send", send)
    monkeypatch.setattr(providers.http.RateLimiter, "wait", lambda self: None)


@pytest.fixture
def cli_run(capsys):
    """run(main, argv) -> (exit code, stdout, stderr) of one search command."""

    def run(main, argv):
        try:
            main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def pubmed_xml(articles: list) -> str:
    """The efetch XML that carries the fields of a recorded article (a synthetic rendering).

    The researcher answers were recorded as parsed articles, not as XML. This writes the XML
    PubMed sends for those fields, so the parser is tested against real element shapes.
    """

    def esc(value) -> str:
        return xml.sax.saxutils.escape(str(value or ""))

    parts = ['<?xml version="1.0" ?>', "<PubmedArticleSet>"]
    for art in articles:
        pmid = esc(art.get("pmid"))
        parts.append(f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>")
        parts.append(f"<Journal><Title>{esc(art.get('journal'))}</Title><JournalIssue>")
        if art.get("year") is not None:
            parts.append(f"<PubDate><Year>{int(art['year'])}</Year></PubDate>")
        parts.append("</JournalIssue></Journal>")
        parts.append(f"<ArticleTitle>{esc(art.get('title'))}.</ArticleTitle>")
        if art.get("abstract"):
            parts.append(f"<Abstract><AbstractText>{esc(art['abstract'])}</AbstractText></Abstract>")
        parts.append("</Article></MedlineCitation><PubmedData><ArticleIdList>")
        parts.append(f'<ArticleId IdType="pubmed">{pmid}</ArticleId>')
        if art.get("doi"):
            parts.append(f'<ArticleId IdType="doi">{esc(art["doi"])}</ArticleId>')
        parts.append("</ArticleIdList></PubmedData></PubmedArticle>")
    parts.append("</PubmedArticleSet>")
    return "".join(parts)


@pytest.fixture
def answer():
    """`answer(source, key)`: one converted researcher answer (see `recorded`)."""
    return recorded


@pytest.fixture
def xml_of():
    """`xml_of(articles)`: the PubMed efetch XML for recorded articles (see `pubmed_xml`)."""
    return pubmed_xml
