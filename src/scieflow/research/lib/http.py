"""HTTP GET with retries/backoff for scholarly APIs. Be polite: identify ourselves."""

import os
import time

import requests


def _user_agent() -> str:
    ua = "ScieFlow/0.2 (https://github.com/jedimik/ScieFlow)"
    mailto = os.environ.get("SCIEFLOW_MAILTO")
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
