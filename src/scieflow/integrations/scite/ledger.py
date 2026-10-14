"""Who called what, how often: the scite call ledger and its budgets.

Two append-only JSONL files get the same row for every request:

- per run: `workspace/<slug>/scite/ledger.jsonl`
- per day: `~/.cache/scieflow/scite/ledger-YYYY-MM-DD.jsonl` (UTC; shared by
  every run on this machine, so the daily budget spans runs)

Each row records the source (`mcp`, `rest`, or a free discovery provider:
`pubtator`, `ctgov`, `openalex`), the operation, the round,
the result count (scite's metering per call vs per result is undocumented, so
both are kept), whether it was served from cache, and its status. Cache hits
cost nothing; only uncached MCP calls count against `max_mcp_calls_per_round`,
`max_mcp_calls_per_run` and `max_mcp_calls_per_day`. Failed calls count — the
server may have metered them.

When a run or day budget drops to 10 % remaining, `scite.quota_low` is emitted
once per run. The per-round budget is a planned boundary every round reaches,
so it never triggers the warning.

Budget check, call and record happen under one machine-wide lock
(`reserve()`), so two runs cannot both spend the last call of the day.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

from scieflow.core import events, store
from scieflow.integrations.scite.policy import Policy, PolicyError

# Free discovery providers (research/lib/providers.py) reuse this ledger with their
# own name as the source; only uncached `mcp` rows count against the budgets.
PROVIDER_SOURCES = ("pubtator", "ctgov", "openalex")
SOURCES = ("mcp", "rest", *PROVIDER_SOURCES)
LOW_FRACTION = 0.10
# An MCP call holds the budget lock from check to record, so concurrent runs
# cannot both spend the last call of the day. Calls are few and bounded.
BUDGET_LOCK_TIMEOUT = 600


class BudgetError(PolicyError):
    """A scite budget in config/scite.yml is spent. A boundary: never retried around."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path(os.environ.get("HOME", "~")) / ".cache")
    return Path(base).expanduser() / "scieflow" / "scite"


def _rows(path: Path) -> list[dict]:
    try:
        return [r for r in store.read_jsonl(path) if isinstance(r, dict)]
    except (OSError, ValueError):
        return []


class Ledger:
    def __init__(self, ws: Path | None, policy: Policy, day_dir: Path | None = None,
                 now: Callable[[], datetime] = _utcnow):
        self.ws = Path(ws) if ws is not None else None
        self.policy = policy
        self.day_dir = Path(day_dir) if day_dir is not None else default_dir()
        self.now = now

    # --- files -----------------------------------------------------------------

    @property
    def run_file(self) -> Path | None:
        return self.ws / "scite" / "ledger.jsonl" if self.ws is not None else None

    def day_file(self) -> Path:
        day = self.now().astimezone(timezone.utc).date().isoformat()
        return self.day_dir / f"ledger-{day}.jsonl"

    def rows(self, scope: str = "run") -> list[dict]:
        if scope == "day":
            return _rows(self.day_file())
        if scope == "run":
            return _rows(self.run_file) if self.run_file is not None else []
        raise ValueError(f"unknown ledger scope {scope!r} (run or day)")

    # --- recording -------------------------------------------------------------

    def record(self, *, source: str, op: str, round: int | None, results: int,  # noqa: A002
               cached: bool = False, status: str = "ok", error: str | None = None,
               key: str | None = None, request: Mapping | None = None) -> dict:
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r} (one of {', '.join(SOURCES)})")
        if not isinstance(results, int) or isinstance(results, bool) or results < 0:
            raise ValueError(f"results must be a non-negative integer, got {results!r}")
        row = {
            "ts": self.now().astimezone(timezone.utc).isoformat(timespec="milliseconds"),
            "run": self.ws.name if self.ws is not None else None,
            "round": round,
            "source": source,
            "op": op,
            "cached": bool(cached),
            "status": status,
            "results": results,
        }
        if error is not None:
            row["error"] = error
        if key is not None:
            row["key"] = key
        if request is not None:
            row["request"] = dict(request)
        if self.run_file is not None:
            store.append_jsonl(self.run_file, row)
        store.append_jsonl(self.day_file(), row)
        if source == "mcp" and not cached:
            self._maybe_warn()
        return row

    # --- counting --------------------------------------------------------------

    @staticmethod
    def _select(rows, source, round_, cached):
        return [r for r in rows
                if r.get("source") == source and bool(r.get("cached")) is cached
                and (round_ is None or r.get("round") == round_)]

    def calls(self, scope: str = "run", source: str = "mcp", round: int | None = None) -> int:  # noqa: A002
        return len(self._select(self.rows(scope), source, round, False))

    def results(self, scope: str = "run", source: str = "mcp", round: int | None = None) -> int:  # noqa: A002
        return sum(int(r.get("results") or 0)
                   for r in self._select(self.rows(scope), source, round, False))

    def cached(self, scope: str = "run", source: str = "mcp") -> int:
        return len(self._select(self.rows(scope), source, None, True))

    def remaining(self, round: int | None) -> dict:  # noqa: A002
        """MCP calls left per budget; None = no such budget (unset, or no run/round)."""
        p = self.policy
        run_rows = self.rows("run")
        out: dict[str, int | None] = {"round": None, "run": None, "day": None}
        if self.ws is not None and round is not None:
            used = len(self._select(run_rows, "mcp", round, False))
            out["round"] = max(p.max_mcp_calls_per_round - used, 0)
        if self.ws is not None:
            out["run"] = max(p.max_mcp_calls_per_run - len(self._select(run_rows, "mcp", None, False)), 0)
        if p.max_mcp_calls_per_day is not None:
            out["day"] = max(p.max_mcp_calls_per_day - self.calls("day"), 0)
        return out

    def check(self, round: int | None, n: int = 1) -> None:  # noqa: A002
        """Raise BudgetError if `n` more MCP calls would exceed any budget."""
        left = self.remaining(round)
        for name in ("round", "run", "day"):
            if left[name] is not None and left[name] < n:
                limit = {"round": self.policy.max_mcp_calls_per_round,
                         "run": self.policy.max_mcp_calls_per_run,
                         "day": self.policy.max_mcp_calls_per_day}[name]
                raise BudgetError(f"scite MCP budget per {name} spent "
                                  f"({limit - left[name]}/{limit} calls used; "
                                  f"max_mcp_calls_per_{name} in config/scite.yml)")

    @contextmanager
    def reserve(self) -> Iterator[None]:
        """Hold the machine-wide budget lock around check -> call -> record."""
        self.day_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.day_dir / "mcp-budget.lock"), timeout=BUDGET_LOCK_TIMEOUT):
            yield

    # --- low-quota warning -----------------------------------------------------

    def _maybe_warn(self) -> None:
        if self.ws is None:
            return
        left = self.remaining(None)
        limits = {"run": self.policy.max_mcp_calls_per_run,
                  "day": self.policy.max_mcp_calls_per_day}
        for name in ("run", "day"):
            limit, rem = limits[name], left[name]
            if limit is None or rem is None or rem > limit * LOW_FRACTION:
                continue
            if events.read(self.ws, types=("scite.quota_low",)):
                return
            events.emit(self.ws, "scite.quota_low", budget=name, remaining=rem, limit=limit)
            return
