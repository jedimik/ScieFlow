"""T-12 t5: the read-only workspace page (`GET /workspace`) and its JSON twin.

Synthetic temporary projects only (the `project` and `client` fixtures). The page
has no form, writes nothing, and escapes every name it shows.
"""

import os
from pathlib import Path

from scieflow.core import service

ODD = 'a"<b>c'


def _tree(path: Path) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(path):
        for name in sorted(dirnames + filenames):
            full = Path(dirpath, name)
            st = os.lstat(full)
            out.append((str(full.relative_to(path)), st.st_mtime_ns,
                        full.read_bytes() if full.is_file() else None))
    return sorted(out, key=lambda row: row[0])


def _strip_time(index: dict) -> dict:
    return {k: v for k, v in index.items() if k != "observed_at"}


def test_page_lists_runs_with_canonical_links(client):
    response = client.get("/workspace")
    assert response.status_code == 200
    assert 'href="/runs/r1"' in response.text
    assert "/runs/r1/health" not in response.text   # the health page is t6


def test_page_escapes_names(client, project):
    (project.root / "workspace" / ODD).mkdir()
    page = client.get("/workspace").text
    assert "<b>c" not in page
    assert "&lt;b&gt;c" in page or "&#34;" in page


def test_api_equals_the_service_output(client, project):
    body = client.get("/api/v1/workspace").json()
    assert _strip_time(body) == _strip_time(service.workspace_index(project))


def test_page_survives_a_mis_shaped_status(client, project):
    odd = project.root / "workspace" / "odd-shape"
    odd.mkdir()
    (odd / "status.yml").write_text("run: odd\nphases: 5\n")
    response = client.get("/workspace")
    assert response.status_code == 200
    assert 'href="/runs/r1"' in response.text


def test_the_dashboard_nav_links_the_page(client):
    assert 'href="/workspace"' in client.get("/").text


def test_the_page_writes_nothing(client, project):
    before = _tree(project.root)
    client.get("/workspace")
    client.get("/api/v1/workspace")
    assert _tree(project.root) == before
    assert not (project.root / "workspace" / "INDEX.md").exists()


def test_page_and_api_survive_an_alias_bomb(client, project):
    odd = project.root / "workspace" / "bomb"
    odd.mkdir()
    levels = 6
    lines = ["a0: &a0 [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]"]
    for i in range(1, levels + 1):
        lines.append(f"a{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 10) + "]")
    (odd / "status.yml").write_text("\n".join(lines) + "\nrun: bomb\nid: *a6\n")
    page = client.get("/workspace")
    assert page.status_code == 200
    assert len(page.content) < 200_000
    api = client.get("/api/v1/workspace")
    assert api.status_code == 200
    assert len(api.content) < 200_000
    assert 'href="/runs/r1"' in page.text


def test_the_page_has_no_form_and_no_dialog_or_safe_filter(project):
    template = Path(__file__).resolve().parents[2] / "src/scieflow/web/templates"
    for name in ("workspace.html", "base.html"):
        text = (template / name).read_text()
        for banned in ("alert(", "confirm(", "prompt(", "|safe"):
            assert banned not in text, f"{name} contains {banned}"
    page_template = (template / "workspace.html").read_text()
    assert "<form" not in page_template
