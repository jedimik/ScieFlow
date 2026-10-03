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

    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("the run page must not sync provenance")

    monkeypatch.setattr(service.provenance, "sync", boom)
    _touch(project.run_dir("r1"), "manuscript", "drafts", "kim", "intro.tex")
    assert client.get("/runs/r1").status_code == 200
    assert calls == []


def test_a_raising_second_panel_leaves_the_band_and_page_up(client, project, monkeypatch):
    from scieflow.core import service

    def boom(ws):
        raise RuntimeError("second panel exploded")

    monkeypatch.setitem(service._PANELS, "boom", boom)
    _touch(project.run_dir("r1"), "manuscript", "drafts", "kim", "intro.tex")
    page = client.get("/runs/r1")
    assert page.status_code == 200
    assert "kim" in _band(page) and "intro" in _band(page)


def test_findings_label_counts_agents_not_findings(client, project):
    ws = project.run_dir("r1")
    for name in ("a", "b", "c"):
        _touch(ws, "findings", f"{name}.json")
    band = _band(client.get("/runs/r1"))
    assert "3 agents (a, b, c)" in band


def test_empty_directories_render_their_own_reason(client, project):
    ws = project.run_dir("r1")
    for sub in ("findings", "gaps", "manuscript/curation/rounds", "review"):
        (ws / sub).mkdir(parents=True)
    band = _band(client.get("/runs/r1"))
    for text in ("findings/ exists but holds no findings yet",
                 "gaps/ exists but holds no gaps yet",
                 "manuscript/curation/rounds/ exists but no merge round has completed",
                 "review/ exists but holds no review round"):
        assert text in band, text
    assert "no findings/ directory yet" not in band
    assert "no review round yet" not in band


def test_the_band_adds_no_post_form(client, project):
    assert 'id="overview"' in client.get("/runs/r1").text
    assert "<form" not in _band(client.get("/runs/r1"))


# --- panel 2: where the manuscript stands ----------------------------------

def _merged(project, hostile=None):
    from scieflow.core import provenance

    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "drafts", "kim", "intro.tex")
    _touch(ws, "manuscript", "drafts", "kim", "results.tex")
    if hostile:
        _touch(ws, "manuscript", "drafts", "kim", f"{hostile}.tex")
    _touch(ws, "manuscript", "curation", "rounds", "1", "results.tex")
    _touch(ws, "manuscript", "curation", "rounds", "2", "results.tex", )
    (ws / "manuscript" / "curation" / "rounds" / "2" / "results.tex").write_text("changed\n")
    provenance.sync(ws)
    return ws


def _manuscript(page):
    band = _band(page)
    start = band.index('id="ov-manuscript"')
    return band[start:band.find('id="ov-', start + 1) if 'id="ov-' in band[start + 1:] else None]


def test_the_manuscript_panel_names_the_round_and_links_the_workbench(client, project):
    _merged(project)
    panel = _manuscript(client.get("/runs/r1"))
    assert "Merge round 2" in panel
    assert "missing from the merge: intro" in panel
    assert "results.tex" in panel
    assert 'href="/runs/r1/drafts"' in panel


def test_the_manuscript_panel_renders_each_no_history_state_distinctly(client, project, monkeypatch):
    from scieflow.core import provenance

    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "curation", "rounds", "1", "results.tex")
    no_repo = _manuscript(client.get("/runs/r1"))
    provenance.ensure_repo(ws)
    no_points = _manuscript(client.get("/runs/r1"))
    monkeypatch.setattr(provenance.shutil, "which", lambda name: None)
    no_git = _manuscript(client.get("/runs/r1"))

    assert "git is not installed" in no_git
    assert "has not been recorded yet" in no_repo
    assert "no points yet" in no_points
    assert len({no_git, no_repo, no_points}) == 3
    for panel in (no_git, no_repo, no_points):
        assert "create" not in panel.lower()
        assert "<form" not in panel and "/drafts" not in panel, "no offer to go build history"
    assert "after the first merge round or workbench visit" in no_repo
    assert "after the first merge round or workbench visit" in no_points


def test_the_manuscript_panel_escapes_a_hostile_section_name(client, project):
    section = 'sec"><img src=x onerror=alert(2)>'
    _merged(project, hostile=section)
    from scieflow.core import provenance
    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "curation", "rounds", "2", f"{section}.tex")
    provenance.sync(ws)
    page = client.get("/runs/r1")
    assert page.status_code == 200
    assert "<img src=x" not in page.text
    assert str(escape(section)) in _manuscript(page)


def test_the_manuscript_panel_never_syncs_on_a_page_get(client, project, monkeypatch):
    from scieflow.core import service

    _merged(project)
    calls = []
    monkeypatch.setattr(service.provenance, "sync", lambda *a, **k: calls.append(a))
    page = client.get("/runs/r1")
    assert "Merge round 2" in _manuscript(page), "the panel must have rendered history"
    assert calls == []
