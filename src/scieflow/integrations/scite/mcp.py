"""scite MCP client (coordinator side only), over the official `mcp` SDK.

`MCPClient.call(tool, args, round=N)` calls one scite MCP retrieval tool
(`search_literature`, `citation_graph`, … as `allowed_tools` permits) through
`gateway.request` (policy → cache → ledger → transport), so every attempt is
budgeted, ledgered and announced with `integration.call`, and an identical
second request is a free cache hit. The SDK is the optional dependency group
`scite` (`uv sync --group scite`); without it the client raises
`NOT_INSTALLED` with that hint before anything is sent.

Order of checks — each a boundary, reported and never retried around:

1. **Inside a ScieFlow dispatch → `DispatchRefused`** (a `PolicyError`, exit 3)
   before the policy is consulted or the token read: sub-agents never reach
   scite. Detection (`in_dispatch`) is a heuristic until K16 lands an explicit
   marker: the sandbox gives every dispatch its own uv cache,
   `UV_CACHE_DIR=<writable area>/.uv-cache` (`core.sandbox.cache_dir`), while
   a coordinator shell uses the shared host cache. `SCIEFLOW_DISPATCH=1` is
   honoured too, so the planned explicit marker works the day it is set.
   A hostile agent can unset either; this guards against accidental use.
   Known gap: a dispatch run with the sandbox disabled (`sandbox_enabled` off
   or disabled for the run) gets no per-dispatch `UV_CACHE_DIR` and is not
   detected until the explicit marker lands (tracked K16 follow-up).
2. Policy (`check_tool`: scite on, tool allowed, token present).
3. SDK installed (`NOT_INSTALLED`).
4. The token is read at call time (env var or token file named by the policy)
   and sent only as `Authorization: Bearer …`. It never reaches the ledger,
   cache, events or error text: server messages are redacted and truncated.

Errors are classified (`MCPFailure.classification`):

- `AUTH` — HTTP 401/403 or an auth message. Not retried.
- `QUOTA` — HTTP 402, or a quota / credits / usage-limit message (also on a
  429). Not retried. `AUTH` and `QUOTA` emit `scite.unavailable` once per run.
- `RATE` — HTTP 429 or a rate-limit message. Retried after `Retry-After`
  (bounded by `retries`); a `Retry-After` above `max_retry_after` is a boundary.
- `TRANSIENT` — 5xx, 408, network errors, unparseable or malformed answers
  (incl. a payload without the documented `hits` / `edges`). Bounded jittered
  retry.
- `TOOL_ERROR` — the tool refused the arguments (JSON-RPC invalid params, other
  4xx, an `isError` result that is none of the above), or the SDK failed in an
  unexpected way. Not retried: repeating it would only spend more calls.
- `NOT_INSTALLED` — the `scite` group is missing.

Every retry is a new gateway request: it is ledgered and counts against the
budget (scite may meter failed calls), so retries stop at the budget too.

The SDK hides HTTP statuses behind a generic JSON-RPC error, so the adapter
(`sdk_rpc`) watches POST responses with an httpx2 event hook and hands the
client an `RPCError` with status, `Retry-After` and a short, redacted body
excerpt. It sends `tools/call` with `ClientSession.send_request` rather than
`call_tool`: the SDK's `call_tool` follows every successful call with a
`tools/list` to validate output schemas, and a failure of that follow-up would
void (and, via retry, repeat) a call scite has already metered. One session is
initialize → tools/call → close: exactly one metered request.
`sdk_rpc` drives the async SDK with `anyio.run`; call it from synchronous
coordinator code (CLI), not from inside a running event loop.

How scite meters MCP usage is not documented; the ledger keeps both call and
result counts (`hits` for `search_literature`, `edges` for `citation_graph`).
"""

from __future__ import annotations

import importlib.util
import os
import re
import time
from collections.abc import Callable, Mapping
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any
import json
import random

from scieflow.core import events
from scieflow.core import sandbox
from scieflow.integrations.scite import gateway
from scieflow.integrations.scite import policy as _policy
from scieflow.integrations.scite.cache import Cache
from scieflow.integrations.scite.ledger import Ledger

MCP_URL = "https://api.scite.ai/mcp"
INSTALL_HINT = "uv sync --group scite"
CLASSIFICATIONS = ("AUTH", "QUOTA", "RATE", "TRANSIENT", "TOOL_ERROR", "NOT_INSTALLED")
BACKOFF = 1.0
MAX_BACKOFF = 30.0
# CLI exit codes, as for the claim-check module (scripts/nblm): 2 auth, 3 policy, 4 quota/rate,
# 5 not installed.
EXIT_AUTH = 2
EXIT_QUOTA = 4
EXIT_NOT_INSTALLED = 5
# Count field per tool: what a "result" is for the ledger. Missing → malformed answer.
_RESULT_LISTS = {"search_literature": "hits", "citation_graph": "edges"}

