"""research/lib/http.py: send() with classified errors, Retry-After, bounded
jittered retries, and the per-second RateLimiter; research/lib/doi.py."""

from __future__ import annotations

from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import pytest
import responses

from scieflow.research.lib import doi as doi_mod
from scieflow.research.lib import http

URL = "https://api.example.org/thing"


class FakeClock:
    """Monotonic clock advanced only by the fake sleep."""

    def __init__(self):
        self.t = 100.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


# --- Retry-After --------------------------------------------------------------------


def test_retry_after_seconds_and_http_date():
    now = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
    assert http.retry_after("2", now=now) == 2.0
    assert http.retry_after(" 0 ", now=now) == 0.0
    later = format_datetime(now + timedelta(seconds=7), usegmt=True)
    assert http.retry_after(later, now=now) == 7.0
    past = format_datetime(now - timedelta(seconds=7), usegmt=True)
    assert http.retry_after(past, now=now) == 0.0
    assert http.retry_after(None, now=now) is None
    assert http.retry_after("soon", now=now) is None
    assert http.retry_after("-3", now=now) is None


@responses.activate
def test_429_honours_retry_after_then_succeeds():
    fc = FakeClock()
    responses.get(URL, status=429, headers={"Retry-After": "2"})
    responses.get(URL, json={"ok": True})
    resp = http.send("GET", URL, sleep=fc.sleep)
    assert resp.json() == {"ok": True}
    assert fc.sleeps == [2.0]
    assert len(responses.calls) == 2


@responses.activate
def test_429_retry_after_beyond_cap_is_not_slept():
    fc = FakeClock()
    responses.get(URL, status=429, headers={"Retry-After": "3600"})
    with pytest.raises(http.HTTPFailure) as exc:
        http.send("GET", URL, sleep=fc.sleep, max_retry_after=60)
    assert exc.value.classification == "RATE"
    assert exc.value.retry_after == 3600.0
    assert fc.sleeps == []
    assert len(responses.calls) == 1


@responses.activate
def test_429_exhausted_is_rate():
    fc = FakeClock()
    for _ in range(3):
        responses.get(URL, status=429, headers={"Retry-After": "1"})
    with pytest.raises(http.HTTPFailure) as exc:
        http.send("GET", URL, sleep=fc.sleep, retries=3)
    assert exc.value.classification == "RATE"
    assert exc.value.status == 429
    assert fc.sleeps == [1.0, 1.0]          # no sleep after the last attempt
    assert len(responses.calls) == 3


@responses.activate
def test_5xx_bounded_jittered_retry_then_transient():
    fc = FakeClock()
    for _ in range(3):
        responses.get(URL, status=503)
    with pytest.raises(http.HTTPFailure) as exc:
        http.send("GET", URL, sleep=fc.sleep, retries=3, backoff=1.0, jitter=lambda: 0.5)
    assert exc.value.classification == "TRANSIENT"
    assert fc.sleeps == [1.5, 2.5]          # backoff * 2**attempt + jitter * backoff
    assert len(responses.calls) == 3


@responses.activate
def test_connection_error_is_transient():
    fc = FakeClock()
    with pytest.raises(http.HTTPFailure) as exc:   # nothing registered -> ConnectionError
        http.send("GET", URL, sleep=fc.sleep, retries=2, jitter=lambda: 0.0)
    assert exc.value.classification == "TRANSIENT"
    assert exc.value.status is None
    assert len(fc.sleeps) == 1


@responses.activate
@pytest.mark.parametrize("status,cls", [(400, "HTTP_400"), (401, "AUTH"), (403, "AUTH"),
                                        (422, "HTTP_422")])
def test_other_4xx_fail_fast(status, cls):
    fc = FakeClock()
    responses.get(URL, status=status)
    with pytest.raises(http.HTTPFailure) as exc:
        http.send("GET", URL, sleep=fc.sleep)
    assert exc.value.classification == cls
    assert fc.sleeps == []
    assert len(responses.calls) == 1


@responses.activate
def test_passthrough_404_is_returned():
    responses.get(URL, status=404, json={"detail": "Not Found"})
    resp = http.send("GET", URL, passthrough=(404,))
    assert resp.status_code == 404


