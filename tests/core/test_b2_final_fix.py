"""T-9 final fix (final review r1, finding 1 and 2): a legacy pointer's count and command reach the rows,
and `archived_runs` lists `workspace/` once for the whole list (B2:5.2 T8-D9, B2:5.6, T9-D9).
"""

import pytest

from scieflow.core import archives as a
from scieflow.core import service
from tests.fixtures.b2_archives import make_project, write_legacy_pointer, write_pointer


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path)


def test_legacy_pointer_row_carries_its_file_count_and_pull_command(project):
    write_legacy_pointer(project, "old", nfiles=4321)
    row = service.archived_runs(project)["rows"][0]
    assert row["kind"] == "directory"
    assert row["nfiles"] == 4321
    assert row["command"] == "uv run scripts/dvc_sync.py pull old"


def test_archive_detail_of_a_legacy_pointer_carries_the_count(project):
    write_legacy_pointer(project, "old", nfiles=7)
    detail = service.archive_detail(project, "old")
    assert detail["nfiles"] == 7
    assert detail["command"] == "uv run scripts/dvc_sync.py pull old"


@pytest.mark.parametrize("count", [60, a.LIST_CAP + 1])
def test_archived_runs_lists_workspace_once_for_the_whole_list(project, monkeypatch, count):
    for index in range(count):
        write_pointer(project, f"r{index:03d}")
    listed = []
    real_names = a._names

    def spy(root, parts):
        listed.append(tuple(parts))
        return real_names(root, parts)

    monkeypatch.setattr(a, "_names", spy)
    doc = service.archived_runs(project)
    assert listed.count(("workspace",)) == 1
    assert listed.count(("workspace", "_archives")) == 1
    assert len(doc["rows"]) == min(count, a.LIST_CAP)
