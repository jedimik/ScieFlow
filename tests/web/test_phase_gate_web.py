"""M-G2 t2: the phase gate over the service, the HTTP API and the run page (M-G2 5.3, 4.1). Synthetic data only.

The claim check is stubbed to one error (or to none); the refusal reaches the API as 404 JSON and the run page as a
redirect with the message, and the status file is byte-identical after every refusal.
"""

from urllib.parse import unquote

import pytest

from scieflow.core import events
from scieflow.core.service import ServiceError, mark_phase
from scieflow.core.run import status
from scieflow.research.manuscript.claims import compile as claims_compile
from scieflow.web import auth


class OneError:
    errors = [{"severity": "error", "code": "C002"}]
    warnings = []
    status_unreadable = False


class Clean:
    errors = []
    warnings = []
    status_unreadable = False


def post(client, path, **form):
    """POST the way a browser form does: the CSRF token as a field."""
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form})


@pytest.fixture
def paper(project, monkeypatch):
    monkeypatch.chdir(project.root)
    monkeypatch.setattr(status, "_VOCAB", None)
    ws = project.run_dir("pd")
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("pd", "per-campaign", "paper-draft"))
    (ws / "manuscript").mkdir()
    (ws / "manuscript" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    monkeypatch.setattr(claims_compile, "check_run", lambda project, run_folder, main="main.tex": OneError())
    return ws


def test_service_mark_phase_refuses_with_the_claim_message_and_keeps_the_status(project, paper):
    before = (paper / "status.yml").read_bytes()
    with pytest.raises(ServiceError, match=r"verify refused: 1 claim error\(s\)"):
        mark_phase(project, "pd", "verify", "done", actor="agent")
    assert (paper / "status.yml").read_bytes() == before
    assert [e["data"]["outcome"] for e in events.read(paper, types=("note.phase_gate",))] == ["refused"]


def test_the_api_returns_the_refusal_as_json_and_keeps_the_status(client, paper):
    before = (paper / "status.yml").read_bytes()
    response = post(client, "/api/v1/runs/pd/phase", phase="verify", state="done")
    assert response.status_code == 404
    assert "1 claim error(s)" in response.json()["error"]
    assert (paper / "status.yml").read_bytes() == before


def test_the_run_page_action_redirects_with_the_refusal_and_keeps_the_status(client, paper):
    before = (paper / "status.yml").read_bytes()
    response = post(client, "/runs/pd/act", action="phase", phase="verify", state="done")
    assert response.status_code == 303
    assert "verify refused: 1 claim error(s)" in unquote(response.headers["location"])
    assert (paper / "status.yml").read_bytes() == before


def test_a_passing_check_marks_verify_done_over_the_api(client, paper, monkeypatch):
    monkeypatch.setattr(claims_compile, "check_run", lambda project, run_folder, main="main.tex": Clean())
    response = post(client, "/api/v1/runs/pd/phase", phase="verify", state="done")
    assert response.status_code == 200, response.text
    assert status.read_status(paper)["phases"]["verify"] == "done"
