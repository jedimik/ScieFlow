"""Deny-by-default access to the free discovery providers (gap discovery, M3), and
the literature search sources that share their gateway (M-A T1, A-1/A-8).

PubTator3, ClinicalTrials.gov and OpenAlex need no key, but every request
still leaves the machine, so it takes the same path as a scite request:

    policy (config/integrations.yml) -> leak check -> cache -> rate limit
    -> transport -> ledger + `integration.call`

- **Policy.** `config/integrations.yml` (user-owned, gitignored;
  `config/integrations.example.yml` is the committed template). No file →
  the discovery providers are off and the four literature search sources
  (`search-*`) are on. A discovery provider must be listed with
  `enabled: true`; a search source is on unless listed `enabled: false`
  (M-A-D1). Its `hosts` may only *narrow* the built-in catalogue
  (`CATALOGUE`): a host outside it is a load error, and a request to any host
  not in the provider's list — or not https — is refused before it is built.
  Unknown keys are refused, never guessed. Refusals are `ProviderRefused`, a
  scite `PolicyError` (CLI exit 3): a boundary, never retried around.
- **Leaks.** Every query parameter is classified. Free text (`text=`, the
  discovery providers) passes the review sanitiser's residual-leak check
  (`core.review.claims.check_text`). Search queries (`checked=`) come from
  `research.search.registry.check_query`, which applies the search variant of
  that check (M-A-D2) and yields a `Checked` value; search syntax built by code
  (`all:`, a publication-year clause) yields a `Composed` value, only after the
  check. A plain `str` is refused in both categories. Ids (PubTator entity ids,
  page tokens) are checked by the provider module; everything else (`fixed=`)
  is a code constant.
- **No redirects.** A 3xx is a failure (`HTTP_3xx`), never followed to a host
  nobody checked.
- **Cache and ledger** are the scite ones, reused: entries are keyed by the
  provider name, op and normalised request; rows land in the run's
  `scite/ledger.jsonl` (when the request belongs to a run) and the per-day
  ledger with the provider as `source`. Provider rows never count against the
  scite MCP budgets. Failures — including a 200 with a body of the wrong shape
  — are ledgered, never cached, and re-raised classified (`HTTPFailure`).

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
# default request rate (requests per second). `default_enabled` marks the literature
# search sources: they are on unless config/integrations.yml switches them off.
CATALOGUE: dict[str, dict] = {
    "pubtator": {"label": "PubTator3", "hosts": ("www.ncbi.nlm.nih.gov",), "rate_per_s": 3},
    "ctgov": {"label": "ClinicalTrials.gov", "hosts": ("clinicaltrials.gov",), "rate_per_s": 1},
    "openalex": {"label": "OpenAlex", "hosts": ("api.openalex.org",), "rate_per_s": 5},
    "europepmc": {"label": "Europe PMC", "hosts": ("www.ebi.ac.uk",), "rate_per_s": 5},
    "search-openalex": {"label": "OpenAlex search", "hosts": ("api.openalex.org",),
                        "rate_per_s": 5, "default_enabled": True},
    # arXiv asks for one request every three seconds; 0.33/s keeps every gap above 3 s.
    "search-arxiv": {"label": "arXiv search", "hosts": ("export.arxiv.org",),
                     "rate_per_s": 0.33, "default_enabled": True},
    "search-europepmc": {"label": "Europe PMC search", "hosts": ("www.ebi.ac.uk",),
                         "rate_per_s": 5, "default_enabled": True},
    "search-crossref": {"label": "Crossref search", "hosts": ("api.crossref.org",),
                        "rate_per_s": 5, "default_enabled": True},
}
_TOP_KEYS = frozenset({"providers", "cache_ttl_days"})
_PROVIDER_KEYS = frozenset({"enabled", "hosts", "rate_per_s"})


def _default_on(name: str) -> bool:
    return bool(CATALOGUE[name].get("default_enabled", False))


def _default_entry(name: str) -> dict:
    cat = CATALOGUE[name]
    return {"hosts": tuple(cat["hosts"]), "rate_per_s": cat["rate_per_s"]}


# --- vetted search text ------------------------------------------------------------------
#
# Only the search registry (research/search/registry.py) may create these: it holds the
# module-private token, so text that never passed the leak check cannot be passed as one.

_VETTED = object()


class _Vetted(str):
    __slots__ = ()

    def __new__(cls, text, _token=None):
        if _token is not _VETTED:
            raise TypeError(f"{cls.__name__} is made only by scieflow.research.search.registry")
        return super().__new__(cls, text)


class Checked(_Vetted):
    """A search query that passed the search leak check (`registry.check_query`)."""

    __slots__ = ()


class Composed(_Vetted):
    """Search syntax built by code from a `Checked` query (`registry.arxiv_query`, ...)."""

    __slots__ = ()


def _mint(kind: type, text: str):
    return kind(text, _VETTED)


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
    enabled = spec.get("enabled", _default_on(name))
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
            if _default_on(name):
                enabled[name] = _default_entry(name)
            else:
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
    """The provider policy for the project at `root`. Absent file → discovery off,
    literature search on (M-A-D1)."""
    path = Path(root) / CONFIG
    if not path.exists():
        reason = (f"discovery providers are off: {CONFIG} not found "
                  f"(copy config/integrations.example.yml to enable them)")
        return ProviderPolicy(
            providers={n: _default_entry(n) for n in CATALOGUE if _default_on(n)},
            off={n: reason for n in CATALOGUE if not _default_on(n)})
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
    """Every provider request of one run — or of one unscoped literature search:
    policy -> leak check -> cache -> limiter -> transport -> ledger. Construct one per
    run (or per command)."""

    def __init__(self, ws: Path | None, policy: ProviderPolicy, *, cache=None,
                 ledger_dir: Path | None = None, session=None, sleep=time.sleep,
                 clock: Callable[[], float] = time.monotonic, retries: int = 3):
        if ws is None:
            raise PolicyError("provider requests are made for a run (no run workspace given); "
                              "an unscoped literature search uses Gateway.unscoped")
        self._setup(Path(ws), policy, cache=cache, ledger_dir=ledger_dir, session=session,
                    sleep=sleep, clock=clock, retries=retries)

    @classmethod
    def unscoped(cls, policy: ProviderPolicy, *, cache=None, ledger_dir: Path | None = None,
                 session=None, sleep=time.sleep, clock: Callable[[], float] = time.monotonic,
                 retries: int = 3) -> "Gateway":
        """A gateway for a search that belongs to no run: rows go to the per-day ledger
        only; no run ledger and no run events."""
        gw = cls.__new__(cls)
        gw._setup(None, policy, cache=cache, ledger_dir=ledger_dir, session=session,
                  sleep=sleep, clock=clock, retries=retries)
        return gw

    def _setup(self, ws: Path | None, policy: ProviderPolicy, *, cache, ledger_dir, session,
               sleep, clock, retries) -> None:
        from scieflow.integrations.scite import policy as scite_policy
        from scieflow.integrations.scite.cache import Cache
        from scieflow.integrations.scite.ledger import Ledger, default_dir

        self.ws = ws
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

    def _params(self, provider: str, op: str, *, text, ids, fixed, checked, composed) -> dict:
        from scieflow.core.review.claims import check_text

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
        for k, v in (checked or {}).items():
            if not isinstance(v, Checked):
                raise ValueError(f"{provider} {op}: checked parameter {k!r} must come from "
                                 f"the search leak check (registry.check_query)")
            params[k] = str(v)
        for k, v in (composed or {}).items():
            if not isinstance(v, Composed):
                raise ValueError(f"{provider} {op}: composed parameter {k!r} must come from "
                                 f"the search composition (registry.arxiv_query / europepmc_query)")
            params[k] = str(v)
        return params

    def get_json(self, provider: str, op: str, url: str, *, text: Mapping | None = None,
                 ids: Mapping | None = None, fixed: Mapping | None = None,
                 checked: Mapping | None = None, composed: Mapping | None = None,
                 ttl_class: str = "search", count: Callable[[object], int] = _count,
                 validate: Callable[[object], None] | None = None):
        """GET `url` with every parameter classified — deny by default:

        - `text`: free text; each value must pass the leak check;
        - `checked` / `composed`: search text from the search registry only;
        - `ids`: identifiers the caller has already shape-checked (entity ids,
          page tokens);
        - `fixed`: the caller's own constants (str / int / float scalars).

        `validate(value)` raises `HTTPFailure` (MALFORMED) for a body of the wrong
        shape; such a response is ledgered as an error and never cached."""
        self.check(provider, url)
        params = self._params(provider, op, text=text, ids=ids, fixed=fixed,
                              checked=checked, composed=composed)
        return self._fetch(provider, op, url, params, ttl_class=ttl_class, count=count,
                           validate=validate, as_text=False)

    def get_text(self, provider: str, op: str, url: str, *, count: Callable[[object], int],
                 text: Mapping | None = None, ids: Mapping | None = None,
                 fixed: Mapping | None = None, checked: Mapping | None = None,
                 composed: Mapping | None = None, ttl_class: str = "search",
                 validate: Callable[[object], None] | None = None) -> str:
        """Like `get_json` for a non-JSON body (arXiv answers Atom XML): the same
        policy, classification, cache, ledger and failure handling; returns the text."""
        self.check(provider, url)
        params = self._params(provider, op, text=text, ids=ids, fixed=fixed,
                              checked=checked, composed=composed)
        return self._fetch(provider, op, url, params, ttl_class=ttl_class, count=count,
                           validate=validate, as_text=True)

    def _fetch(self, provider: str, op: str, url: str, params: dict, *, ttl_class: str,
               count, validate, as_text: bool):
        from scieflow.integrations.scite.cache import normalise_params, request_key

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
            if as_text:
                value = resp.text
            else:
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
        if self.ws is None:
            return
        data = {"integration": provider, "source": provider, "op": op, "results": results,
                "status": status}
        if error is not None:
            data["error"] = error
        events.emit(self.ws, "integration.call", **data)
