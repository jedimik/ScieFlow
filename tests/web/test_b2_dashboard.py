"""T-9 t5: the dashboard's Archived runs section and the archived badge (B2:5.9, T9-D9)."""

from scieflow.core import archives as a
from scieflow.core.run import budget, status
from tests.fixtures.b2_web import arch_project, dvc_bin, pointer_only, web  # noqa: F401
from tests.fixtures.b2_archives import write_pointer


def test_no_archives_says_so(web):
    assert "No archived runs in this checkout." in web.get("/").text


def test_archives_section_lists_pointers_before_open_gates(arch_project, web):
    pointer_only(arch_project, "r1")
    pointer_only(arch_project, "r2")
    body = web.get("/").text
    assert "<h2>Archived runs</h2>" in body
    assert 'href="/archives/r1"' in body and 'href="/archives/r2"' in body
    assert body.index("Archived runs") < body.index("Open gates")


def test_cap_note_at_cap_plus_one(arch_project, web):
    for i in range(a.LIST_CAP + 1):
        write_pointer(arch_project, f"s{i:04d}")
    assert "1 more not shown (cap 500)" in web.get("/").text


def test_archived_badge_on_a_local_run(arch_project, web):
    ws = arch_project.run_dir("r1")
    (ws / "logs").mkdir(parents=True)
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 60))
    pointer_only(arch_project, "r1")
    assert '<a href="/archives/r1"><span class="dim">archived</span></a>' in web.get("/").text
