"""T-9 t5 (5.13 row 3): every named failure reaches the page as its fixed sentence, never a traceback or dvc output.

The dvc failures run the real archive job with the fake dvc, which prints the matching line and exits 1.
"""

import html

import pytest

from scieflow.core import archives as a
from tests.fixtures import b2_fake_dvc as fake
from tests.fixtures.b2_archives import MD5, dead_pid, tree_snapshot, write_job, write_job_pointer, write_pointer
from tests.fixtures.b2_web import (arch_project, dvc_bin, pointer_only, post, ready_archive, roomy_disk,  # noqa: F401
                                   wait_idle, web, zip_bytes)

DVC_LINES = {
    "no-remote": "ERROR: config file error: no remote specified",
    "no-credentials": "ERROR: NoCredentialsError: Unable to locate credentials",
    "credentials-refused": "ERROR: InvalidAccessKeyId: the key is not valid",
    "remote-unreachable": "ERROR: could not connect to the endpoint URL",
    "missing-on-remote": "ERROR: missing cache files for this output",
    "disk-full": "ERROR: [Errno 28] No space left on device",
}


def page_text(web, path: str = "/archives/r1") -> str:
    return html.unescape(web.get(path).text)


@pytest.mark.parametrize("code", sorted(DVC_LINES))
def test_dvc_failure_shows_its_sentence_and_never_dvc_output(arch_project, web, roomy_disk, dvc_bin, code):
    pointer_only(arch_project, "r1", size=6)
    fake.set_script(dvc_bin, exit_code=1, stderr=[DVC_LINES[code]])
    assert post(web, "/archives/r1/fetch").status_code == 303
    wait_idle(arch_project, "r1")
    body = page_text(web)
    assert a.sentence(code, md5=MD5) in body
    assert "Traceback" not in body
    assert DVC_LINES[code].split(": ", 1)[1] not in body     # dvc's own words stay in the job log


def test_interrupted_job_is_read_on_get_without_a_write(arch_project, web):
    pointer_only(arch_project, "r1")
    job_id = write_job(arch_project, "r1", kind="archive-fetch", state="running", pid=dead_pid())
    write_job_pointer(arch_project, "r1", job_id + "\n")
    before = (tree_snapshot(arch_project.root), tree_snapshot(arch_project.state_dir))
    body = page_text(web)
    assert a.sentence("interrupted") in body
    assert (tree_snapshot(arch_project.root), tree_snapshot(arch_project.state_dir)) == before


def test_interrupted_job_becomes_lost_after_the_next_post(arch_project, web):
    pointer_only(arch_project, "r1")
    job_id = write_job(arch_project, "r1", kind="archive-fetch", state="running", pid=dead_pid())
    write_job_pointer(arch_project, "r1", job_id + "\n")
    assert post(web, "/archives/r1/cancel").status_code == 303
    record = (arch_project.state_dir / "jobs" / f"{job_id}.json").read_text()
    assert '"state": "lost"' in record                     # the action wrote `lost` (B2:5.6.1)
    assert a.sentence("interrupted") in page_text(web)     # a lost job reads as interrupted on the page


def test_partial_download_leftover_is_named(arch_project, web):
    pointer_only(arch_project, "r1")
    (arch_project.workspace_root / "_archives" / "r1.zip.partial").write_bytes(b"x")
    assert "_archives/r1.zip.partial" in page_text(web)


def test_dvc2_pointer_renders_without_a_traceback(arch_project, web):
    write_pointer(arch_project, "r1", size=6, hash_key=None)
    r = web.get("/archives/r1")
    assert r.status_code == 200 and "Traceback" not in r.text


def test_unreadable_pointer_renders_without_a_traceback(arch_project, web):
    write_pointer(arch_project, "r1", text="outs: [1, 2]\n")
    r = web.get("/archives/r1")
    assert r.status_code == 200 and "Traceback" not in r.text
    assert "The pointer file cannot be read" in page_text(web)


def test_archive_without_status_yml_says_so(arch_project, web):
    ready_archive(arch_project, "r1", zip_bytes({"notes/log.md": b"hi\n"}))
    assert "no status.yml" in page_text(web)


def test_old_archive_with_provenance_git_renders(arch_project, web):
    ready_archive(arch_project, "r1", zip_bytes({"provenance.git/HEAD": b"ref: x\n",
                                                 "status.yml": b"kind: loop\n"}))
    r = web.get("/archives/r1")
    assert r.status_code == 200 and "Traceback" not in r.text


def test_failure_page_uses_the_alert_paragraph_for_refusals(arch_project, web, roomy_disk):
    write_pointer(arch_project, "r1", size=None)
    r = post(web, "/archives/r1/fetch")
    assert '<p class="state-failed" role="alert">' in r.text
