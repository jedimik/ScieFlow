"""T-9 t4: archived runs stay off the MCP server (B2:5.11 item 6; T9-D10; T8-D18).

No MCP tool serves an archive function, and the runs tool (list_runs_with_budget) lists only
local runs, so an archived-only slug never reaches an agent.
"""

from scieflow.core import service
from scieflow.mcp_server import tools
from tests.fixtures.b2_archives import make_project, write_pointer

ARCHIVE_FUNCTIONS = {"archived_runs", "archive_detail", "archive_entries", "archive_member",
                     "fetch_archive", "restore_archive", "cancel_archive_job"}


def test_no_mcp_tool_is_named_or_wired_to_an_archive_function():
    assert not any("archive" in tool.name for tool in tools.TOOLS)
    assert not {tool.service for tool in tools.TOOLS} & ARCHIVE_FUNCTIONS


def test_the_runs_tool_omits_an_archived_only_run(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "archived-only-run")
    (project.workspace_root / "local-run").mkdir()
    (project.workspace_root / "local-run" / "config.yml").write_text("{}\n")

    listed = service.list_runs_with_budget(project)
    slugs = {row["slug"] for row in listed}

    assert "local-run" in slugs
    assert "archived-only-run" not in slugs