# Order matters: rate-limit wording wins (bounded retry, honours Retry-After), then
# quota (stops), then auth. Only explicit quota vocabulary counts as QUOTA.
_RATE = re.compile(r"rate.?limit|too many requests|slow down|throttl", re.IGNORECASE)
_QUOTA = re.compile(r"quota|credits?\b|usage limit|monthly limit|plan limit|billing|"
                    r"payment required|subscription", re.IGNORECASE)
_AUTH = re.compile(r"unauthori[sz]ed|forbidden|invalid[\w\s]{0,20}token|authenticat|api key|"
                   r"credentials|token (?:expired|revoked)|expired token|insufficient.scope|"
                   r"permission", re.IGNORECASE)
_TOOL_CODES = (-32600, -32601, -32602)
_TEXT_MAX = 200


class DispatchRefused(_policy.PolicyError):
    """scite was asked for from inside a ScieFlow dispatch. Sub-agents never call scite."""


class MCPFailure(Exception):
    """A classified scite MCP failure (see the module doc). Never carries the token."""

    def __init__(self, message: str, classification: str, *, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.classification = classification
        self.status = status
        self.retry_after = retry_after


class RPCError(Exception):
    """What the transport adapter saw, before classification: HTTP status, JSON-RPC error
    code, `Retry-After`, a short body excerpt, or that the answer was malformed / the
    network failed. Built with the token already redacted."""

    def __init__(self, message: str, *, status: int | None = None, code: int | None = None,
                 retry_after: str | None = None, body: str | None = None,
                 malformed: bool = False, network: bool = False, unexpected: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.retry_after = retry_after
        self.body = body
        self.malformed = malformed
        self.network = network
        self.unexpected = unexpected


RPC = Callable[[str, dict], dict]            # (tool, args) -> CallToolResult as a JSON dict
RPCFactory = Callable[[str, str], RPC]       # (url, token) -> RPC


def exit_code(exc: BaseException) -> int:
    """CLI exit code: 3 policy/dispatch refusal, 2 auth, 4 quota/rate, 5 not installed, else 1."""
    if isinstance(exc, _policy.PolicyError):
        return _policy.EXIT_REFUSED_POLICY
    if isinstance(exc, MCPFailure):
        return {"AUTH": EXIT_AUTH, "QUOTA": EXIT_QUOTA, "RATE": EXIT_QUOTA,
                "NOT_INSTALLED": EXIT_NOT_INSTALLED}.get(exc.classification, 1)
    return 1


# --- dispatch detection --------------------------------------------------------------------


def in_dispatch(environ: Mapping[str, str] | None = None) -> bool:
    """True when this process looks like a ScieFlow dispatch (heuristic, see module doc)."""
    env = os.environ if environ is None else environ
    if (env.get("SCIEFLOW_DISPATCH") or "").strip().lower() in ("1", "true", "yes"):
        return True
    uv = (env.get("UV_CACHE_DIR") or "").strip()
    return bool(uv) and PurePath(uv).name == sandbox.CACHE_DIRNAME


def refuse_in_dispatch(environ: Mapping[str, str] | None = None) -> None:
    if in_dispatch(environ):
        raise DispatchRefused(
            "scite refused: this looks like a ScieFlow dispatch (per-dispatch UV_CACHE_DIR "
            f"…/{sandbox.CACHE_DIRNAME} or SCIEFLOW_DISPATCH=1). Only the coordinator calls "
            "scite; reviewers read the evidence bundle.")


def sdk_available() -> bool:
    try:
        return (importlib.util.find_spec("mcp") is not None
                and importlib.util.find_spec("httpx2") is not None)
    except (ImportError, ValueError):
        return False


# --- classification -------------------------------------------------------------------------


def _redact(text, token: str | None) -> str:
    s = str(text or "")
    if token:
        s = s.replace(token, "***")
    s = " ".join(s.split())
    return s if len(s) <= _TEXT_MAX else s[:_TEXT_MAX] + "…"


def _seconds(value) -> float | None:
    """Retry-After as seconds (delta-seconds or HTTP-date); None when absent or unreadable."""
    if value is None:
        return None
    s = str(value).strip()
    try:
        return max(float(s), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((when - datetime.now(timezone.utc)).total_seconds(), 0.0)


def _by_text(text: str) -> str | None:
    if _RATE.search(text):
        return "RATE"
    if _QUOTA.search(text):
        return "QUOTA"
    if _AUTH.search(text):
        return "AUTH"
    return None


def classify(err: RPCError, token: str | None = None) -> MCPFailure:
    """Map what the transport saw to an `MCPFailure` (see the module doc)."""
    text = f"{err.message or ''} {err.body or ''}"
    status, code = err.status, err.code
    if status in (401, 403):
        cls = "AUTH"
    elif status == 402:
        cls = "QUOTA"
    elif status == 429:
        cls = "QUOTA" if _by_text(text) == "QUOTA" else "RATE"
    elif status is not None and (status == 408 or status >= 500):
        cls = "TRANSIENT"
    elif status is not None and status >= 400:
        cls = _by_text(text) or "TOOL_ERROR"
    elif err.unexpected:
        cls = "TOOL_ERROR"
    elif err.malformed or err.network:
        cls = "TRANSIENT"
    else:
        cls = _by_text(text) or ("TOOL_ERROR" if code in _TOOL_CODES else "TRANSIENT")
    where = f"HTTP {status}" if status is not None else (
        f"JSON-RPC {code}" if code is not None else "transport")
    msg = f"scite MCP {cls} ({where}): {_redact(err.message, token)}"
    if cls == "AUTH":
        msg += " — check the token named in config/scite.yml"
    return MCPFailure(msg, cls, status=status,
                      retry_after=_seconds(err.retry_after) if cls == "RATE" else None)


def _malformed(tool: str, why: str) -> MCPFailure:
    return MCPFailure(f"scite MCP TRANSIENT: malformed {tool} answer ({why})", "TRANSIENT")


def parse_result(tool: str, result, token: str | None = None) -> tuple[Any, int]:
    """CallToolResult dict → (value, result count). Raises `MCPFailure` for a tool error
    (`isError`) or a malformed answer. Prefers `structuredContent`; otherwise the first
    text content item must hold JSON."""
    if not isinstance(result, dict):
        raise _malformed(tool, "not an object")
    if result.get("isError") is True:
        content = result.get("content") if isinstance(result.get("content"), list) else []
        text = " ".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
        cls = _by_text(text) or "TOOL_ERROR"
        raise MCPFailure(f"scite MCP {cls} ({tool} tool error): {_redact(text, token)}", cls)
    value = result.get("structuredContent")
    if not isinstance(value, (dict, list)):
        content = result.get("content")
        if not isinstance(content, list):
            raise _malformed(tool, "content is not a list")
        texts = [c.get("text") for c in content
                 if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)]
        if not texts:
            raise _malformed(tool, "no text content")
        try:
            value = json.loads(texts[0])
        except ValueError:
            raise _malformed(tool, "text content is not JSON") from None
    key = _RESULT_LISTS.get(tool)
    if key is not None:
        if not isinstance(value, dict) or not isinstance(value.get(key), list):
            raise _malformed(tool, f"no '{key}' list")
        return value, len(value[key])
    return value, len(value) if isinstance(value, list) else 1


# --- the SDK adapter --------------------------------------------------------------------------


def _leaves(exc: BaseException) -> list[BaseException]:
    subs = getattr(exc, "exceptions", None)
    if isinstance(subs, (list, tuple)) and subs:
        out: list[BaseException] = []
        for s in subs:
            out.extend(_leaves(s))
        return out
    return [exc]


def sdk_rpc(url: str, token: str, *, timeout: float = 60.0, http_transport=None) -> RPC:
    """An RPC over the official SDK's streamable-HTTP client: one session per call
    (initialize, then tools/call). `http_transport` (an httpx2 transport) is for tests."""
    import anyio
    import httpx2
    from mcp import ClientSession
    from mcp import types as mcp_types
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.exceptions import MCPError
    from pydantic import ValidationError

    def _rpc_error(exc: BaseException, seen: dict) -> RPCError:
        leaves = _leaves(exc)
        mcp_err = next((e for e in leaves if isinstance(e, MCPError)), None)
        net = next((e for e in leaves if isinstance(e, (httpx2.TransportError,
                                                        TimeoutError))), None)
        bad = next((e for e in leaves if isinstance(e, (ValidationError, ValueError))), None)
        common = {"status": seen.get("status"), "retry_after": seen.get("retry_after"),
                  "body": seen.get("body")}
        if mcp_err is not None:
            return RPCError(_redact(mcp_err.message, token), code=mcp_err.code,
                            malformed=mcp_err.code == -32700, **common)
        if net is not None:
            return RPCError(f"network error ({type(net).__name__})", network=True, **common)
        if bad is not None:
            return RPCError(f"malformed answer ({type(bad).__name__})", malformed=True, **common)
        return RPCError(f"MCP session failed ({type(leaves[0]).__name__})", unexpected=True,
                        **common)

    def rpc(tool: str, args: dict) -> dict:
        seen: dict = {}

        async def watch(resp) -> None:
            if resp.request.method != "POST" or resp.status_code < 400 or "status" in seen:
                return
            seen["status"] = resp.status_code
            seen["retry_after"] = resp.headers.get("retry-after")
            try:
                await resp.aread()
                seen["body"] = _redact(resp.text, token)    # redact first, then truncate
            except Exception:  # noqa: BLE001 - the excerpt is optional
                pass

        async def run() -> dict:
            async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=httpx2.Timeout(timeout), transport=http_transport,
                    event_hooks={"response": [watch]}) as client:
                async with streamable_http_client(url, http_client=client) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timeout) as s:
                        await s.initialize()
                        # not call_tool(): see the module doc (no tools/list follow-up)
                        req = mcp_types.CallToolRequest(
                            params=mcp_types.CallToolRequestParams(name=tool, arguments=args))
                        res = await s.send_request(req, mcp_types.CallToolResult)
                        return res.model_dump(by_alias=True, mode="json", exclude_none=True)

        failure: RPCError | None = None
        try:
            out = anyio.run(run)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # noqa: BLE001 - classified below
            failure = _rpc_error(exc, seen)
        if failure is not None:
            # raised outside the except block: no __context__ chain to unredacted SDK errors
            raise failure
        if not isinstance(out, dict) or "content" not in out:
            raise RPCError("tools/call did not return a tool result", malformed=True)
        return out

    return rpc


