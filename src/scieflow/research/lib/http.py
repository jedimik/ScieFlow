"""HTTP GET with retries/backoff for scholarly APIs. Be polite: identify ourselves."""

import time

import requests

from scieflow.core import legacy


def _user_agent() -> str:
    ua = "ScieFlow/0.2 (https://github.com/jedimik/ScieFlow)"
    mailto = legacy.env("SCIEFLOW_MAILTO")  # RESEARCHX_MAILTO still honoured
    return f"{ua} mailto:{mailto}" if mailto else ua


def get(url, params=None, headers=None, retries=3, backoff=1.0):
    hdrs = {"User-Agent": _user_agent(), **(headers or {})}
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as exc:
            # Retry only on 429 and 5xx; fail fast on other 4xx
            status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status_code == 429 or (status_code and status_code >= 500):
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(backoff * (2**attempt))
            else:
                raise
        except requests.RequestException as exc:
            # Retry on connection/timeout errors
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
    raise last_exc


# --- classified requests (scite public tier, Crossref, Europe PMC) ---------------------
#
# `send()` is the stricter sibling of `get()` used by the evidence sources: any
# method, classified failures instead of bare exceptions, `Retry-After` honoured
# on 429, bounded jittered retries on 5xx / connection errors, an optional
# per-second `RateLimiter`, and statuses the caller wants to see (404 → "not
# found") returned instead of raised. Error messages never carry the URL query.
# A 429 with Retry-After waits exactly that long (refused outright above
# `max_retry_after`); a 429 without it gets the same bounded jittered backoff as
# a 5xx, as scite's and Crossref's rate-limit docs prescribe. Either way the
# attempts are bounded by `retries` and the final failure is RATE.

_RETRYABLE_5XX = 500


class HTTPFailure(requests.RequestException):
    """A request that failed for good. `classification` is one of RATE, TRANSIENT,
    AUTH, MALFORMED or HTTP_<status>; callers ledger it and never retry around it."""

    def __init__(self, message: str, classification: str, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.classification = classification
        self.status = status
        self.retry_after = retry_after


def retry_after(value, now=None) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date)."""
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


class RateLimiter:
    """At most `rate_per_s` requests per second: successive `wait()` calls are
    spaced at least 1/rate apart (measured on a monotonic clock)."""

    def __init__(self, rate_per_s: float, clock=time.monotonic, sleep=time.sleep):
        if not isinstance(rate_per_s, (int, float)) or isinstance(rate_per_s, bool) \
                or rate_per_s <= 0:
            raise ValueError("rate_per_s must be a number > 0")
        self.interval = 1.0 / float(rate_per_s)
        self.clock = clock
        self.sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self._last is not None:
            delay = self._last + self.interval - now
            if delay > 0:
                self.sleep(delay)
                now = self.clock()
        self._last = now


def _classify(status: int) -> str:
    if status == 429:
        return "RATE"
    if status >= _RETRYABLE_5XX:
        return "TRANSIENT"
    if status in (401, 403):
        return "AUTH"
    return f"HTTP_{status}"


def send(method: str, url: str, *, params=None, json=None, headers=None, retries: int = 3,
         backoff: float = 1.0, max_retry_after: float = 60.0, limiter: RateLimiter | None = None,
         sleep=time.sleep, passthrough=(404,), timeout: float = 30, jitter=None,
         session=None, allow_redirects: bool = True) -> requests.Response:
    """One logical request with at most `retries` attempts. Returns a 2xx response
    or one whose status is in `passthrough`; raises `HTTPFailure` otherwise.
    `allow_redirects=False` turns a 3xx into `HTTPFailure` (`HTTP_3xx`) instead of
    following it to a host the caller never checked."""
    import random

    jitter = jitter or random.random
    hdrs = {"User-Agent": _user_agent(), **(headers or {})}
    http = session or requests
    attempts = max(1, int(retries))
    failure: HTTPFailure | None = None
    for attempt in range(attempts):
        if limiter is not None:
            limiter.wait()
        try:
            extra = {} if allow_redirects else {"allow_redirects": False}
            resp = http.request(method, url, params=params, json=json, headers=hdrs,
                                timeout=timeout, **extra)
        except requests.RequestException as exc:
            failure = HTTPFailure(f"{method} request failed ({type(exc).__name__})", "TRANSIENT")
            delay = backoff * (2 ** attempt) + jitter() * backoff
        else:
            if 200 <= resp.status_code < 300 or resp.status_code in passthrough:
                return resp
            cls = _classify(resp.status_code)
            wait = retry_after(resp.headers.get("Retry-After")) if cls == "RATE" else None
            failure = HTTPFailure(f"{method} failed with HTTP {resp.status_code}", cls,
                                  resp.status_code, wait)
            if cls not in ("RATE", "TRANSIENT"):
                raise failure
            if wait is not None:
                if wait > max_retry_after:
                    raise failure     # told to come back much later: a boundary, not a nap
                delay = wait
            else:
                delay = backoff * (2 ** attempt) + jitter() * backoff
        if attempt < attempts - 1:
            sleep(delay)
    assert failure is not None
    raise failure


def cached(cache, ttl_class: str, key_parts: dict, fetch):
    """Return `fetch()` through an optional cache (anything with get/put keyed by
    a sha256 hex string, e.g. integrations.scite.cache.Cache). The value is
    wrapped so a cached `None` (a definite "not found") is still a hit."""
    if cache is None:
        return fetch()
    import hashlib
    import json as _json

    key = hashlib.sha256(_json.dumps(key_parts, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()
    hit = cache.get(key, ttl_class)
    if isinstance(hit, dict) and "value" in hit:
        return hit["value"]
    value = fetch()
    try:
        cache.put(key, ttl_class, {"value": value})
    except (TypeError, ValueError, OSError):
        pass
    return value
