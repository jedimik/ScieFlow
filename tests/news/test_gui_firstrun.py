import pytest
from nicegui.testing import User

from scieflow.news.gui.app import init_pages
from scieflow.news.gui.context import GuiContext

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def ctx(tmp_path):
    cfg = tmp_path / "news.yml"  # deliberately not created
    return GuiContext(config_path=cfg, db_path=tmp_path / "db.json")


@pytest.fixture(autouse=True)
def _pages(ctx, user):
    # `user` must be resolved first: entering nicegui's user_simulation
    # resets all registered NiceGUI routes/pages, so page registration has
    # to happen after that reset rather than before it.
    init_pages(ctx)


async def test_interests_page_shows_first_run_card(user: User) -> None:
    await user.open("/")
    await user.should_see("Create example config")


async def test_run_page_shows_no_config_hint(user: User) -> None:
    await user.open("/run")
    await user.should_see("No config yet")