# --- the client -------------------------------------------------------------------------------


class MCPClient:
    def __init__(self, policy: _policy.Policy, cache: Cache, ledger: Ledger, *,
                 url: str = MCP_URL, rpc_factory: RPCFactory | None = None,
                 environ: Mapping[str, str] | None = None, sleep=time.sleep,
                 jitter=random.random, retries: int = 2, max_retry_after: float = 60.0,
                 timeout: float = 60.0):
        self.policy = policy
        self.cache = cache
        self.ledger = ledger
        self.url = url
        self.rpc_factory = rpc_factory
        self.environ = environ
        self.sleep = sleep
        self.jitter = jitter
        self.retries = max(0, int(retries))
        self.max_retry_after = max_retry_after
        self.timeout = timeout

    def _token(self) -> str:
        env = os.environ if self.environ is None else self.environ
        p = self.policy
        try:
            value = (Path(p.token_file).expanduser().read_text(encoding="utf-8")
                     if p.token_file else env.get(p.token_env or "", ""))
        except (OSError, UnicodeDecodeError):
            value = ""
        value = (value or "").strip()
        if not value:
            raise _policy.PolicyError("scite MCP token missing at call time "
                                      "(see token_env / token_file in config/scite.yml)")
        return value

    def _mark_unavailable(self, reason: str, tool: str) -> None:
        ws = self.ledger.ws
        if ws is None:
            return
        with self.ledger.reserve():
            if not events.read(ws, types=("scite.unavailable",)):
                events.emit(ws, "scite.unavailable", reason=reason, tool=tool)

    def _delay(self, failure: MCPFailure, attempt: int) -> float | None:
        """Seconds to wait before the next attempt, or None when this is a boundary."""
        if failure.classification not in ("TRANSIENT", "RATE") or attempt >= self.retries:
            return None
        if failure.classification == "RATE" and failure.retry_after is not None:
            return failure.retry_after if failure.retry_after <= self.max_retry_after else None
        return min(MAX_BACKOFF, BACKOFF * (2 ** attempt) * (0.5 + self.jitter()))

    def call(self, tool: str, args: Mapping, *, round: int) -> gateway.Result:  # noqa: A002
        refuse_in_dispatch(self.environ)
        _policy.check_tool(self.policy, tool)
        if self.rpc_factory is None and not sdk_available():
            raise MCPFailure(f"scite MCP NOT_INSTALLED: the optional SDK is missing — "
                             f"install it with `{INSTALL_HINT}`", "NOT_INSTALLED")
        token = self._token()
        if self.rpc_factory is not None:
            rpc = self.rpc_factory(self.url, token)
        else:
            rpc = sdk_rpc(self.url, token, timeout=self.timeout)

        def transport(op: str, params: dict) -> tuple[Any, int]:
            try:
                result = rpc(op, params)
            except RPCError as exc:
                raise classify(exc, token) from None
            return parse_result(op, result, token)

        attempt = 0
        while True:
            try:
                return gateway.request(self.policy, self.cache, self.ledger, source="mcp",
                                       op=tool, params=args, round=round, ttl_class="search",
                                       transport=transport)
            except MCPFailure as failure:
                if failure.classification in ("AUTH", "QUOTA"):
                    self._mark_unavailable(failure.classification, tool)
                    raise
                delay = self._delay(failure, attempt)
                if delay is None:
                    raise
                self.sleep(delay)
                attempt += 1
