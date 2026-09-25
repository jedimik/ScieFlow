"""One run, driven entirely through the browser.

Each task's own tests prove its route works. This proves the app as a whole
is sufficient: a person who never opens a terminal can answer the gate that
is blocking a run, mark the phase it was blocking, record what it cost and
stop the run for the night — and see each of those on the timeline.
"""

from scieflow.core import events
from scieflow.web import auth


def post(client, path, **form):
    return client.post(path, follow_redirects=False,
                       data={auth.CSRF_FIELD: client.cookies[auth.CSRF_COOKIE], **form})


def test_a_whole_run_driven_from_the_browser(client, project):
    assert "r1" in client.get("/").text

    gate = client.get("/api/v1/gates").json()[0]
    assert post(client, f"/runs/r1/gates/{gate['id']}", answer="A").status_code == 303

    post(client, "/runs/r1/act", action="phase", phase="hypothesize", state="done")
    post(client, "/runs/r1/act", action="spend", experiment_runs="2")
    post(client, "/runs/r1/act", action="checkpoint", reason="user", detail="for the night")

    page = client.get("/runs/r1").text
    assert "stopped: user" in page
    assert "for the night" in page

    kinds = [e["type"] for e in events.read(project.run_dir("r1"))]
    for expected in ("gate.answered", "phase.done", "budget.recorded", "checkpoint"):
        assert expected in kinds, f"{expected} missing from {kinds}"
