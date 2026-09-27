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
    # "not 500" alone is close to vacuous -- pin down that the actual
    # refusal (drafts.check_name's own message, surfaced through
    # service.ServiceError) reached the page, not just that some 200 came
    # back.
    assert "not a draft name" in response.text


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
    # No PDF exists yet in this state, so `p.pdf` is None -- nothing should
    # render an <iframe> pointed at a non-existent file (e.g. `?path=None`).
    assert "<iframe" not in page.text, "no broken frame when there is no PDF"

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
    # `"Yield" not in page.text or "itemize" in page.text` (the brief's own
    # phrasing) is tautologically true: the draft's raw source now literally
    # contains "itemize" no matter what the compile did, so that disjunct
    # alone always holds. Assert the un-hedged fact instead: the old content
    # is really gone from the draft view.
    assert "Yield" not in page.text
    assert "itemize" in page.text or "error" in page.text.lower(), (
        "the compiler's own output must reach the page")
    # The draft's own raw source also contains the word "itemize" (it is the
    # broken .tex itself), which would satisfy the assertion above even with
    # no compile step at all -- so pin down that a *compile actually ran and
    # was recorded as failed* too, which only the implementation can produce.
    assert 'class="log"' in page.text, "no rendered compiler-log block found"
    assert "The last compile failed" in page.text


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_a_compiled_round_preview_is_shown_in_the_page(client, drafted):
    """The round-side twin of test_a_compiled_preview_is_shown_in_the_page
    above. `_preview_dest`'s `round:` branch and the round PDF/log render
    path are otherwise only ever touched by tests that never actually
    compile (the "previewable too" test, and the traversal-refusal test) --
    `latexmk` is present on this host, so this runs the round compile for
    real rather than leaving that path unexercised end to end."""
    d = drafted / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("Round one merged text.\n")

    post(client, "/runs/r1/preview", source="round:1", follow_redirects=True)
    page = client.get("/runs/r1/drafts")
    assert "<iframe" in page.text
    assert "main.pdf" in page.text

    pdf_path = "manuscript/curation/preview/round-1/main.pdf"
    served = client.get(f"/runs/r1/file?path={pdf_path}")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("application/pdf")
    assert "attachment" not in served.headers.get("content-disposition", "")


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
    a real HTML tag and checking it comes out escaped.

    A completed round is added alongside the agent draft so both the agent
    panel and the round panel render the malicious log, and `count == 2`
    checks that each one escaped it.

    Deferred minor #20: `count == 2` does NOT structurally require the
    `preview_panel` macro extraction -- the duplicated blocks it replaced
    would have produced two escaped occurrences just the same. The number
    proves both panels escape the log; it proves nothing about how many
    template sites render it. (Nothing here needs to: the escaping is the
    property worth pinning, and the macro is a readability change.)"""
    from scieflow.core import service

    d = drafted / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")

    malicious = "! LaTeX Error <script>alert(1)</script> in section"

    def fake_preview_of(project, slug, source):
        return {"available": True, "pdf": None, "state": "failed", "log": malicious}

    monkeypatch.setattr(service, "preview_of", fake_preview_of)
    page = client.get("/runs/r1/drafts")
    assert "<script>alert(1)</script>" not in page.text
    assert page.text.count("&lt;script&gt;") == 2, (
        "expected the escaped log in both the agent and round panels")


def test_a_dead_previews_jobs_record_self_heals_the_disabled_button(client, project, drafted):
    """IMPORTANT 1 (fix round 1): `service.preview_busy` must reconcile
    stale job records before deciding whether to disable the Compile
    buttons -- `jobs.reconcile` has exactly one caller in this codebase
    before this test existed (`compile_preview`), and `compile_preview` is
    only ever reached by actually clicking a Compile button. Once that
    button can be disabled, a `state="running"` record surviving a crash,
    an OOM kill or a host restart would never be corrected again unless
    the read that disables the button also reconciles -- this is what
    would wedge the workbench shut forever if that call were missing.

    The stale job's pid is guaranteed dead (a reaped `Popen(["true"])`, not
    merely `os.getpid()` of this test process, which very much is alive)."""
    import subprocess

    from scieflow.core import jobs

    dead = subprocess.Popen(["true"])
    dead.wait()

    jobs_dir = jobs.jobs_dir(project, drafted)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job = jobs.Job(id="01STALEPREVIEWJOBRECORD0000", kind="preview", argv=["latexmk"],
                   cwd=str(drafted / "manuscript" / "curation" / "preview" / "agent-claude"),
                   run_dir=str(drafted), state="running", pid=dead.pid,
                   log=str(jobs_dir / "01STALEPREVIEWJOBRECORD0000.log"))
    jobs.save(job)

    page = client.get("/runs/r1/drafts")
    assert '<button type="submit">Compile PDF</button>' in page.text
    assert '<button type="submit" disabled>Compile PDF</button>' not in page.text
    assert "already running" not in page.text and "already compiling" not in page.text


def test_a_pdf_path_with_a_query_metacharacter_is_url_encoded(client, project):
    """Deferred minor #18. `p.pdf` is `agent-<name>/main.pdf`, and `<name>` is
    a directory name an AGENT chose -- not a value from a safe alphabet, which
    is what the original justification for deferring this got wrong. An `&`
    would end the `path` parameter early and a `#` would truncate it to a
    fragment, so the iframe would load the wrong file or none. HTML-escaping
    makes the attribute well-formed; only URL-encoding makes the query string
    mean what it says.

    FALSIFICATION: drop `| urlencode` and the raw `&` appears in `src`, and
    the round-trip below stops resolving.
    """
    ws = project.run_dir("r1")
    name = "a&b#c"
    d = ws / "manuscript" / "drafts" / name
    d.mkdir(parents=True)
    (d / "results.tex").write_text("Yield was 95\\%.\n")
    dest = ws / "manuscript" / "curation" / "preview" / f"agent-{name}"
    dest.mkdir(parents=True)
    (dest / "main.pdf").write_bytes(b"%PDF-1.4 fake")

    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    encoded = "manuscript/curation/preview/agent-a%26b%23c/main.pdf"
    assert f'src="/runs/r1/file?path={encoded}"' in page.text, (
        "the `&` and `#` must be percent-encoded, or the path parameter breaks")
    assert "agent-a&b" not in page.text and "agent-a&amp;b" not in page.text

    # And that URL, decoded the way a browser and Starlette decode it, really
    # does resolve to this preview's PDF -- the encoding is not merely cosmetic.
    served = client.get(f"/runs/r1/file?path={encoded}")
    assert served.status_code == 200 and served.content.startswith(b"%PDF")
