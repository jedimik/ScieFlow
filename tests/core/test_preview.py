"""Compiling one whole draft into a PDF.

A section `.tex` has no `\\documentclass`, and the template's `main.tex`
`\\input`s `sections/` — the *merged* output — so neither can preview a
draft. `assemble` builds a throwaway document around the directory being
previewed instead, in a scratch dir, never touching the run's manuscript.
"""

import os
import shutil
import subprocess
from datetime import date

import pytest

from scieflow.core import drafts, jobs, preview, service


@pytest.fixture
def drafted(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / "claude"
    d.mkdir(parents=True)
    (d / "introduction.tex").write_text("Catalysis matters.\n")
    (d / "results.tex").write_text("Yield was 95\\%.\n")
    return ws


def test_shell_escape_is_never_passed(drafted):
    """THE security property of this feature. The `.tex` was written by an
    agent, so `\\write18` would turn a preview into command execution. This
    asserts the flag's absence directly, because a test that only checked
    the compile succeeded would pass after someone added it."""
    argv = preview.compile_argv()
    joined = " ".join(argv)
    assert "-shell-escape" not in joined
    assert "--shell-escape" not in joined
    assert "-enable-write18" not in joined
    assert "shell-escape" not in joined, "not in any spelling, not in any argument"
    assert argv[0] == preview.LATEXMK
    assert "-halt-on-error" in argv and "-interaction=nonstopmode" in argv
    assert "-norc" in argv, (
        "without -norc, latexmk executes a .latexmkrc from the compile "
        "directory as Perl -- and that directory is writable by the same "
        "agent whose .tex is untrusted")


def test_assemble_builds_a_document_around_the_draft(drafted):
    dest = drafted / "scratch"
    main = preview.assemble(drafted, "agent:claude", dest)
    text = main.read_text()
    assert "\\documentclass" in text
    assert "\\input{preamble}" in text
    assert (dest / "preamble.tex").exists(), "the preamble must travel with it"
    for section in ("introduction", "results"):
        assert f"\\input{{{section}}}" in text, "inputs point at the draft, not sections/"
        assert (dest / f"{section}.tex").exists()


def test_assemble_never_writes_into_the_runs_manuscript(drafted):
    before = sorted(p.relative_to(drafted) for p in (drafted / "manuscript").rglob("*"))
    preview.assemble(drafted, "agent:claude", drafted / "manuscript" / "curation" / "preview" / "x")
    after = sorted(p.relative_to(drafted) for p in (drafted / "manuscript").rglob("*"))
    added = set(after) - set(before)
    assert added, "nothing was written at all"
    assert all(str(p).startswith("manuscript/curation") for p in added), (
        f"a preview wrote outside its scratch directory: {added}")


def test_the_bibliography_is_emitted_only_when_there_is_one(drafted):
    """`-halt-on-error` plus a missing references.bib halts the compile, so a
    draft with no bibliography yet must still be previewable."""
    plain = preview.assemble(drafted, "agent:claude", drafted / "s1").read_text()
    assert "\\bibliography{" not in plain

    (drafted / "manuscript").mkdir(exist_ok=True)
    (drafted / "manuscript" / "references.bib").write_text("@article{a,title={t}}\n")
    withbib = preview.assemble(drafted, "agent:claude", drafted / "s2")
    assert "\\bibliography{references}" in withbib.read_text()
    assert (withbib.parent / "references.bib").exists()


def test_the_runs_own_preamble_wins_over_the_template(drafted):
    (drafted / "manuscript").mkdir(exist_ok=True)
    (drafted / "manuscript" / "preamble.tex").write_text("% the run's own preamble\n")
    main = preview.assemble(drafted, "agent:claude", drafted / "s")
    assert "the run's own preamble" in (main.parent / "preamble.tex").read_text()


def test_a_draft_before_assembly_uses_the_shipped_template(drafted):
    """Pre-Phase-5 there is no manuscript/preamble.tex; a preview must not
    demand the user assemble first."""
    main = preview.assemble(drafted, "agent:claude", drafted / "s")
    assert "geometry" in (main.parent / "preamble.tex").read_text()


def test_placeholders_get_plain_fallbacks(drafted):
    """A preview must not require the author list to be settled — and the
    fallback must be real text, not merely an empty string where the
    placeholder used to be."""
    text = preview.assemble(drafted, "agent:claude", drafted / "s").read_text()
    assert "\\title{Draft preview}" in text
    assert "\\author{r1}" in text, "falls back to the run's own slug"
    assert f"\\date{{{date.today().isoformat()}}}" in text
    for placeholder in ("%%TITLE%%", "%%AUTHORS%%", "%%DATE%%"):
        assert placeholder not in text


def test_only_the_abstract_section_lands_inside_the_abstract_environment(drafted):
    """`article`'s abstract environment is `\\small\\quotation` — collapsing
    every section into the template's first `\\input{sections/...}` line
    (which sits inside it) rendered the whole manuscript that way, for
    every draft, whether or not it had an abstract at all."""
    (drafted / "manuscript" / "drafts" / "claude" / "abstract.tex").write_text(
        "Short summary.\n")
    text = preview.assemble(drafted, "agent:claude", drafted / "s").read_text()
    start = text.index("\\begin{abstract}")
    end = text.index("\\end{abstract}")
    body = text[start:end]
    assert "\\input{abstract}" in body
    for section in ("introduction", "results"):
        assert f"\\input{{{section}}}" not in body, (
            f"{section} leaked into the abstract environment")


def test_abstract_environment_is_dropped_when_there_is_no_abstract_section(drafted):
    text = preview.assemble(drafted, "agent:claude", drafted / "s").read_text()
    assert "\\begin{abstract}" not in text
    assert "\\end{abstract}" not in text


def test_a_source_with_no_sections_is_refused(project):
    ws = project.run_dir("r1")
    (ws / "manuscript" / "drafts" / "empty").mkdir(parents=True)
    with pytest.raises(preview.PreviewError, match="no sections"):
        preview.assemble(ws, "agent:empty", ws / "s")


def test_a_round_is_previewable_like_a_draft(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged results\n")
    main = preview.assemble(ws, "round:1", ws / "s")
    assert "\\input{results}" in main.read_text()


def test_a_missing_latexmk_is_a_supported_state(project, drafted, monkeypatch):
    """Not a skip. Phase 6 already degrades with a warning; so does this."""
    monkeypatch.setattr(preview.shutil, "which", lambda name: None)
    assert preview.available() is False
    with pytest.raises(service.ServiceError, match="latexmk"):
        service.compile_preview(project, "r1", "agent:claude")
    view = service.preview_of(project, "r1", "agent:claude")
    assert view["available"] is False
    assert "latexmk" in view["log"], "the page must be able to say why"


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_a_real_compile_produces_a_pdf(project, drafted):
    job = service.compile_preview(project, "r1", "agent:claude")
    assert job["state"] == "done", job
    view = service.preview_of(project, "r1", "agent:claude")
    assert view["pdf"] and (drafted / view["pdf"]).exists()
    assert (drafted / view["pdf"]).read_bytes()[:4] == b"%PDF"


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_broken_latex_reports_the_compiler_error_without_failing_the_round(project, drafted):
    """Normal during drafting. The error output is what a person needs."""
    (drafted / "manuscript" / "drafts" / "claude" / "results.tex").write_text(
        "\\begin{itemize}\nno end\n")
    job = service.compile_preview(project, "r1", "agent:claude")
    assert job["state"] == "failed"

    view = service.preview_of(project, "r1", "agent:claude")
    assert view["log"].strip(), "a failed compile with no error output is useless"
    from scieflow.core.run import curation
    assert curation.read(drafted)["round"] == 1, "a failed preview is not a failed round"


def test_the_compile_is_sandboxed_and_confined_to_its_run(project, drafted, monkeypatch):
    seen = {}

    def spy(prj, argv, **kwargs):
        seen["argv"] = argv
        seen["writable"] = kwargs.get("sandbox_writable")
        seen["kind"] = kwargs.get("kind")
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        raise RuntimeError("stop here")

    monkeypatch.setattr(preview.jobs, "run_blocking", spy)
    monkeypatch.setattr(preview.shutil, "which", lambda name: "/usr/bin/latexmk")
    with pytest.raises(RuntimeError):
        service.compile_preview(project, "r1", "agent:claude")

    assert "shell-escape" not in " ".join(seen["argv"])
    assert seen["kind"] == "preview"
    assert seen["writable"] is not None, "a preview must not run unconfined"
    assert seen["writable"][0] == drafted, (
        "a preview is confined to the run it belongs to, like any dispatch")
    assert seen["cwd"] == drafted / "manuscript" / "curation" / "preview" / "agent-claude", (
        "the compile must run inside its own preview directory")
    env_keys = {str(k).lower() for k in (seen["env"] or {})}
    assert "shell_escape" not in env_keys and "openout_any" not in env_keys, (
        "no channel new to this task should carry those settings, even by accident")


def test_preview_dest_refuses_a_traversing_source(project):
    """Pins the guard itself: delete it and the whole suite stays green,
    because nothing else exercises `_preview_dest` with a bad `source`."""
    ws = project.run_dir("r1")
    with pytest.raises(drafts.DraftError):
        service._preview_dest(ws, "agent:../../../../etc")


def test_preview_of_refuses_a_traversing_source_without_raising(project, drafted):
    """Not just "the output happens to look benign" -- proven against a real
    decoy planted exactly where the unguarded path arithmetic lands. A
    colon-free `source` (no "agent:"/"round:" prefix at all) leaves
    `source.replace(":", "-")` untouched, so its leading ".." components are
    real, kernel-honoured parent-directory references -- unlike a prefixed
    one (e.g. `"agent-.."`), which is a literal, nonexistent directory name
    that blocks a real `is_file()`/`read_bytes()` lookup before any ".."
    ever takes effect (`Path.resolve()`'s lexical simplification looks like
    an escape but is never what a real read call does; a real lookup also
    needs every intermediate directory to actually exist, which is why this
    uses `drafted` and pre-creates `PREVIEW_DIR` -- exactly the state any
    run is in after its first compile of anything). Four ".." cancel
    `PREVIEW_DIR`'s three segments plus the run itself, landing one level
    *above* `ws` -- still inside the project, but outside every run's own
    confinement."""
    ws = drafted
    (ws / preview.PREVIEW_DIR).mkdir(parents=True, exist_ok=True)
    decoy_dir = ws.parent / "decoy"
    decoy_dir.mkdir(parents=True)
    (decoy_dir / "main.pdf").write_bytes(b"%PDF-not this run's")

    source = "../../../../decoy"
    view = service.preview_of(project, "r1", source)
    assert view == {"available": True, "pdf": None, "state": None, "log": ""}, (
        "a decoy file outside the run must never be reported as this run's preview")


def test_preview_of_before_any_compile_is_the_normal_state(project, drafted):
    """The state every draft is in on first page load."""
    view = service.preview_of(project, "r1", "agent:claude")
    assert view == {"available": True, "pdf": None, "state": None, "log": ""}


def _save_fake_preview_job(project, ws, cwd, pid, job_id="01ARZ3NDEKTSV4RRFFQ69G5FAV"):
    """A `kind="preview"` job record, `state="running"`, as if some earlier
    compile started it -- without actually running `latexmk`."""
    jobs_dir = jobs.jobs_dir(project, ws)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job = jobs.Job(id=job_id, kind="preview", argv=["latexmk"], cwd=str(cwd),
                   run_dir=str(ws), state="running", pid=pid,
                   log=str(jobs_dir / f"{job_id}.log"))
    jobs.save(job)
    return job


def test_a_running_preview_for_a_different_source_refuses_a_new_compile(project, drafted):
    """Widened from "same source" to "same run": the shared `.texmf-cache`
    (one tree per run, not per source) means two concurrent compiles of
    *different* sources would race on it just the same."""
    other_dest = drafted / "manuscript" / "curation" / "preview" / "round-1"
    other_dest.mkdir(parents=True)
    _save_fake_preview_job(project, drafted, other_dest, pid=os.getpid())

    with pytest.raises(service.ServiceError, match="already compiling"):
        service.compile_preview(project, "r1", "agent:claude")


def test_the_refusal_message_says_what_is_happening_and_how_to_clear_it(project, drafted):
    """Task 6 will disable the button; this message is what a person
    actually sees when they hit the refusal anyway."""
    other_dest = drafted / "manuscript" / "curation" / "preview" / "round-1"
    other_dest.mkdir(parents=True)
    _save_fake_preview_job(project, drafted, other_dest, pid=os.getpid())

    with pytest.raises(service.ServiceError) as exc_info:
        service.compile_preview(project, "r1", "agent:claude")
    message = str(exc_info.value)
    assert "already compiling" in message, "must say what is happening"
    assert "cancel" in message.lower() and "job list" in message, (
        "must say how to clear it")


def test_a_stale_running_record_does_not_block_a_new_compile(project, drafted, monkeypatch):
    """THE important test: a `"running"` record survives whatever killed its
    process -- crashed, OOM-killed, orphaned by a host restart -- with
    nothing to correct it automatically except `jobs.reconcile`. Without
    calling that first, this refusal would wedge every future preview of
    the run shut forever, which is worse than the race it exists to
    prevent.

    The stale job is recorded against `dest` -- the exact preview
    directory `"agent:claude"` itself compiles to -- not a different
    source's. Recording it under a different source would still pass this
    test even against the pre-`jobs.reconcile` code, because that code
    gated on `cwd == dest` and would never have looked at a different
    source's job in the first place; the test would then be passing for
    the wrong reason, satisfied by the *old* code's own narrower scope
    rather than by `reconcile` doing anything. Matching `dest` is what
    makes `reconcile` the only thing standing between "refused forever"
    and "compile proceeds" -- which is the regression this test exists to
    guard.
    """
    dead = subprocess.Popen(["true"])
    dead.wait()  # guaranteed not alive: reaped, not just exited

    dest = drafted / "manuscript" / "curation" / "preview" / "agent-claude"
    dest.mkdir(parents=True)
    _save_fake_preview_job(project, drafted, dest, pid=dead.pid)

    def spy(prj, argv, **kwargs):
        raise RuntimeError("reached run_compile -- the stale record did not block it")

    monkeypatch.setattr(preview.jobs, "run_blocking", spy)
    monkeypatch.setattr(preview.shutil, "which", lambda name: "/usr/bin/latexmk")

    with pytest.raises(RuntimeError, match="reached run_compile"):
        service.compile_preview(project, "r1", "agent:claude")


def test_log_tail_under_the_limit_returns_the_whole_file(tmp_path):
    p = tmp_path / "main.log"
    p.write_text("short log\n")
    assert service._log_tail(p, limit=1000) == "short log\n"


def test_log_tail_of_a_missing_log_is_empty(tmp_path):
    assert service._log_tail(tmp_path / "no-such.log", limit=1000) == ""


def test_log_tail_truncates_a_large_log(tmp_path):
    p = tmp_path / "main.log"
    p.write_text("X" * 5000 + "TAIL-MARKER")
    text = service._log_tail(p, limit=100)
    assert text.startswith("… (log truncated)\n")
    assert "TAIL-MARKER" in text
    assert text.count("X") < 5000, "must not be the whole file"


def test_log_tail_survives_a_seek_that_splits_a_multibyte_character(tmp_path):
    """The contract is "never raises" -- `errors="replace"` substitutes
    U+FFFD for whatever byte sequence the seek happens to land inside, but
    nothing in the suite pinned that until now."""
    prefix = "A" * 10
    multibyte = "日"          # 3 bytes in UTF-8: E6 97 A5
    suffix = "B" * 10
    data = (prefix + multibyte + suffix).encode("utf-8")
    p = tmp_path / "main.log"
    p.write_bytes(data)

    # `limit` is chosen so `size - limit` lands one byte into `multibyte`'s
    # 3-byte encoding -- a continuation byte, invalid as a sequence start.
    split_at = len(prefix.encode("utf-8")) + 1
    limit = len(data) - split_at

    text = service._log_tail(p, limit=limit)  # must not raise
    assert text.endswith(suffix)
    assert "�" in text, "the split byte must be replaced, not dropped or raised on"
