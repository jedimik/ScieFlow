import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from scieflow.core.project import Project
from scieflow.web import auth
from scieflow.web.app import create_app

TOKEN = "s3cret-token"


@pytest.fixture
def app(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    application = create_app(Project(tmp_path), TOKEN)

    # exercise the real dependencies through routes that exist only in tests
    @application.get("/guarded", dependencies=[Depends(auth.require_session)])
    async def guarded() -> dict:
        return {"seen": True}

    @application.post("/guarded", dependencies=[Depends(auth.require_session),
                                                Depends(auth.csrf_protect)])
    async def guarded_post() -> dict:
        return {"posted": True}

    return application


def test_no_session_is_401(app):
    with TestClient(app) as client:
        assert client.get("/guarded").status_code == 401


def test_wrong_token_is_403_and_issues_nothing(app):
    with TestClient(app) as client:
        response = client.get("/guarded?token=wrong", follow_redirects=False)
        assert response.status_code == 403
        assert auth.SESSION_COOKIE not in response.cookies
        assert client.get("/guarded").status_code == 401


def test_good_token_redirects_without_the_token_and_sets_cookies(app):
    with TestClient(app) as client:
        response = client.get(f"/guarded?token={TOKEN}", follow_redirects=False)
        assert response.status_code == 303
        assert "token" not in response.headers["location"]
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(c for c in cookies if c.startswith(auth.SESSION_COOKIE))
        csrf_cookie = next(c for c in cookies if c.startswith(auth.CSRF_COOKIE))
        assert "HttpOnly" in session_cookie and "SameSite=strict" in session_cookie
        assert "SameSite=strict" in csrf_cookie
        # the cookie now stands in for the token
        assert client.get("/guarded").json() == {"seen": True}


def test_session_cookie_is_not_the_token(app):
    with TestClient(app) as client:
        client.get(f"/guarded?token={TOKEN}")
        assert client.cookies[auth.SESSION_COOKIE] != TOKEN


def test_post_without_csrf_header_is_refused(app):
    with TestClient(app) as client:
        client.get(f"/guarded?token={TOKEN}")
        assert client.post("/guarded").status_code == 403


def test_post_with_a_wrong_csrf_header_is_refused(app):
    with TestClient(app) as client:
        client.get(f"/guarded?token={TOKEN}")
        response = client.post("/guarded", headers={auth.CSRF_HEADER: "nope"})
        assert response.status_code == 403


def test_post_with_the_matching_csrf_header_is_allowed(app):
    with TestClient(app) as client:
        client.get(f"/guarded?token={TOKEN}")
        csrf = client.cookies[auth.CSRF_COOKIE]
        response = client.post("/guarded", headers={auth.CSRF_HEADER: csrf})
        assert response.status_code == 200 and response.json() == {"posted": True}


def test_unauthorized_html_explains_how_to_get_in(app):
    with TestClient(app) as client:
        response = client.get("/guarded", headers={"Accept": "text/html"})
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("text/html")
        assert "<h1>Not signed in</h1>" in response.text  # HTML branch marker
        assert "scieflow serve" in response.text
        assert TOKEN not in response.text          # never echo the secret
