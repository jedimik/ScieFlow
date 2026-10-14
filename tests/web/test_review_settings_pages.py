"""W1 scite toggle and W2 reviewer pool editor, through the browser routes.

`scite_on` / `scite_off` (tests/web/conftest.py) stand in for config/scite.yml so
no scite call is ever made. `review_project` is the fixture run at slug `rv1`.
"""

import re
import sys
from urllib.parse import quote

import pytest
import yaml

from scieflow.core import service
from scieflow.core.review import service as review_service
from scieflow.core.review.settings import SettingsError
from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def review_block(project, slug):
    doc = yaml.safe_load((project.run_dir(slug) / "config.yml").read_text()) or {}
    return doc.get("review") or {}


def config_bytes(project, slug):
    return (project.run_dir(slug) / "config.yml").read_bytes()


APPLY = "/runs/rv1/review/settings"
SCITE_ON = {"scite_present": "1", "scite_use": "on"}


# --- W1: the scite toggle on /start -------------------------------------------------


def test_the_start_toggle_is_live_and_checked_when_scite_is_configured(client, scite_on):
    page = client.get("/start").text
    assert 'name="scite_present"' in page
    assert 'name="scite_use"' in page and "checked" in page


def test_the_start_toggle_is_disabled_with_its_reason_when_scite_is_absent(client, scite_off):
    page = client.get("/start").text
    assert 'name="scite_present"' not in page
    assert "config/scite.yml is absent" in page


def test_a_reason_from_scite_is_escaped_on_the_page(client, monkeypatch):
    status = {"configured": False, "reason": "<script>alert(1)</script>"}
    monkeypatch.setattr(review_service, "scite_status", lambda project: dict(status))
    page = client.get("/start").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_a_start_with_scite_ask_records_ask(client, project, scite_on):
    response = post(client, "/start", slug="s-ask", goal="a goal", **SCITE_ON)
    assert response.status_code == 303
    assert review_block(project, "s-ask")["scite"] == "ask"


def test_a_start_with_scite_approved_records_approved(client, project, scite_on):
    post(client, "/start", slug="s-ok", goal="a goal", scite_present="1",
         scite_use="on", scite_no_ask="on")
    assert review_block(project, "s-ok")["scite"] == "approved"


def test_a_start_with_scite_unchecked_records_off(client, project, scite_on):
    post(client, "/start", slug="s-off", goal="a goal", scite_present="1")
    assert review_block(project, "s-off")["scite"] == "off"


def test_nothing_is_written_when_scite_is_not_configured_even_if_posted(client, project, scite_off):
    response = post(client, "/start", slug="s-none", goal="a goal", **SCITE_ON)
    assert response.status_code == 303
    assert "scite" not in review_block(project, "s-none")


def test_a_refused_start_writes_no_scite(client, project, scite_on):
    response = post(client, "/start", slug="../escape", goal="a goal", **SCITE_ON)
    assert response.status_code == 200
    assert not (project.root.parent / "escape").exists()


def test_a_started_but_failed_run_still_gets_its_scite_and_the_error(client, project,
                                                                      scite_on, monkeypatch):
    def refuse(project_, slug, message, *args, **kwargs):
        raise service.ServiceError("the opening turn was refused")

    monkeypatch.setattr(service, "say", refuse)
    response = post(client, "/start", slug="rs-run", goal="a goal", agent="stub", **SCITE_ON)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/rs-run?error=")
    assert review_block(project, "rs-run")["scite"] == "ask"


def test_a_post_creation_settings_refusal_redirects_to_the_run(client, project, scite_on,
                                                              monkeypatch):
    def refuse(project_, slug, changes, actor):
        raise SettingsError("review.scite: refused by test")

    monkeypatch.setattr(review_service, "set_review_settings", refuse)
    response = post(client, "/start", slug="se-run", goal="a goal", **SCITE_ON)
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/se-run?error=" + quote(
        "review.scite: refused by test")
    assert project.run_dir("se-run").is_dir()


# --- W2: the reviewer pool editor on /agents?slug= ----------------------------------


def test_the_agents_page_shows_the_pool_editor_for_a_run(client, review_project, scite_on):
    page = client.get("/agents?slug=rv1").text
    for field in ('name="pool-0-agent"', 'name="pool-0-covers"', 'name="reviews_per_draft"',
                  'name="allow_self_review"', 'name="pool_reset"'):
        assert field in page, field
    assert "Traceback" not in page


