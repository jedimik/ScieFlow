import pytest

from scieflow.web import files


def test_resolve_accepts_a_file_inside_the_run(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.md").write_text("hi")
    assert files.resolve(tmp_path, "a/x.md").name == "x.md"


@pytest.mark.parametrize("bad", ["../secrets.txt", "a/../../secrets.txt",
                                 "/etc/passwd", "a/../..", ""])
def test_resolve_refuses_anything_outside_the_run(tmp_path, bad):
    (tmp_path / "a").mkdir()
    (tmp_path.parent / "secrets.txt").write_text("nope")
    with pytest.raises(ValueError):
        files.resolve(tmp_path, bad)


def test_resolve_refuses_a_symlink_pointing_out(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope")
    (tmp_path / "link.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        files.resolve(tmp_path, "link.txt")


def test_listing_sorts_directories_first(tmp_path):
    (tmp_path / "zdir").mkdir()
    (tmp_path / "a.md").write_text("x")
    names = [entry["name"] for entry in files.listing(tmp_path, "")]
    assert names == ["zdir", "a.md"]


def test_files_page_lists_the_run(client, project):
    ws = project.run_dir("r1")
    (ws / "iterations").mkdir(exist_ok=True)
    (ws / "iterations" / "hypothesis.md").write_text("# H1\n")
    body = client.get("/runs/r1/files").text
    assert "iterations" in body and "status.yml" in body


def test_text_file_is_shown_inline(client, project):
    (project.run_dir("r1") / "note.md").write_text("# a heading\n")
    body = client.get("/runs/r1/file", params={"path": "note.md"}).text
    assert "# a heading" in body


def test_binary_file_is_served_with_its_media_type(client, project):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    (project.run_dir("r1") / "figure.png").write_bytes(png)
    response = client.get("/runs/r1/file", params={"path": "figure.png"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_traversal_through_the_route_is_refused(client, project):
    (project.root / "config" / "agents.yml").exists()
    response = client.get("/runs/r1/file", params={"path": "../../config/agents.yml"})
    assert response.status_code == 400
    assert "escapes" in response.text


def test_missing_file_is_404(client):
    assert client.get("/runs/r1/file", params={"path": "nope.md"}).status_code == 404