@responses.activate
def test_404_without_passthrough_fails_fast():
    responses.get(URL, status=404)
    with pytest.raises(http.HTTPFailure) as exc:
        http.send("GET", URL, passthrough=())
    assert exc.value.classification == "HTTP_404"


@responses.activate
def test_post_json_and_user_agent():
    responses.post(URL, json={"ok": 1})
    http.send("POST", URL, json=["10.1/a"])
    req = responses.calls[0].request
    assert req.body == b'["10.1/a"]'
    assert req.headers["User-Agent"].startswith("ScieFlow/")


@responses.activate
def test_failure_message_carries_no_url_query():
    responses.get(URL, status=500)
    with pytest.raises(http.HTTPFailure) as exc:
        http.send("GET", URL, params={"query": "secret-term"}, retries=1)
    assert "secret-term" not in str(exc.value)


# --- rate limiter -------------------------------------------------------------------


def test_rate_limiter_spaces_requests():
    fc = FakeClock()
    lim = http.RateLimiter(2, clock=fc.clock, sleep=fc.sleep)
    for _ in range(3):
        lim.wait()
    assert fc.sleeps == [0.5, 0.5]


def test_rate_limiter_no_wait_after_idle():
    fc = FakeClock()
    lim = http.RateLimiter(4, clock=fc.clock, sleep=fc.sleep)
    lim.wait()
    fc.t += 10
    lim.wait()
    assert fc.sleeps == []


def test_rate_limiter_rejects_bad_rate():
    with pytest.raises(ValueError):
        http.RateLimiter(0)


@responses.activate
def test_send_uses_limiter_for_every_attempt():
    fc = FakeClock()
    lim = http.RateLimiter(1, clock=fc.clock, sleep=fc.sleep)
    responses.get(URL, status=429, headers={"Retry-After": "0"})
    responses.get(URL, json={})
    http.send("GET", URL, limiter=lim, sleep=fc.sleep)
    # second attempt waits for the limiter (Retry-After 0 is not enough)
    assert fc.sleeps == [0.0, 1.0]


# --- DOIs ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,norm", [
    ("10.1038/NATURE12373", "10.1038/nature12373"),
    ("doi:10.1/X", "10.1/x"),
    ("https://doi.org/10.1016/S0140-6736(97)11096-0", "10.1016/s0140-6736(97)11096-0"),
    ("  https://dx.doi.org/10.1093/brain/awn044 ", "10.1093/brain/awn044"),
    ("https://www.doi.org/10.1000/XYZ", "10.1000/xyz"),
    ("http://www.dx.doi.org/10.1000/xyz", "10.1000/xyz"),
    ("doi.org/10.1000/xyz", "10.1000/xyz"),
    ("www.doi.org/10.1000/xyz", "10.1000/xyz"),
    ("DOI 10.1000/xyz", "10.1000/xyz"),
    ("DOI: 10.1000/xyz", "10.1000/xyz"),
    ("doi:10.1000/xyz", "10.1000/xyz"),
    ("urn:doi:10.1000/xyz", "10.1000/xyz"),
])
def test_doi_normalise(raw, norm):
    assert doi_mod.normalise(raw) == norm


@pytest.mark.parametrize("value,ok", [
    ("10.1038/nature12373", True),
    ("10.1016/s0140-6736(97)11096-0", True),
    ("10.12/x", False),                    # registrant too short
    ("11.1234/x", False),
    ("10.1234/", False),
    ("10.1234/../../etc/passwd", False),   # path-shaped
    ("10.1234/a/..", False),
    ("10.1234/a b", False),
    ("10.1234/a\n", False),
    ("10.1234/a\x00b", False),
    ("10.1234/" + "x" * 400, False),
    ("", False),
    (None, False),
    (42, False),
])
def test_doi_is_valid(value, ok):
    assert doi_mod.is_valid(value) is ok


def test_doi_quote_keeps_slashes_escapes_query_chars():
    assert doi_mod.quote("10.1016/s0140-6736(97)11096-0") == "10.1016/s0140-6736(97)11096-0"
    assert doi_mod.quote("10.1234/a?b#c%d") == "10.1234/a%3Fb%23c%25d"
