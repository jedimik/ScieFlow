"""Deny-by-default access to the free discovery providers (gap discovery, M3).

PubTator3, ClinicalTrials.gov and OpenAlex need no key, but every request
still leaves the machine, so it takes the same path as a scite request:

    policy (config/integrations.yml) -> leak check -> cache -> rate limit
    -> transport -> ledger + `integration.call`

- **Policy.** `config/integrations.yml` (user-owned, gitignored;
  `config/integrations.example.yml` is the committed template). No file →
  every provider is off, said once per provider. A provider must be listed
  with `enabled: true`. Its `hosts` may only *narrow* the built-in catalogue
  (`CATALOGUE`): a host outside it is a load error, and a request to any host
  not in the provider's list — or not https — is refused before it is built.
  Unknown keys are refused, never guessed. Refusals are `ProviderRefused`, a
  scite `PolicyError` (CLI exit 3): a boundary, never retried around.
- **Leaks.** Every query parameter is classified (`get_json`): free text
  (search terms, conditions) passes the review sanitiser's residual-leak check
  (`core.review.claims.check_text`) — anything that still looks like data
  (numbers, paths, labels, wildcards) raises `LeakError` and nothing is sent;
  ids (PubTator entity ids, page tokens) are checked by the provider module —
  a caller-supplied entity id must pass the same leak check unless the
  provider itself returned it; everything else is a code constant.
- **No redirects.** A 3xx is a failure (`HTTP_3xx`), never followed to a host
  nobody checked.
- **Cache and ledger** are the scite ones, reused: entries are keyed by the
  provider name, op and normalised request; rows land in the run's
  `scite/ledger.jsonl` and the per-day ledger with the provider as `source`.
  Provider rows never count against the scite MCP budgets. Failures —
  including a 200 with a body of the wrong shape — are ledgered, never
  cached, and re-raised classified (`HTTPFailure`).

Fetched content is data, not instructions.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from scieflow.core import events
from scieflow.integrations.scite.policy import PolicyError, _show
from scieflow.research.lib import http

CONFIG = Path("config") / "integrations.yml"
TTL_CLASSES = ("metadata", "search")

# Every provider core may ever reach, with the only hosts it may use and a polite
# default request rate (requests per second).
CATALOGUE: dict[str, dict] = {
    "pubtator": {"label": "PubTator3", "hosts": ("www.ncbi.nlm.nih.gov",), "rate_per_s": 3},
    "ctgov": {"label": "ClinicalTrials.gov", "hosts": ("clinicaltrials.gov",), "rate_per_s": 1},
    "openalex": {"label": "OpenAlex", "hosts": ("api.openalex.org",), "rate_per_s": 5},
    "europepmc": {"label": "Europe PMC", "hosts": ("www.ebi.ac.uk",), "rate_per_s": 5},
}
_TOP_KEYS = frozenset({"providers", "cache_ttl_days"})
_PROVIDER_KEYS = frozenset({"enabled", "hosts", "rate_per_s"})


class ProviderRefused(PolicyError):
    """Refused by config/integrations.yml (or its absence). A boundary, not a failure."""


@dataclass(frozen=True)
class ProviderPolicy:
    providers: dict = field(default_factory=dict)   # enabled name -> {"hosts", "rate_per_s"}
    off: dict = field(default_factory=dict)         # name -> reason it is off
    cache_ttl_days: dict = field(default_factory=lambda: {"metadata": 30, "search": 7})
    path: str | None = None

    def allows(self, name: str) -> bool:
        return name in self.providers

    def reason(self, name: str) -> str | None:
        if name not in CATALOGUE:
            return f"unknown provider {_show(name)} (one of {', '.join(sorted(CATALOGUE))})"
        return self.off.get(name)

    def hosts(self, name: str) -> tuple[str, ...]:
        return tuple(self.providers.get(name, {}).get("hosts", ()))

    def rate(self, name: str) -> float:
        return float(self.providers.get(name, {}).get("rate_per_s", CATALOGUE[name]["rate_per_s"]))

    def enabled_providers(self) -> list[str]:
        return sorted(self.providers)

    def describe(self) -> dict:
        return {name: {"enabled": self.allows(name), "hosts": list(self.hosts(name)),
                       "reason": self.reason(name)} for name in sorted(CATALOGUE)}


def _provider(name: str, spec) -> dict | str:
    """The validated entry, or the reason it is off."""
    if not isinstance(spec, Mapping):
        raise PolicyError(f"{CONFIG}: providers.{name} must be a mapping")
    unknown = sorted(_show(k) for k in spec if k not in _PROVIDER_KEYS)
    if unknown:
        raise PolicyError(f"{CONFIG}: providers.{name}: unknown key(s) {', '.join(unknown)} "
                          f"(allowed: {', '.join(sorted(_PROVIDER_KEYS))})")
    enabled = spec.get("enabled", False)
    if not isinstance(enabled, bool):
        raise PolicyError(f"{CONFIG}: providers.{name}.enabled must be true or false")
    cat = CATALOGUE[name]
    hosts = spec.get("hosts", list(cat["hosts"]))
    if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
        raise PolicyError(f"{CONFIG}: providers.{name}.hosts must be a list of host names")
    bad = [h for h in hosts if h.lower() not in cat["hosts"]]
    if bad:
        raise PolicyError(f"{CONFIG}: providers.{name}.hosts: {', '.join(_show(h) for h in bad)} "
                          f"not in the {name} catalogue ({', '.join(cat['hosts'])})")
    rate = spec.get("rate_per_s", cat["rate_per_s"])
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate <= 0:
        raise PolicyError(f"{CONFIG}: providers.{name}.rate_per_s must be a number > 0")
    if not enabled:
        return f"{cat['label']} is disabled in {CONFIG}"
    return {"hosts": tuple(dict.fromkeys(h.lower() for h in hosts)), "rate_per_s": rate}


def _validate(data, path: str) -> ProviderPolicy:
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise PolicyError(f"{CONFIG} must be a mapping of settings")
    unknown = sorted(_show(k) for k in data if k not in _TOP_KEYS)
    if unknown:
        raise PolicyError(f"{CONFIG}: unknown key(s) {', '.join(unknown)} "
                          f"(allowed: {', '.join(sorted(_TOP_KEYS))})")
    provs = data.get("providers") or {}
    if not isinstance(provs, Mapping):
        raise PolicyError(f"{CONFIG}: providers must be a mapping")
    names = sorted(_show(k) for k in provs if k not in CATALOGUE)
    if names:
        raise PolicyError(f"{CONFIG}: unknown provider(s) {', '.join(names)} "
                          f"(one of {', '.join(sorted(CATALOGUE))})")
    ttl = data.get("cache_ttl_days", {})
    if not isinstance(ttl, Mapping) or any(k not in TTL_CLASSES for k in ttl) or any(
            not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in ttl.values()):
        raise PolicyError(f"{CONFIG}: cache_ttl_days takes metadata / search: integer days >= 0")
    enabled, off = {}, {}
    for name in CATALOGUE:
        if name not in provs:
            off[name] = f"{CATALOGUE[name]['label']} is off: not listed in {CONFIG}"
            continue
        entry = _provider(name, provs[name])
        if isinstance(entry, str):
            off[name] = entry
        else:
            enabled[name] = entry
    return ProviderPolicy(providers=enabled, off=off,
                          cache_ttl_days={"metadata": 30, "search": 7, **dict(ttl)}, path=path)


def load(root: Path) -> ProviderPolicy:
    """The provider policy for the project at `root`. Absent file → everything off."""
    path = Path(root) / CONFIG
    if not path.exists():
        reason = (f"discovery providers are off: {CONFIG} not found "
                  f"(copy config/integrations.example.yml to enable them)")
        return ProviderPolicy(off={name: reason for name in CATALOGUE})
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyError(f"{CONFIG} is not readable ({type(exc).__name__})") from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise PolicyError(f"{CONFIG} is not valid YAML{where}") from None
    return _validate(data, str(path))


def _count(value) -> int:
    return len(value) if isinstance(value, list) else 1


class Gateway:
    """Every provider request of one run: policy -> leak check -> cache -> limiter
    -> transport -> ledger. Construct one per run (or per command)."""

    def __init__(self, ws: Path | None, policy: ProviderPolicy, *, cache=None,
                 ledger_dir: Path | None = None, session=None, sleep=time.sleep,
                 clock: Callable[[], float] = time.monotonic, retries: int = 3):
        from scieflow.integrations.scite import policy as scite_policy
        from scieflow.integrations.scite.cache import Cache
        from scieflow.integrations.scite.ledger import Ledger, default_dir

        if ws is None:
            raise PolicyError("provider requests are made for a run (no run workspace given)")
        self.ws = Path(ws)
        self.policy = policy
        self.cache = cache if cache is not None else Cache(default_dir() / "cache",
                                                           policy.cache_ttl_days)
        # The scite ledger, reused: provider rows never touch the scite MCP budgets.
        self.ledger = Ledger(self.ws, scite_policy.Policy.disabled("provider rows only"),
                             day_dir=ledger_dir)
        self.session, self.sleep, self.clock, self.retries = session, sleep, clock, retries
        self._limiters: dict[str, http.RateLimiter] = {}
        self._received: dict[str, set] = {}

    def _limiter(self, provider: str) -> http.RateLimiter:
        if provider not in self._limiters:
            self._limiters[provider] = http.RateLimiter(self.policy.rate(provider),
                                                        clock=self.clock, sleep=self.sleep)
        return self._limiters[provider]

    def check(self, provider: str, url: str) -> None:
        """Raise `ProviderRefused` unless `provider` may request `url` now."""
        if not self.policy.allows(provider):
            raise ProviderRefused(self.policy.reason(provider) or f"{provider} is off")
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise ProviderRefused(f"{provider}: only https requests are allowed")
        host = (parts.hostname or "").lower()
        if parts.username or parts.password or parts.port not in (None, 443) \
                or host not in self.policy.hosts(provider):
            raise ProviderRefused(f"{provider}: host {_show(host) if host else '<none>'} is not "
                                  f"in its allowed hosts ({', '.join(self.policy.hosts(provider)) or 'none'}"
                                  f"; config/integrations.yml)")

    # --- ids the provider itself returned (safe to send back) -----------------------

    def remember(self, provider: str, values) -> None:
        self._received.setdefault(provider, set()).update(v for v in values if isinstance(v, str))

    def was_received(self, provider: str, value) -> bool:
        return value in self._received.get(provider, ())

    # --- the one request path --------------------------------------------------------

    def get_json(self, provider: str, op: str, url: str, *, text: Mapping | None = None,
                 ids: Mapping | None = None, fixed: Mapping | None = None,
                 ttl_class: str = "search", count: Callable[[object], int] = _count,
                 validate: Callable[[object], None] | None = None):
        """GET `url` with every parameter classified — deny by default:

        - `text`: free text; each value must pass the leak check;
        - `ids`: identifiers the caller has already shape-checked (entity ids,
          page tokens);
        - `fixed`: the caller's own constants (str / int / float scalars).

        `validate(value)` raises `HTTPFailure` (MALFORMED) for a body of the wrong
        shape; such a response is ledgered as an error and never cached."""
        from scieflow.core.review.claims import check_text
        from scieflow.integrations.scite.cache import normalise_params, request_key

        self.check(provider, url)
        params: dict = {}
        for k, v in (text or {}).items():
            if v is not None:
                params[k] = check_text(v)
        for k, v in (ids or {}).items():
            if not isinstance(v, str) or not v:
                raise ValueError(f"{provider} {op}: id parameter {k!r} must be a non-empty string")
            params[k] = v
        for k, v in (fixed or {}).items():
            if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                raise ValueError(f"{provider} {op}: fixed parameter {k!r} must be a scalar")
            params[k] = v
        req = normalise_params(params)
        key = request_key(provider, op, {"url": url, **req})
        hit = self.cache.get(key, ttl_class)
        if isinstance(hit, dict) and "value" in hit:
            raw = hit.get("results")
            n = raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else 0
            self.ledger.record(source=provider, op=op, round=None, results=n, cached=True,
                               key=key, request=req)
            return hit["value"]
        try:
            # never follow a redirect: the next host would be one nobody checked
            resp = http.send("GET", url, params=req, retries=self.retries,
                             limiter=self._limiter(provider), sleep=self.sleep,
                             session=self.session, passthrough=(), allow_redirects=False)
            try:
                value = resp.json()
            except ValueError:
                raise http.HTTPFailure(f"{provider} {op} response is not JSON", "MALFORMED",
                                       resp.status_code) from None
            if validate is not None:
                validate(value)
        except http.HTTPFailure as exc:
            self._record(provider, op, 0, key, req, exc.classification)
            raise
        n = count(value)
        self._record(provider, op, n, key, req, None)
        try:
            self.cache.put(key, ttl_class, {"value": value, "results": n})
        except (TypeError, ValueError, OSError):
            pass
        return value

    def _record(self, provider, op, results, key, req, error) -> None:
        status = "ok" if error is None else "error"
        self.ledger.record(source=provider, op=op, round=None, results=results, status=status,
                           error=error, key=key, request=req)
        data = {"integration": provider, "source": provider, "op": op, "results": results,
                "status": status}
        if error is not None:
            data["error"] = error
        events.emit(self.ws, "integration.call", **data)
