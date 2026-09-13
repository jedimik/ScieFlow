import inspect
from types import SimpleNamespace

import pytest

from nblm import policy, session

PROFILE = policy.Profile(
    name="default",
    session_state="/nonexistent/state.json",
    notebook_prefix="scieflow-",
    allowed_ops=["check", "verify"],
    source_hosts=["arxiv.org"],
    limits={"max_sources_per_notebook": 50, "max_source_mb": 50,
            "max_questions_per_run": 40, "max_questions_per_day": 45,
            "max_claims_per_question": 5, "reask_threshold": "partial"},
)


class FakeClient:
    """Shaped like the real library: every API method is a coroutine."""

    def __init__(self, notebooks=None, answer=None, fail=None, error=None):
        self.calls = []
        self._notebooks = notebooks or []
        self._answer = answer
        self._fail = fail
        self._error = error or RuntimeError("upstream exploded")
        self.notebooks = SimpleNamespace(list=self._list, create=self._create)
        self.sources = SimpleNamespace(add_file=self._add_file, list=self._src_list)
        self.chat = SimpleNamespace(ask=self._ask)

    def _boom(self, what):
        if self._fail == what:
            raise self._error

    async def _list(self):
        self.calls.append(("notebooks.list",))
        self._boom("notebooks.list")
        return self._notebooks

    async def _create(self, title):
        self.calls.append(("notebooks.create", title))
        self._boom("notebooks.create")
        return {"id": "nb-new", "title": title}

    async def _add_file(self, notebook_id, file_path, wait=False, wait_timeout=None):
        self.calls.append(("sources.add_file", notebook_id, file_path, wait))
        self._boom("sources.add_file")
        return SimpleNamespace(id="src-1")

    async def _src_list(self, notebook_id):
        self.calls.append(("sources.list", notebook_id))
        return [{"id": "src-1"}]

    async def _ask(self, notebook_id, question, source_ids=None):
        self.calls.append(("chat.ask", notebook_id, question, source_ids))
        self._boom("chat.ask")
        return self._answer


def sess(client):
    return session.Session(PROFILE, client=client)


# --- the contract with the real library --------------------------------

try:
    import notebooklm
except ImportError:                      # the optional dependency is absent
    notebooklm = None

requires_upstream = pytest.mark.skipif(
    notebooklm is None,
    reason="optional dependency absent: uv sync --group notebooklm")


@requires_upstream
def test_upstream_api_is_async_and_from_storage_is_a_context_manager():
    """The shape session.py is written against. A sync fake cannot catch this."""
    from notebooklm import NotebookLMClient
    from notebooklm.client import ChatAPI, NotebooksAPI, SourcesAPI

    for cls, name in [(NotebooksAPI, "list"), (NotebooksAPI, "create"),
                      (SourcesAPI, "add_file"), (SourcesAPI, "list"),
                      (ChatAPI, "ask")]:
        method = getattr(cls, name)
        assert inspect.iscoroutinefunction(method), f"{cls.__name__}.{name}"

    assert not inspect.iscoroutinefunction(NotebookLMClient.from_storage)
    returned = inspect.signature(NotebookLMClient.from_storage).return_annotation
    assert "Context" in str(returned), returned


@requires_upstream
def test_upstream_keyword_names_still_match():
    from notebooklm.client import ChatAPI, NotebooksAPI, SourcesAPI
    assert "title" in inspect.signature(NotebooksAPI.create).parameters
    add_file_params = inspect.signature(SourcesAPI.add_file).parameters
    assert "file_path" in add_file_params
    assert "wait" in add_file_params
    ask_params = inspect.signature(ChatAPI.ask).parameters
    assert "question" in ask_params
    assert "source_ids" in ask_params      # native scoping; prose scoping fails


@requires_upstream
def test_upstream_exposes_the_errors_we_map():
    assert issubclass(notebooklm.AuthError, Exception)
    assert issubclass(notebooklm.RateLimitError, Exception)


# --- behaviour ---------------------------------------------------------

def test_build_context_without_session_never_imports_upstream():
    with pytest.raises(session.NoSession, match="notebooklm_cli login"):
        session.build_context(PROFILE)


def test_ensure_notebook_reuses_existing():
    client = FakeClient(notebooks=[{"id": "nb-1", "title": "scieflow-run"}])
    with sess(client) as s:
        assert s.ensure_notebook("scieflow-run") == "nb-1"
    assert ("notebooks.create", "scieflow-run") not in client.calls


def test_ensure_notebook_creates_when_absent():
    with sess(FakeClient(notebooks=[])) as s:
        assert s.ensure_notebook("scieflow-run") == "nb-new"


def test_ensure_notebook_refuses_name_outside_prefix():
    client = FakeClient()
    with sess(client) as s, pytest.raises(policy.PolicyError, match="notebook_prefix"):
        s.ensure_notebook("private-notes")
    assert client.calls == []


def test_ping_counts_notebooks():
    with sess(FakeClient(notebooks=[{"id": "a"}, {"id": "b"}])) as s:
        assert s.ping() == 2


def test_add_file_returns_source_id():
    client = FakeClient()
    with sess(client) as s:
        assert s.add_file("nb-1", "/tmp/a.pdf") == "src-1"
    # wait=True: a question asked before ingestion finishes sees an empty source
    assert client.calls[-1] == ("sources.add_file", "nb-1", "/tmp/a.pdf", True)


def test_list_sources():
    with sess(FakeClient()) as s:
        assert s.list_sources("nb-1") == [{"id": "src-1"}]


def test_ask_reads_either_object_or_dict_answers():
    for raw, expected in [
        (SimpleNamespace(text="from attr", citations=["c1"]), "from attr"),
        ({"answer": "from dict", "sources": []}, "from dict"),
    ]:
        with sess(FakeClient(answer=raw)) as s:
            assert s.ask("nb", "q?").text == expected


def test_upstream_failure_becomes_session_error():
    with sess(FakeClient(fail="chat.ask")) as s:
        with pytest.raises(session.SessionError, match="chat.ask failed"):
            s.ask("nb-1", "q?")


@requires_upstream
def test_auth_error_becomes_no_session():
    client = FakeClient(fail="notebooks.list", error=notebooklm.AuthError("expired"))
    with sess(client) as s:
        with pytest.raises(session.NoSession, match="no longer valid"):
            s.ping()


@requires_upstream
def test_rate_limit_error_is_its_own_stop_signal():
    client = FakeClient(fail="chat.ask", error=notebooklm.RateLimitError("slow down"))
    with sess(client) as s:
        with pytest.raises(session.RateLimited, match="never retry"):
            s.ask("nb-1", "q?")


def test_closing_releases_the_loop_and_reopening_works():
    client = FakeClient(notebooks=[{"id": "a"}])
    s = sess(client)
    with s:
        assert s.ping() == 1
    assert s._loop is None
    with s:                      # a second use must not reuse a closed loop
        assert s.ping() == 1