def test_the_pool_editor_prefills_the_runs_own_pool(client, review_project, scite_on):
    page = client.get("/agents?slug=rv1").text
    assert "<option selected>claude-paper</option>" in page
    assert '<option value="all" selected>all</option>' in page


def test_apply_writes_the_pool_switches_and_scite(client, review_project, scite_on):
    response = post(client, APPLY, pool_present="1",
                    **{"pool-0-agent": "claude-paper", "pool-0-covers": "all",
                       "pool-1-agent": "codex-review", "pool-1-covers": "auto"},
                    switches_present="1", allow_same_family_review="on",
                    reviews_per_draft="2", **SCITE_ON)
    assert response.status_code == 303
    block = review_block(review_project, "rv1")
    assert block["pool"] == [{"agent": "claude-paper", "covers": "all"},
                             {"agent": "codex-review", "covers": "auto"}]
    assert block["allow_same_family_review"] is True
    assert block["allow_self_review"] is False
    assert block["reviews_per_draft"] == 2
    assert block["scite"] == "ask"


def test_reset_removes_the_runs_pool(client, review_project, scite_on):
    post(client, APPLY, pool_present="1", **{"pool-0-agent": "claude-paper",
                                            "pool-0-covers": "all"})
    assert review_block(review_project, "rv1")["pool"]
    post(client, APPLY, pool_present="1", pool_reset="1")
    assert "pool" not in review_block(review_project, "rv1")


def test_a_garbage_reviews_per_draft_is_refused_without_calling_core(client, review_project,
                                                                    scite_on, monkeypatch):
    calls = []

    def spy(project, slug, changes, actor):
        calls.append(changes)
        return {}

    monkeypatch.setattr(review_service, "set_review_settings", spy)
    before = config_bytes(review_project, "rv1")
    response = post(client, APPLY, reviews_per_draft="lots")
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert calls == [], "a garbage field must not reach core"
    assert config_bytes(review_project, "rv1") == before


def test_a_core_refusal_on_apply_is_shown_on_the_page_and_writes_nothing(client, review_project,
                                                                         scite_on):
    before = config_bytes(review_project, "rv1")
    response = post(client, APPLY, pool_present="1",
                    **{"pool-0-agent": "claude-paper", "pool-0-covers": "all",
                       "pool-1-agent": "claude-paper", "pool-1-covers": "all"})
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "listed more than once" in page
    assert config_bytes(review_project, "rv1") == before


def test_the_preview_is_a_get_that_writes_nothing(client, review_project, scite_on):
    before = config_bytes(review_project, "rv1")
    page = client.get("/agents", params={
        "slug": "rv1", "preview": "1", "pool_present": "1",
        "pool-0-agent": "claude-paper", "pool-0-covers": "auto",
        "pool-1-agent": "codex-paper", "pool-1-covers": "auto",
        "switches_present": "1", "reviews_per_draft": "1",
        "scite_present": "1", "scite_use": "on"}).text
    assert "Assignment preview (nothing written)" in page
    assert config_bytes(review_project, "rv1") == before


def test_a_preview_error_is_shown_in_place(client, review_project, scite_on):
    page = client.get("/agents", params={
        "slug": "rv1", "preview": "1", "pool_present": "1",
        "pool-0-agent": "claude-paper", "pool-0-covers": "all",
        "pool-1-agent": "claude-paper", "pool-1-covers": "all"}).text
    assert "listed more than once" in page


def test_a_script_agent_name_is_escaped_in_a_preview_error(client, review_project, scite_on):
    page = client.get("/agents", params={
        "slug": "rv1", "preview": "1", "pool_present": "1",
        "pool-0-agent": "<script>alert(1)</script>", "pool-0-covers": "all"}).text
    assert "<script>alert(1)" not in page


def test_the_review_settings_route_refuses_an_unknown_run(client, scite_on):
    assert post(client, "/runs/no-such-run/review/settings", **SCITE_ON).status_code == 404


def test_the_pool_editor_degrades_without_the_research_extra(client, review_project,
                                                            scite_on, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def without_review(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("scieflow.core.review"):
            raise ImportError("No module named 'jsonschema'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", without_review)
    response = client.get("/agents?slug=rv1")
    assert response.status_code == 200
    assert "review needs the research extra" in response.text
