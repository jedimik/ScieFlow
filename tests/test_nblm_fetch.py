import io
import pytest

from nblm import fetch, policy

PROFILE = policy.Profile(
    name="default",
    session_state="/nonexistent/state.json",
    notebook_prefix="scieflow-",
    allowed_ops=["check", "fetch"],
    source_hosts=["arxiv.org"],
    limits={"max_sources_per_notebook": 50, "max_source_mb": 50,
            "max_questions_per_run": 40, "max_questions_per_day": 45,
            "max_claims_per_question": 5, "reask_threshold": "partial"},
)


class FakeResponse(io.BytesIO):
    def __init__(self, body, url, content_type="application/pdf"):
        super().__init__(body)
        self._url = url
        self.headers = {"Content-Type": content_type}

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def opener_for(body=b"%PDF-1.4 body", url="https://arxiv.org/pdf/1.pdf", **kw):
    seen = []

    def opener(requested):
        seen.append(requested)
        return FakeResponse(body, url, **kw)

    return opener, seen


def test_download_writes_file_and_hashes(tmp_path):
    opener, seen = opener_for()
    dest = tmp_path / "sources" / "10.1-x.pdf"
    result = fetch.download(PROFILE, "https://arxiv.org/pdf/1.pdf", dest, opener=opener)
    assert dest.read_bytes() == b"%PDF-1.4 body"
    assert result["bytes"] == 13 and len(result["sha256"]) == 64
    assert seen == ["https://arxiv.org/pdf/1.pdf"]
    assert not list(dest.parent.glob("*.part"))


def test_download_refuses_disallowed_host_without_opening(tmp_path):
    opener, seen = opener_for()
    with pytest.raises(policy.PolicyError, match="not in source_hosts"):
        fetch.download(PROFILE, "https://evil.example/p.pdf", tmp_path / "a.pdf",
                       opener=opener)
    assert seen == []


def test_download_refuses_redirect_to_disallowed_host(tmp_path):
    opener, _ = opener_for(url="https://evil.example/p.pdf")
    with pytest.raises(policy.PolicyError, match="not in source_hosts"):
        fetch.download(PROFILE, "https://arxiv.org/pdf/1.pdf", tmp_path / "a.pdf",
                       opener=opener)


def test_download_refuses_non_pdf_content_type(tmp_path):
    opener, _ = opener_for(body=b"<html>", content_type="text/html; charset=utf-8")
    with pytest.raises(fetch.FetchError, match="not a PDF"):
        fetch.download(PROFILE, "https://arxiv.org/pdf/1.pdf", tmp_path / "a.pdf",
                       opener=opener)


def test_download_enforces_size_cap_while_streaming(tmp_path):
    big = b"x" * (51 * 1024 * 1024)
    opener, _ = opener_for(body=big)
    dest = tmp_path / "a.pdf"
    with pytest.raises(policy.PolicyError, match="max_source_mb"):
        fetch.download(PROFILE, "https://arxiv.org/pdf/1.pdf", dest, opener=opener)
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_empty_body(tmp_path):
    opener, _ = opener_for(body=b"")
    with pytest.raises(fetch.FetchError, match="empty body"):
        fetch.download(PROFILE, "https://arxiv.org/pdf/1.pdf", tmp_path / "a.pdf",
                       opener=opener)


def test_destination_is_inside_workspace(tmp_path):
    dest = fetch.destination(tmp_path, "10.1000/xyz.123")
    assert dest == tmp_path / "sources" / "10.1000-xyz.123.pdf"
