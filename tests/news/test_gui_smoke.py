import pytest
from nicegui import ui
from nicegui.testing import User

from scieflow.news.gui.app import init_pages
from scieflow.news.gui.context import GuiContext

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def ctx(tmp_path):
    cfg = tmp_path / "news.yml"
    cfg.write_text(
        "interests:\n  - name: Snakemake\n"
        "groups:\n  - name: TestGroup\n    interests: [Snakemake]\n"
    )
    return GuiContext(config_path=cfg, db_path=tmp_path / "db.json")


@pytest.fixture(autouse=True)
def _pages(ctx, user):
    # `user` must be resolved first: entering nicegui's user_simulation
    # resets all registered NiceGUI routes/pages, so page registration has
    # to happen after that reset rather than before it.
    init_pages(ctx)


async def test_all_pages_render(user: User) -> None:
    await user.open("/")
    await user.should_see("Interests")
    await user.should_see("Snakemake")
    await user.should_see("TestGroup")
    await user.open("/run")
    await user.should_see("Run")
    await user.open("/reports")
    await user.should_see("Reports")


async def test_reports_page_shows_stored_result(user: User, ctx) -> None:
    from datetime import date

    db = ctx.open_db()
    run_id = db.create_run("claude")
    db.add_result(
        run_id,
        "Snakemake",
        date(2026, 6, 17),
        date(2026, 7, 17),
        "## Snakemake\n### News\n- something happened\n",
    )
    hits = db.search_results("something happened")
    assert len(hits) == 1
    assert hits[0]["run_id"] == run_id
    assert hits[0]["name"] == "Snakemake"
    await user.open("/reports")
    await user.should_see("Snakemake")
    await user.should_see("something happened")
    await user.should_see("Search")


async def test_reports_page_history_shows_multiple_runs(user: User, ctx) -> None:
    from datetime import date

    db = ctx.open_db()
    old_run_id = db.create_run("codex")
    db.add_result(
        old_run_id,
        "Snakemake",
        date(2026, 5, 1),
        date(2026, 5, 31),
        "## Snakemake\n### News\n- older happening\n",
    )
    new_run_id = db.create_run("claude")
    db.add_result(
        new_run_id,
        "Snakemake",
        date(2026, 6, 17),
        date(2026, 7, 17),
        "## Snakemake\n### News\n- newest happening\n",
    )
    await user.open("/reports")
    # ui.table renders rows client-side from its `rows` prop, so `should_see`
    # (which inspects element props/text, not table cell data) can't see
    # "codex"/"claude" as text; inspect the table element's rows directly.
    table = next(iter(user.find(kind=ui.table).elements))
    agents_shown = {row["agent"] for row in table.rows}
    assert agents_shown == {"codex", "claude"}
    # detail section defaults to the newest run's content
    await user.should_see("newest happening")


async def test_reports_page_shows_failure_details(user: User, ctx) -> None:
    from datetime import date

    db = ctx.open_db()
    run_id = db.create_run("claude")
    db.add_result(
        run_id,
        "Snakemake",
        date(2026, 6, 17),
        date(2026, 7, 17),
        "## Snakemake\n### News\n- something happened\n",
    )
    db.add_failure(
        run_id,
        "Broken",
        "no '## Broken' header in agent output",
        raw_output="I refuse to format\n```\nsome fenced junk\n```\nmore text",
    )
    await user.open("/reports")
    await user.should_see("Broken")
    # Regression: raw_output containing a ``` fence must not truncate/mangle
    # the rendered failure expansion — the trailing text past the fence
    # must still be visible.
    await user.should_see("more text")
