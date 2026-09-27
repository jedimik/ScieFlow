"""Compiling a draft from the workbench."""

import shutil
import sys

import pytest

from scieflow.web import auth


def post(client, path, follow_redirects=False, **form):
    """POST the way a browser form does: the CSRF token as a field — the
    same helper every other page's test module uses (test_drafts_page,
    test_mutations, test_run_page_actions, ...). The `client` fixture only
    exchanges the loopback token for cookies; it never stamps a CSRF field
    onto a POST for you, so a bare `client.post(...)` against a mutating
    route always gets refused with 403."""
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=follow_redirects)


@pytest.fixture
def drafted(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / "claude"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("Yield was 95\\%.\n")
    return ws


def test_each_draft_offers_a_compile_button(client, drafted):
    page = client.get("/runs/r1/drafts")
    assert "/runs/r1/preview" in page.text
    assert 'value="agent:claude"' in page.text
    # Idle: the button is enabled -- no `disabled` attribute on it. Paired
    # with test_compile_buttons_disabled_while_a_preview_is_running below,
    # which checks the opposite state, so neither test can pass merely
    # because the word "disabled" appears somewhere else on the page.
    assert '<button type="submit">Compile PDF</button>' in page.text


def test_a_completed_round_is_previewable_too(client, drafted):
    d = drafted / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")
    page = client.get("/runs/r1/drafts")
    assert 'value="round:1"' in page.text


def test_a_preview_source_that_is_not_a_draft_is_refused(client, drafted):
    response = post(client, "/runs/r1/preview", source="agent:../../etc",
                    follow_redirects=True)
    assert response.status_code == 200
    assert "500" not in response.text
    assert not (drafted.parent / "etc").exists()


def test_the_page_says_so_when_latexmk_is_missing(client, drafted, monkeypatch):
    """REVIEW FOCUS 3: a supported state. The source view must keep working,
    and the page must explain why there is no PDF rather than showing a
    broken frame or a bare error."""
    from scieflow.core import preview

    monkeypatch.setattr(preview.shutil, "which", lambda name: None)
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "latexmk" in page.text
    assert "Yield was 95" in page.text, "the source view still works"

    response = post(client, "/runs/r1/preview", source="agent:claude",
                    follow_redirects=True)
    assert response.status_code == 200
    assert "latexmk" in response.text


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_a_compiled_preview_is_shown_in_the_page(client, drafted):
    post(client, "/runs/r1/preview", source="agent:claude", follow_redirects=True)
    page = client.get("/runs/r1/drafts")
    assert "<iframe" in page.text
    assert "main.pdf" in page.text

    pdf_path = "manuscript/curation/preview/agent-claude/main.pdf"
    served = client.get(f"/runs/r1/file?path={pdf_path}")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("application/pdf")
    assert "attachment" not in served.headers.get("content-disposition", "")


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_a_failed_compile_shows_the_compiler_error_beside_the_source(client, drafted):
    """REVIEW FOCUS 4: an agent's LaTeX will not always build. That is normal
    during drafting, and the error output is the thing a person needs."""
    (drafted / "manuscript" / "drafts" / "claude" / "results.tex").write_text(
        "\\begin{itemize}\nno end\n")
    post(client, "/runs/r1/preview", source="agent:claude", follow_redirects=True)
    page = client.get("/runs/r1/drafts")
    assert "Yield" not in page.text or "itemize" in page.text
    assert "itemize" in page.text or "error" in page.text.lower(), (
        "the compiler's own output must reach the page")
    # The draft's own raw source also contains the word "itemize" (it is the
    # broken .tex itself), which would satisfy the assertion above even with
    # no compile step at all -- so pin down that a *compile actually ran and
    # was recorded as failed* too, which only the implementation can produce.
    assert 'class="log"' in page.text, "no rendered compiler-log block found"
    assert "The last compile failed" in page.text


# ---------------------------------------------------------------------------
# Additions beyond the brief: falsifiable coverage for the two requirements
# named explicitly in the task ("disabled while busy" and "never |safe").
# ---------------------------------------------------------------------------

@pytest.fixture
def running_preview_job(project):
    """A `kind="preview"` job left running, so `service.preview_busy` (and
    the workbench's Compile buttons) see this run as busy -- mirrors
    conftest's `running_job` fixture, but tagged `preview` instead of
    `agent`, since `preview_busy` must match only that kind (a run-wide
    `agent`/`turn` job must never disable the Compile buttons)."""
    from scieflow.core import jobs

    ws = project.run_dir("r1")
    job, proc = jobs.start(project, [sys.executable, "-c", "import time; time.sleep(300)"],
                           kind="preview", cwd=project.root, run_dir=ws, label="preview")
    yield job
    jobs.cancel(job)
    jobs.wait(job, proc)


def test_compile_buttons_disabled_while_a_preview_is_running(
        client, drafted, running_preview_job):
    page = client.get("/runs/r1/drafts")
    assert '<button type="submit" disabled>Compile PDF</button>' in page.text
    assert '<button type="submit">Compile PDF</button>' not in page.text
    assert "already running" in page.text or "already compiling" in page.text


def test_the_compiler_log_is_escaped_not_raw_html(client, drafted, monkeypatch):
    """The compiler log is agent-influenced text (it echoes back whatever
    the agent's `.tex` produced) and must never reach the page through
    `|safe` -- Jinja's default autoescaping is what this test actually
    exercises, by forcing `service.preview_of` to hand back a log containing
    a real HTML tag and checking it comes out escaped."""
    from scieflow.core import service

    malicious = "! LaTeX Error <script>alert(1)</script> in section"

    def fake_preview_of(project, slug, source):
        return {"available": True, "pdf": None, "state": "failed", "log": malicious}

    monkeypatch.setattr(service, "preview_of", fake_preview_of)
    page = client.get("/runs/r1/drafts")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text
