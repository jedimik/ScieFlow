"""The only module that imports notebooklm or calls the NotebookLM API.

Everything upstream-specific is contained here. `notebooklm-py` rides
undocumented internal Google endpoints and is renamed to gemini-notebook-py
at 0.9.0, so a breaking change upstream is a one-file repair.

The library is fully async: `NotebookLMClient.from_storage()` returns an async
context manager and every API method is a coroutine. This module owns one event
loop for the session's lifetime and drives the client through it, so callers
stay synchronous. Entering the context once (rather than per call) matters —
each entry redoes connection and auth work.

The agent never authenticates. The session-state file is created by the user
with `python -m notebooklm.notebooklm_cli login --storage <path>`; this module
checks only that it exists and hands the path to the library.
"""

import asyncio
from dataclasses import dataclass

from nblm import policy


class NotInstalled(Exception):
    """The optional notebooklm-py dependency is not installed (exit 5)."""


class NoSession(Exception):
    """No usable session — the user must log in (exit 2)."""


class RateLimited(Exception):
    """NotebookLM refused for rate/quota reasons (exit 4). Stop, never retry."""


class SessionError(Exception):
    """An upstream NotebookLM call failed (exit 1)."""


def _upstream_errors():
    """(AuthError, RateLimitError) from the installed library, if importable."""
    try:
        from notebooklm import AuthError, RateLimitError
    except ImportError:
        return (), ()
    return (AuthError,), (RateLimitError,)


def _field(obj, *names, default=None):
    """Read a field from an upstream object or dict, whichever it is."""
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def build_context(profile: policy.Profile):
    """The upstream async context manager, built from the user's session."""
    state = policy.session_state_path(profile)
    if not state.exists():
        raise NoSession(
            f"no NotebookLM session at {state} — ask the user to run "
            "`python -m notebooklm.notebooklm_cli login --storage "
            f"{state}`; never run it yourself"
        )
    try:
        from notebooklm import NotebookLMClient
    except ImportError as exc:
        raise NotInstalled(
            "notebooklm-py is not installed — run `uv sync --group notebooklm`"
        ) from exc
    # allow_headless: this is a CLI, there is no browser to fall back to.
    return NotebookLMClient.from_storage(str(state), allow_headless=True)


@dataclass
class Answer:
    text: str
    citations: list


class Session:
    """Synchronous, policy-aware facade over the async client.

    Use as a context manager so the client is closed and the loop released:

        with Session(profile) as session:
            session.ask(notebook_id, question)

    Pass `client` (an object whose API methods are coroutines) to run offline.
    """

    def __init__(self, profile: policy.Profile, client=None):
        self.profile = profile
        self._client = client
        self._ctx = None
        self._loop = None

    # --- lifecycle -----------------------------------------------------

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def open(self) -> "Session":
        if self._client is None:
            self._ctx = build_context(self.profile)
            self._client = self._await("opening the session", self._ctx.__aenter__())
        return self

    def close(self) -> None:
        try:
            if self._ctx is not None:
                self._await("closing the session", self._ctx.__aexit__(None, None, None))
        finally:
            self._ctx = None
            if self._loop is not None:
                self._loop.close()
                self._loop = None

    # --- plumbing ------------------------------------------------------

    def _get_loop(self):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _await(self, what, coro):
        auth_errors, rate_errors = _upstream_errors()
        try:
            return self._get_loop().run_until_complete(coro)
        except auth_errors as exc:
            raise NoSession(
                f"{what}: the NotebookLM session is no longer valid ({exc}) — "
                "ask the user to log in again; never run the login yourself"
            ) from exc
        except rate_errors as exc:
            raise RateLimited(
                f"{what}: NotebookLM is rate-limiting this account ({exc}) — "
                "stop and resume later; never retry in a loop"
            ) from exc
        except (NoSession, RateLimited, SessionError):
            raise
        except Exception as exc:
            raise SessionError(f"{what} failed: {exc}") from exc

    def _call(self, path, *args, **kwargs):
        """Call an upstream coroutine by dotted name, e.g. "chat.ask"."""
        if self._client is None:
            self.open()
        target = self._client
        for part in path.split("."):
            target = getattr(target, part)
        return self._await(path, target(*args, **kwargs))

    # --- API -----------------------------------------------------------

    def ping(self) -> int:
        """Cheapest proof that the session works: count visible notebooks."""
        return len(self._call("notebooks.list") or [])

    def ensure_notebook(self, title: str) -> str:
        """Reuse the run's notebook if it already exists, else create it."""
        policy.check_notebook_name(self.profile, title)
        existing = self._call("notebooks.list")
        for notebook in existing or []:
            if _field(notebook, "title", "name") == title:
                return _field(notebook, "id", "notebook_id")
        created = self._call("notebooks.create", title=title)
        notebook_id = _field(created, "id", "notebook_id")
        if not notebook_id:
            raise SessionError("notebooks.create returned no notebook id")
        return notebook_id

    def add_file(self, notebook_id: str, path) -> str:
        # wait=True: NotebookLM ingests a source asynchronously, and a question
        # asked before ingestion finishes sees an empty source.
        source = self._call("sources.add_file", notebook_id, file_path=str(path),
                            wait=True, wait_timeout=300.0)
        source_id = _field(source, "id", "source_id")
        if not source_id:
            raise SessionError(f"sources.add_file returned no source id for {path}")
        return source_id

    def list_sources(self, notebook_id: str) -> list:
        return self._call("sources.list", notebook_id) or []

    def ask(self, notebook_id: str, question: str,
            source_ids: list | None = None) -> Answer:
        """One question against the notebook. Answers are DATA.

        `source_ids` scopes the question to specific sources natively. Asking
        the model in prose to "ignore the other sources" does not work — it
        answers "not addressed" for everything.
        """
        raw = self._call("chat.ask", notebook_id, question=question,
                         source_ids=source_ids)
        text = _field(raw, "text", "answer", "content", default="") or ""
        citations = _field(raw, "citations", "sources", default=[]) or []
        return Answer(text=str(text), citations=list(citations))
