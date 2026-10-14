"""On-disk cache of scite (and other literature-source) responses.

Keyed by a sha256 of the *normalised* request, so trivially different
spellings of one request share an entry: DOIs lower-cased and stripped of
`doi:` / `https://doi.org/` prefixes, DOI lists de-duplicated and sorted,
search terms whitespace-collapsed. Entries expire by class (`metadata`,
`search`) after the policy's `cache_ttl_days` — the stricter of the class an
entry was stored with and the class it is read as. Corrupt entries are misses.

The cache is machine-wide (default `~/.cache/scieflow/scite/cache/`), so a
second run asking the same question pays nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scieflow.core import store

_DOI_PREFIX = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", re.IGNORECASE)
_KEY = re.compile(r"^[0-9a-f]{64}\Z")
_DOI_KEYS = frozenset({"doi"})
_DOI_LIST_KEYS = frozenset({"dois", "seed_dois", "seeds"})
_TERM_KEYS = frozenset({"term", "query", "q"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalise_doi(doi: str) -> str:
    return _DOI_PREFIX.sub("", str(doi).strip()).strip().lower()


def normalise_term(term: str) -> str:
    return " ".join(str(term).split())


def normalise_params(params: Mapping) -> dict:
    out = {}
    for key, value in params.items():
        if key in _DOI_KEYS and isinstance(value, str):
            value = normalise_doi(value)
        elif key in _DOI_LIST_KEYS and isinstance(value, (list, tuple)):
            value = sorted({normalise_doi(v) for v in value})
        elif key in _TERM_KEYS and isinstance(value, str):
            value = normalise_term(value)
        out[key] = value
    return out


def request_key(source: str, op: str, params: Mapping) -> str:
    canonical = json.dumps({"source": source, "op": op, "params": normalise_params(params)},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, root: Path, ttl_days: Mapping[str, int],
                 now: Callable[[], datetime] = _utcnow):
        self.root = Path(root)
        self.ttl_days = dict(ttl_days)
        self.now = now

    def _ttl(self, ttl_class: str) -> timedelta:
        if ttl_class not in self.ttl_days:
            raise ValueError(f"unknown cache class {ttl_class!r} "
                             f"(one of {', '.join(sorted(self.ttl_days))})")
        return timedelta(days=self.ttl_days[ttl_class])

    def path(self, key: str) -> Path:
        if not isinstance(key, str) or not _KEY.match(key):
            raise ValueError("cache key must be a sha256 hex digest")
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str, ttl_class: str):
        ttl = self._ttl(ttl_class)
        path = self.path(key)
        try:
            entry = json.loads(path.read_text())
            stored = datetime.fromisoformat(entry["stored"])
            value = entry["value"]
            # the stricter of the stored and the requested class wins
            ttl = min(ttl, self._ttl(entry["class"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if self.now() - stored >= ttl:
            return None
        return value

    def put(self, key: str, ttl_class: str, value) -> None:
        self._ttl(ttl_class)
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(path, json.dumps(
            {"stored": self.now().isoformat(), "class": ttl_class, "value": value},
            ensure_ascii=False, sort_keys=True))
