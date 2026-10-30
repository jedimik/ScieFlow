"""M-C2 t4 test support: the offline identity gold transport (no network).

`GoldTransport` answers the `responses` mock only from `tests/fixtures/kernel2/identity/
gold-responses.json.gz` (converted researcher answers). A request that is not in the fixture is
recorded in `unrecorded` and fails the test with AssertionError: the transport never invents an
answer and never reaches a real service. Parameters are compared as strings, so a recorded
integer `5` matches the wire value `5`.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

REPO = Path(__file__).resolve().parents[3]
IDENTITY_DIR = REPO / "tests" / "fixtures" / "kernel2" / "identity"
GOLD_FILE = IDENTITY_DIR / "gold-responses.json.gz"
BASELINE_FILE = IDENTITY_DIR / "identity_baseline_verdicts.json"
GOLD_YAML = IDENTITY_DIR / "identity.yaml"
BIB_FILE = IDENTITY_DIR / "references.bib"

HOST_SOURCE = {
    "api.openalex.org": "openalex",
    "api.crossref.org": "crossref",
    "api.datacite.org": "datacite",
}
IDENTITY_URL = re.compile(r"https://api\.(openalex|crossref|datacite)\.org/.*")


def canon(params) -> str:
    return json.dumps({str(k): str(v) for k, v in dict(params or {}).items()}, sort_keys=True)


def split_request(url: str) -> tuple[str, str, dict]:
    """(source, endpoint, query parameters) of one identity request URL."""
    parts = urlsplit(url)
    source = HOST_SOURCE[parts.netloc]
    endpoint = parts.path.lstrip("/")   # the wire path, as researcher recorded it
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    return source, endpoint, params


def load_gold_answers() -> dict:
    """(source, endpoint, canonical params) -> (status, body) for every recorded request."""
    rows = json.loads(gzip.decompress(GOLD_FILE.read_bytes()).decode("utf-8"))
    return {(r["source"], r["endpoint"], canon(r["params"])): (r["status"], r["body"])
            for r in rows}


class GoldTransport:
    """The responses callback. Answers from the fixture or fails the test."""

    def __init__(self) -> None:
        self.answers = load_gold_answers()
        self.served: list[tuple[str, str, str]] = []
        self.unrecorded: list[tuple[str, str, str]] = []

    def __call__(self, request):
        source, endpoint, params = split_request(request.url)
        key = (source, endpoint, canon(params))
        if key not in self.answers:
            self.unrecorded.append(key)
            raise AssertionError(f"request not in the gold fixture: {source} {endpoint}")
        self.served.append(key)
        status, body = self.answers[key]
        text = "" if body is None else json.dumps(body)
        return status, {"Content-Type": "application/json"}, text


class NoTransport:
    """A callback for tests that must send nothing: any request fails the test."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request):
        self.calls.append(request.url)
        raise AssertionError(f"a request was sent: {request.url}")
