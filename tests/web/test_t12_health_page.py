"""T-12 t6: the run health page (`GET /runs/<slug>/health`), its check form and the JSON twins.

Synthetic temporary projects only (the `project` and `client` fixtures). The cheap page reads
and never scans; the scan runs only on an explicit POST with the session and CSRF guards.
"""

from pathlib import Path

import yaml

from scieflow.core import service
from scieflow.web import auth

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "scieflow" / "web" / "templates"


def post(client, path, **form):
    """POST the way a browser form does: the CSRF token as a field."""
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form})


def _strip_time(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "observed_at"}


def test_page_shows_the_cheap_checks_and_a_check_form(client):
    response = client.get("/runs/r1/health")
    assert response.status_code == 200
    assert 'action="/runs/r1/health/check"' in response.text
    assert 'name="csrf_token"' in response.text
    assert "Check now" in response.text
    assert "not checked yet" in response.text      # the walked checks wait for the button


def test_the_page_never_scans(client, project, monkeypatch):
    from scieflow.core import workspace_health

    def refuse(*args, **kwargs):
        raise AssertionError("a GET must not scan")

    monkeypatch.setattr(workspace_health, "scan", refuse)
    assert client.get("/runs/r1/health").status_code == 200


def test_check_is_a_post_with_csrf_and_renders_the_scan(client):
    assert client.post("/runs/r1/health/check").status_code in (401, 403)
    response = post(client, "/runs/r1/health/check")
    assert response.status_code == 200
    assert "entries observed" in response.text
    assert "stop reason" in response.text
    assert "results may change while a run is active" in response.text


def test_api_get_equals_the_service_and_post_equals_the_scan(client, project):
    body = client.get("/api/v1/runs/r1/health").json()
    assert _strip_time(body) == _strip_time(service.run_health(project, "r1"))
    scanned = post(client, "/api/v1/runs/r1/health/check").json()
    assert scanned["mode"] == "scan"
    assert set(scanned["checks"]) == {"missing", "legacy_config_keys", "per_file_dvc_pointer",
                                      "junk_dirs", "absolute_self_links", "scripts_in_logs"}


def test_missing_run_is_a_404_on_every_route(client):
    assert client.get("/runs/no-such-run/health").status_code == 404
    assert client.get("/api/v1/runs/no-such-run/health").status_code == 404
    assert post(client, "/runs/no-such-run/health/check").status_code == 404
    assert post(client, "/api/v1/runs/no-such-run/health/check").status_code == 404


def test_an_alias_shows_only_what_it_points_to(client, project):
    (project.root / "workspace" / "r1-alias").symlink_to("r1")
    response = client.get("/runs/r1-alias/health")
    assert response.status_code == 200
    assert "alias of r1" in response.text
    assert "Check now" not in response.text


def test_the_page_escapes_what_a_control_says(client, project):
    odd = project.root / "workspace" / "odd"
    odd.mkdir()
    (odd / "status.yml").write_text(yaml.safe_dump(
        {"run": "odd", "iteration": 1, "stopped": {"reason": "<img src=x onerror=1>"},
         "phases": {}}))
    page = client.get("/runs/odd/health").text
    assert "<img src=x" not in page
    assert "&lt;img" in page


def test_workspace_links_each_run_to_its_health(client):
    assert 'href="/runs/r1/health"' in client.get("/workspace").text


def test_new_templates_have_no_dialogs_and_no_safe_filter():
    for name in ("health.html", "workspace.html"):
        text = (TEMPLATES / name).read_text()
        for dialog in ("alert(", "confirm(", "prompt("):
            assert dialog not in text, (name, dialog)
        assert "|safe" not in text, name
