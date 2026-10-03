"""The overview band on the run page: panel 1, what exists on disk."""

from markupsafe import escape


def _touch(ws, *parts):
    path = ws.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n")


def _band(page):
    start = page.text.index('id="overview"')
    return page.text[start:page.text.index("The agreed plan")]


def test_the_band_sits_above_the_existing_sections(client, project):
    page = client.get("/runs/r1")
    assert page.status_code == 200
    assert page.text.index('id="overview"') < page.text.index("The agreed plan")


def test_a_run_before_phase_3_names_the_phase_not_zeros(client, project):
    band = _band(client.get("/runs/r1"))
    assert "no drafts yet" in band
    assert "Phase 3" in band
    assert "exists but" not in band


def test_an_empty_drafts_directory_reads_differently_from_an_absent_one(client, project):
    (project.run_dir("r1") / "manuscript" / "drafts").mkdir(parents=True)
    band = _band(client.get("/runs/r1"))
    assert "manuscript/drafts/ exists but no agent has drafted" in band
    assert "no drafts yet" not in band


def test_the_band_shows_the_agents_and_sections_the_run_used(client, project):
    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "drafts", "kim", "intro.tex")
    _touch(ws, "manuscript", "drafts", "lee", "results.tex")
    _touch(ws, "findings", "kim.json")
    _touch(ws, "gaps", "lee.json")
    _touch(ws, "manuscript", "curation", "rounds", "1", "intro.tex")
    _touch(ws, "review", "round-1", "review.md")
    band = _band(client.get("/runs/r1"))
    for token in ("kim", "lee", "intro", "results", "Merge round 1", "Review round 1"):
        assert token in band, token


def test_hostile_agent_and_section_names_render_escaped(client, project):
    name = 'evil"><script>alert(1)'
    section = 'sec"><img src=x onerror=alert(2)>'
    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "drafts", name, f"{section}.tex")
    _touch(ws, "findings", f"{name}.json")
    page = client.get("/runs/r1")
    assert page.status_code == 200
    assert "<script>alert(1)" not in page.text
    assert "<img src=x" not in page.text
    band = _band(page)
    assert str(escape(name)) in band
    assert str(escape(section)) in band


def test_the_band_never_triggers_a_provenance_sync(client, project, monkeypatch):
    from scieflow.core import service

    def boom(*a, **k):
        raise AssertionError("the run page must not sync provenance")

    monkeypatch.setattr(service.provenance, "sync", boom)
    _touch(project.run_dir("r1"), "manuscript", "drafts", "kim", "intro.tex")
    assert client.get("/runs/r1").status_code == 200


def test_the_band_adds_no_post_form(client, project):
    assert 'id="overview"' in client.get("/runs/r1").text
    assert "<form" not in _band(client.get("/runs/r1"))
