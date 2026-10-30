"""M-C2 t4 (plan 5.4, Part B t4 item 3): the identity gold set, through the Gateway, offline.

The 150 gold claims are built as researcher's eval builds them (researcher_baseline.py in the
M-C2 plan probe). Each goes through the three Gateway-backed connectors over the faked transport
(tests/fixtures/kernel2/identity/gold-responses.json.gz). Acceptance: every verdict equals the
baseline, and refusal-grade false positives are 0/100 and false negatives 0/50. A search whose
title the leak check refuses is not sent; in those cells the outcome must be `source_error`
instead of `negative`, and nothing else may differ.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter

import pytest
import responses
import yaml

from tests.research.kernel2.identity_support import BASELINE_FILE, GOLD_YAML, IDENTITY_URL, GoldTransport
from scieflow._vendor.researcher_core import verify
from scieflow.core.review.claims import LeakError
from scieflow.research.lib.identity import CrossrefIdentity, DataCiteIdentity, OpenAlexIdentity
from scieflow.research.search import registry

REFUSAL = ("mismatch", "unresolvable")
GOLD_ITEMS = yaml.safe_load(GOLD_YAML.read_text(encoding="utf-8"))["items"]
BASELINE = {i["doi"]: i for i in json.loads(BASELINE_FILE.read_text(encoding="utf-8"))["items"]}


def claim_for(item) -> verify.ReferenceClaim:
    """The claim as researcher's eval builds it (researcher_baseline.py)."""
    doi = str(item.get("doi") or "")
    year = item.get("year") if isinstance(item.get("year"), int) else None
    authors = [str(item["first_author"])] if item.get("first_author") else []
    return verify.ReferenceClaim(key=doi, title=str(item.get("title") or ""), doi=doi,
                                 year=year, authors=authors)


@pytest.fixture
def gold(make_gateway, fast_send):
    transport = GoldTransport()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.GET, IDENTITY_URL, callback=transport)
        yield make_gateway(None), transport


def test_the_gold_set_has_150_items_and_both_classes():
    assert len(GOLD_ITEMS) == 150
    counts = Counter(i["expected"] for i in GOLD_ITEMS)
    assert counts == {"verified": 75, "inconclusive": 25, "mismatch": 25, "unresolvable": 25}
    assert sum(counts[k] for k in ("verified", "inconclusive")) == 100   # negatives
    assert sum(counts[k] for k in REFUSAL) == 50                         # positives


def test_identity_gold_verdicts_match_the_baseline(gold, capsys):
    gw, transport = gold
    conns = [OpenAlexIdentity(gw), CrossrefIdentity(gw), DataCiteIdentity(gw)]
    confusion: Counter = Counter()
    refused_cells = 0
    for item in GOLD_ITEMS:
        claim = claim_for(item)
        entry = asyncio.run(verify.verify_claim_async(
            claim, conns, check_status=False, check_accessibility=False))
        base = BASELINE[item["doi"]]
        confusion[(item["expected"], entry.verdict)] += 1
        assert entry.verdict == base["verdict"], item["doi"]
        for got, want_outcome in zip(entry.outcomes, base["outcomes"]):
            if got.outcome == want_outcome:
                continue
            # the only allowed difference: a title the leak check refuses is not sent
            assert want_outcome == "negative" and got.outcome == "source_error", item["doi"]
            with pytest.raises(LeakError):
                registry.check_query(claim.title)
            refused_cells += 1

    assert transport.unrecorded == []
    fp = sum(v for (g, p), v in confusion.items() if g in ("verified", "inconclusive") and p in REFUSAL)
    fn = sum(v for (g, p), v in confusion.items() if g in REFUSAL and p not in REFUSAL)
    print("\nidentity gold confusion (expected -> verdict: count)")
    for (g, p), v in sorted(confusion.items()):
        print(f"  {g} -> {p}: {v}")
    print(f"refusal-grade false positives {fp}/100, false negatives {fn}/50, "
          f"leak-refused outcome cells {refused_cells}")
    assert fp == 0 and fn == 0
    assert refused_cells > 0   # the leak-refused titles really are exercised


def test_a_request_outside_the_fixture_fails_the_test(gold):
    gw, _transport = gold
    with pytest.raises(AssertionError):
        asyncio.run(OpenAlexIdentity(gw).resolve_doi("10.9999/not-recorded-anywhere"))


def test_every_gold_item_asks_at_least_one_source(gold):
    gw, transport = gold
    conns = [OpenAlexIdentity(gw), CrossrefIdentity(gw), DataCiteIdentity(gw)]
    for item in GOLD_ITEMS:
        asyncio.run(verify.verify_claim_async(claim_for(item), conns,
                                              check_status=False, check_accessibility=False))
    asked = {(s, e) for (s, e, _p) in transport.served}
    assert len(transport.served) >= 450
    assert any(s == "datacite" for s, _ in asked) and any(s == "crossref" for s, _ in asked)
