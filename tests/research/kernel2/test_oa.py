"""M-C2 t7 (plan 5.7; Part B t7 item 1): `scieflow research oa`, the Unpaywall lookup.

Every transport is `responses` answering from the recorded bodies in tests/fixtures/kernel2/unpaywall
(researcher's recorded answers, request parameters without the e-mail). A request the test did not
register fails loudly. The contact address below is a reserved example address set up in the test,
never written to a fixture. No network, no patient data.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import responses
import yaml

from scieflow.research.lib import oa

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "kernel2" / "unpaywall"
FULL = "10.7717/peerj.4375"
ABSTRACT = "10.1109/5.726791"
MISSING = "10.9999/nonexistent.12345"
CONTACT = "oa-tester@example.org"
UNPAYWALL_RE = re.compile(r"https://api\.unpaywall\.org/.*")
BODIES = {
    FULL: json.loads((FIXTURES / "full-text.json").read_text(encoding="utf-8")),
    ABSTRACT: json.loads((FIXTURES / "abstract-only.json").read_text(encoding="utf-8")),
}
JSON_HEADERS = {"Content-Type": "application/json"}


def unpaywall_answer(request):
    doi = unquote(urlsplit(request.url).path[len("/v2/"):])
    body = BODIES.get(doi)
    if body is None:
        return 404, JSON_HEADERS, json.dumps({"error": True, "message": "doi not found"})
    return 200, JSON_HEADERS, json.dumps(body)


def enable(root, *, contact=CONTACT):
    entry = {"enabled": True}
    if contact is not None:
        entry["contact_email"] = contact
    (root / "config" / "integrations.yml").write_text(
        yaml.safe_dump({"providers": {"unpaywall": entry}}), encoding="utf-8")


def run(cli_run, argv):
    code, out, err = cli_run(oa.main, argv)
    return code, out, err


def verdicts(out):
    return {row["doi"]: row["verdict"] for row in json.loads(out)["results"]}


# --- 1. verdicts on recorded answers (GAP row 6) -------------------------------------------------


@responses.activate
def test_verdicts_on_recorded_answers(cli_project, cli_run, fast_send):
    root, _ws = cli_project
    enable(root)
    responses.add_callback(responses.GET, UNPAYWALL_RE, callback=unpaywall_answer)
    code, out, _err = run(cli_run, [FULL, ABSTRACT, MISSING, "--workspace", "demo"])
    assert code == 0
    assert verdicts(out) == {FULL: "full-text", ABSTRACT: "abstract-only", MISSING: "unavailable"}
    full = json.loads(out)["results"][0]
    assert full["oa_url"] == "https://doi.org/10.7717/peerj.4375"
    assert full["host_type"] == "publisher" and full["license"] == "cc-by"


@responses.activate
def test_only_unpaywall_is_contacted_and_the_contact_is_on_the_wire(cli_project, cli_run, fast_send):
    root, _ws = cli_project
    enable(root)
    responses.add_callback(responses.GET, UNPAYWALL_RE, callback=unpaywall_answer)
    run(cli_run, [FULL, ABSTRACT])
    hosts = {urlsplit(c.request.url).netloc for c in responses.calls}
    assert hosts == {"api.unpaywall.org"}, "the oa_url of a verdict is never requested"
    from urllib.parse import parse_qs
    assert all(parse_qs(urlsplit(c.request.url).query)["email"] == [CONTACT] for c in responses.calls)


# --- 2. refusals before any request (plan 5.2, 5.9) --------------------------------------------


@responses.activate(assert_all_requests_are_fired=False)
def test_unpaywall_off_exits_3_with_the_policy_reason(cli_project, cli_run, fast_send):
    responses.add_callback(responses.GET, UNPAYWALL_RE, callback=unpaywall_answer)
    code, out, err = run(cli_run, [FULL])
    assert code == 3
    assert "refused" in err and "integrations.yml" in err, "the policy reason is named, no address"
    assert out == "" and len(responses.calls) == 0


@responses.activate(assert_all_requests_are_fired=False)
def test_enabled_without_contact_email_exits_3(cli_project, cli_run, fast_send):
    root, _ws = cli_project
    enable(root, contact=None)
    responses.add_callback(responses.GET, UNPAYWALL_RE, callback=unpaywall_answer)
    code, out, err = run(cli_run, [FULL])
    assert code == 3
    assert "contact_email" in err
    assert len(responses.calls) == 0


@pytest.mark.parametrize("argv", [["not-a-doi"], [f"10.1000/{i}" for i in range(51)]])
def test_bad_or_too_many_dois_exit_1_before_any_request(cli_project, cli_run, argv):
    root, _ws = cli_project
    enable(root)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.GET, UNPAYWALL_RE, callback=unpaywall_answer)
        code, _out, _err = run(cli_run, argv)
        assert code == 1
        assert len(rsps.calls) == 0


# --- 3. a source failure is never a negative (plan 5.9, researcher's rule) -----------------------


@responses.activate
def test_a_server_error_is_a_failure_not_unavailable(cli_project, cli_run, fast_send):
    root, _ws = cli_project
    enable(root)
    responses.add(responses.GET, UNPAYWALL_RE, status=500, body="unavailable")
    code, out, _err = run(cli_run, [FULL])
    assert code == 1
    assert out == ""


# --- 4. the address leaves no file or output (plan 5.9) ----------------------------------------


@responses.activate
def test_contact_address_is_written_nowhere(cli_project, cli_run, fast_send, tmp_path):
    root, _ws = cli_project
    enable(root)
    responses.add_callback(responses.GET, UNPAYWALL_RE, callback=unpaywall_answer)
    code, out, _err = run(cli_run, [FULL, ABSTRACT, MISSING, "--workspace", "demo"])
    assert code == 0
    assert CONTACT not in out
    for path in tmp_path.rglob("*"):
        # the user's own config/integrations.yml holds the address by design; nothing else may
        if path.is_file() and path != tmp_path / "proj" / "config" / "integrations.yml":
            assert CONTACT.encode() not in path.read_bytes(), path.name


# --- 5. replay: recorded answers, no transport; a hole exits 6 (plan 5.9) ----------------------


@responses.activate
def test_record_then_replay_offline_gives_the_same_verdicts(cli_project, cli_run, fast_send):
    root, _ws = cli_project
    enable(root)
    responses.add_callback(responses.GET, UNPAYWALL_RE, callback=unpaywall_answer)
    code, recorded, _err = run(cli_run, [FULL, ABSTRACT, MISSING, "--workspace", "demo",
                                         "--network", "record"])
    assert code == 0
    sent = len(responses.calls)
    code, replayed, _err = run(cli_run, [FULL, ABSTRACT, MISSING, "--workspace", "demo",
                                         "--network", "replay"])
    assert code == 0
    assert verdicts(replayed) == verdicts(recorded)
    assert len(responses.calls) == sent, "replay sends nothing"


@responses.activate
def test_replay_miss_exits_6_and_prints_no_report(cli_project, cli_run, fast_send):
    root, _ws = cli_project
    enable(root)
    code, out, _err = run(cli_run, [FULL, "--workspace", "demo", "--network", "replay"])
    assert code == 6
    assert out == ""
    assert len(responses.calls) == 0
