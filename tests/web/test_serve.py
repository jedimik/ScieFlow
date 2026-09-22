import pytest
from click.testing import CliRunner

from scieflow.core.project import Project
from scieflow.web import auth
from scieflow.web.app import create_app
from scieflow.web.serve import serve


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_accepted(host):
    assert auth.loopback_only(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_non_loopback_is_refused(host):
    with pytest.raises(ValueError, match="loopback"):
        auth.loopback_only(host)


def test_serve_refuses_a_non_loopback_bind(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    result = CliRunner().invoke(serve, ["--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "loopback" in result.output and "tunnel" in result.output


def test_tokens_are_long_and_unique():
    tokens = {auth.new_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


def test_healthz_needs_no_session(tmp_path):
    from fastapi.testclient import TestClient

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    app = create_app(Project(tmp_path), "tok")
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    # liveness only: never leak the filesystem layout or the token
    assert str(tmp_path) not in response.text and "tok" not in response.text


def test_no_cors_headers_are_ever_sent(tmp_path):
    from fastapi.testclient import TestClient

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    app = create_app(Project(tmp_path), "tok")
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
