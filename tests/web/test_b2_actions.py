"""T-9 t5: actions from the page and the API (B2:5.4, 5.7, 5.13 row 2; T9-D2; the consent bounds)."""

from scieflow.core import archives as a
from tests.fixtures import b2_fake_dvc as fake
from tests.fixtures.b2_archives import MD5, OTHER_MD5, write_pointer
from tests.fixtures.b2_web import (arch_project, dvc_bin, pointer_only, post, ready_archive, roomy_disk,  # noqa: F401
                                   started, wait_idle, web)


def sized_pointer(project, size: int | None, slug: str = "r1") -> None:
    write_pointer(project, slug, md5=MD5, size=size)


def test_fetch_at_threshold_renders_confirmation_and_starts_nothing(arch_project, web, roomy_disk):
    sized_pointer(arch_project, a.CONSENT_BYTES)
    r = post(web, "/archives/r1/fetch")
    assert r.status_code == 200
    assert f'name="confirm_size" value="{a.CONSENT_BYTES}"' in r.text
    assert "Confirm" in r.text and 'href="/archives/r1"' in r.text
    assert not started(arch_project)


def test_fetch_below_threshold_starts_on_one_click(arch_project, web, roomy_disk, dvc_bin):
    sized_pointer(arch_project, a.CONSENT_BYTES - 1)
    fake.set_script(dvc_bin, exit_code=0)
    r = post(web, "/archives/r1/fetch")
    assert r.status_code == 303 and r.headers["location"] == "/archives/r1"
    assert started(arch_project)
    wait_idle(arch_project, "r1")


def test_confirmed_threshold_starts(arch_project, web, roomy_disk, dvc_bin):
    sized_pointer(arch_project, a.CONSENT_BYTES)
    fake.set_script(dvc_bin, exit_code=0)
    r = post(web, "/archives/r1/fetch", confirm_size=str(a.CONSENT_BYTES))
    assert r.status_code == 303
    assert started(arch_project)
    wait_idle(arch_project, "r1")


def test_confirm_mismatch_starts_nothing(arch_project, web, roomy_disk):
    sized_pointer(arch_project, a.CONSENT_BYTES)
    r = post(web, "/archives/r1/fetch", confirm_size="5")
    assert "The size changed since the confirmation was shown" in r.text
    assert not started(arch_project)


def test_size_unknown_refuses_and_names_the_terminal_command(arch_project, web, roomy_disk):
    sized_pointer(arch_project, None)
    r = post(web, "/archives/r1/fetch")
    assert r.status_code == 200
    assert "scieflow workspace archive fetch r1 --yes" in r.text
    assert not started(arch_project)


def test_replace_needs_consent_and_renders_the_panel(arch_project, web, roomy_disk):
    data = ready_archive(arch_project, "r1")
    write_pointer(arch_project, "r1", md5=OTHER_MD5, size=len(data))   # the local copy is now not the verified one
    r = post(web, "/archives/r1/fetch")
    assert "Download it again with replace to overwrite it." in r.text
    assert not started(arch_project)
    r2 = post(web, "/archives/r1/fetch", replace="1")
    assert 'name="replace" value="1"' in r2.text
    assert 'name="confirm_size"' in r2.text
    assert not started(arch_project)


def test_yes_is_not_reachable_from_the_web(arch_project, web, roomy_disk):
    sized_pointer(arch_project, a.CONSENT_BYTES)
    r = post(web, "/archives/r1/fetch", yes="1")
    assert 'name="confirm_size"' in r.text
    assert not started(arch_project)


def test_api_refusal_is_409_with_reason_and_message(arch_project, web, roomy_disk):
    sized_pointer(arch_project, a.CONSENT_BYTES)
    r = post(web, "/api/v1/archives/r1/fetch")
    assert r.status_code == 409
    assert r.json()["reason"] == "confirm-required"
    assert "confirm" in r.json()["message"].lower()
    assert not started(arch_project)


def test_api_refusal_on_yes_is_still_refused(arch_project, web, roomy_disk):
    sized_pointer(arch_project, a.CONSENT_BYTES)
    r = post(web, "/api/v1/archives/r1/fetch", yes="1")
    assert r.status_code == 409 and r.json()["reason"] == "confirm-required"


def test_second_quick_post_is_refused_locked_and_one_job_runs(arch_project, web, roomy_disk, dvc_bin):
    sized_pointer(arch_project, a.CONSENT_BYTES - 1)
    fake.set_script(dvc_bin, exit_code=0, sleep=2)
    assert post(web, "/archives/r1/fetch").status_code == 303
    second = post(web, "/archives/r1/fetch")
    assert "Another archive operation on this run is in progress." in second.text
    wait_idle(arch_project, "r1")
    jobs = list((arch_project.state_dir / "jobs").glob("*.json"))
    assert len(jobs) == 1


def test_cancel_with_no_job_returns_to_the_page(arch_project, web):
    pointer_only(arch_project, "r1")
    r = post(web, "/archives/r1/cancel")
    assert r.status_code == 303 and r.headers["location"] == "/archives/r1"


def test_csrf_token_is_never_in_a_url(arch_project, web):
    pointer_only(arch_project, "r1")
    body = web.get("/archives/r1").text
    assert "csrf_token=" not in body
