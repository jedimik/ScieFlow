"""The one path every scite request takes: policy -> cache -> ledger -> transport.

`request()` is what the REST and MCP clients (and the evidence executor) call.
The transport is injected — a callable `(op, params) -> (value, result_count)`
— so the order of checks is enforced here once, and tests can assert that a
refused request never reaches the network.

- Every request belongs to a run (`ledger.ws`): that is where it is ledgered
  and where `integration.call` is emitted. MCP requests also need a round.
- MCP: the tool must be in `allowed_tools` and a token present; a cache hit is
  served even when the budget is spent (it costs nothing); otherwise the
  budget is checked *before* the transport is touched, under the ledger's
  machine-wide lock held until the call is recorded.
- REST (public tier, no key): only needs scite enabled; counted, not budgeted.
- Every real call (success or failure) is written to the ledger and emits
  `integration.call` before anything else can fail; cache hits are ledgered as
  `cached` and emit nothing. Failures are never cached and are re-raised for
  the caller to classify. A response that cannot be cached is still returned.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from scieflow.core import events, network
from scieflow.integrations.scite import policy as _policy
from scieflow.integrations.scite.cache import Cache, normalise_params, request_key
from scieflow.integrations.scite.ledger import Ledger

Transport = Callable[[str, dict], tuple[Any, int]]


@dataclass(frozen=True)
class Result:
    value: Any
    cached: bool
    results: int
    key: str


def _count(n) -> int | None:
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None


def request(policy: _policy.Policy, cache: Cache, ledger: Ledger, *, source: str, op: str,
            params: Mapping, round: int | None, ttl_class: str,  # noqa: A002
            transport: Transport) -> Result:
    network.require_live(ledger.ws, "scite")   # M-C1 5.7: refused in replay/offline, first
    if source == "mcp":
        _policy.check_tool(policy, op)
    elif source == "rest":
        _policy.check_rest(policy)
    else:
        raise ValueError(f"unknown source {source!r} (mcp or rest)")
    if ledger.ws is None:
        raise _policy.PolicyError("scite requests are made for a run (no run workspace given)")
    from scieflow.core.data import egress

    try:   # M-B 5.7: an L2 run's data is sent to scite only when config/data-policy.yml lists it
        egress.service_check(egress.root_of_run(ledger.ws), ledger.ws, "scite")
    except egress.EgressRefused as exc:
        raise _policy.PolicyError(str(exc)) from None
    if source == "mcp" and (not isinstance(round, int) or isinstance(round, bool) or round < 1):
        raise ValueError(f"MCP requests need a review round >= 1, got {round!r}")

    req = normalise_params(params)
    key = request_key(source, op, req)
    hit = cache.get(key, ttl_class)
    if isinstance(hit, dict) and "value" in hit:
        n = _count(hit.get("results")) or 0
        ledger.record(source=source, op=op, round=round, results=n,
                      cached=True, key=key, request=req)
        return Result(hit["value"], True, n, key)

    with ledger.reserve() if source == "mcp" else nullcontext():
        if source == "mcp":
            ledger.check(round)
        try:
            value, raw = transport(op, dict(req))
        except Exception as exc:
            kind = str(getattr(exc, "classification", None) or type(exc).__name__)
            _record(ledger, source, op, round, 0, key, req, kind)
            raise
        results = _count(raw)
        if results is None:
            _record(ledger, source, op, round, 0, key, req, "BAD_RESULT_COUNT")
            raise ValueError(f"transport for {op} returned an invalid result count")
        _record(ledger, source, op, round, results, key, req, None)

    try:
        json.dumps(value)
        cache.put(key, ttl_class, {"value": value, "results": results})
    except (TypeError, ValueError, OSError):
        pass  # not cacheable: the next identical request is a real call again
    return Result(value, False, results, key)


def _record(ledger: Ledger, source: str, op: str, round_, results: int, key: str,
            req: dict, error: str | None) -> None:
    assert ledger.ws is not None
    status = "ok" if error is None else "error"
    ledger.record(source=source, op=op, round=round_, results=results, status=status,
                  error=error, key=key, request=req)
    data = {"integration": "scite", "source": source, "op": op, "round": round_,
            "results": results, "status": status}
    if error is not None:
        data["error"] = error
    events.emit(ledger.ws, "integration.call", **data)
