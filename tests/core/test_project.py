import pytest

from scieflow.core.project import Project, ProjectError


def make_repo(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "status.yml").write_text("phases: [a]\n")
    return tmp_path


def test_discover_from_a_start_path_without_chdir(tmp_path):
    root = make_repo(tmp_path)
    (root / "deep" / "er").mkdir(parents=True)
    project = Project.discover(root / "deep" / "er")
    assert project.root == root.resolve()
    assert project.workspace_root == root.resolve() / "workspace"


def test_loaders_read_the_projects_own_files(tmp_path):
    project = Project(make_repo(tmp_path))
    assert project.agents() == {}
    assert project.defaults()["approval"] == "per-campaign"
    assert project.schema("status") == {"phases": ["a"]}


@pytest.mark.parametrize("slug", ["../escape", "a/b", "", "/abs"])
def test_run_dir_refuses_paths_that_are_not_a_slug(tmp_path, slug):
    with pytest.raises(ProjectError):
        Project(make_repo(tmp_path)).run_dir(slug)


def test_run_dir_accepts_workspace_prefix_and_spaces(tmp_path):
    project = Project(make_repo(tmp_path))
    assert project.run_dir("workspace/2026-01-x/") == project.workspace_root / "2026-01-x"
    assert project.run_dir("2026-08-a copy").name == "2026-08-a copy"


def test_state_dir_honours_the_environment(tmp_path, monkeypatch):
    project = Project(make_repo(tmp_path))
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(tmp_path / "elsewhere"))
    assert project.state_dir == tmp_path / "elsewhere"
    monkeypatch.delenv("SCIEFLOW_STATE_DIR")
    assert project.state_dir == project.root / ".scieflow"
