"""Download an open-access source PDF into the run's workspace.

Deny-by-default: the URL's host must be listed in the profile's source_hosts,
and so must the host of whatever the request redirects to. Size is enforced
while streaming, not after. The opener is injectable so tests never leave the
machine.
"""

import hashlib
import urllib.request
from pathlib import Path

from nblm import policy

CHUNK = 64 * 1024
TIMEOUT = 60
USER_AGENT = "ScieFlow-claim-check/0.1 (+https://github.com/jedimik/ScieFlow)"
PDF_TYPES = {"application/pdf", "application/octet-stream", "binary/octet-stream"}


class FetchError(Exception):
    """The download failed or returned something unusable (exit 1)."""


def _default_opener(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def download(profile: policy.Profile, url: str, dest: Path, opener=None) -> dict:
    """Fetch `url` to `dest`. Returns {path, sha256, bytes, content_type}."""
    policy.check_source_url(profile, url)
    open_url = opener or _default_opener
    cap = profile.limits["max_source_mb"] * 1024 * 1024

    try:
        response = open_url(url)
    except Exception as exc:
        raise FetchError(f"could not fetch {url}: {exc}") from exc

    with response:
        final = getattr(response, "geturl", lambda: url)()
        # A redirect must land somewhere the user also allowed.
        policy.check_source_url(profile, final)
        content_type = _content_type(response)
        if content_type and content_type not in PDF_TYPES:
            raise FetchError(
                f"{final} returned '{content_type}', not a PDF — no source stored"
            )
        digest = hashlib.sha256()
        size = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".part")
        try:
            with partial.open("wb") as handle:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > cap:
                        raise policy.PolicyError(
                            f"{final} exceeds max_source_mb "
                            f"({profile.limits['max_source_mb']} MB)"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise FetchError(f"{final} returned an empty body")
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)

    return {
        "path": str(dest),
        "sha256": digest.hexdigest(),
        "bytes": size,
        "content_type": content_type,
    }


def _content_type(response) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    raw = getter("Content-Type", "") if getter else ""
    return (raw or "").split(";")[0].strip().lower()


def destination(workspace: Path, doi: str) -> Path:
    """Where a source for this DOI lives — inside the workspace, always."""
    path = workspace / "sources" / f"{policy.doi_slug(doi)}.pdf"
    policy.check_inside_workspace(workspace, path.parent, "sources directory")
    return path


# --- source index ------------------------------------------------------
# A DOI slug is lossy, so the filename can never be trusted to name the DOI.
# sources/index.yml is the authority on which file is which source.

def index_path(workspace: Path) -> Path:
    return workspace / "sources" / "index.yml"


def load_index(workspace: Path) -> dict:
    path = index_path(workspace)
    if not path.exists():
        return {}
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("sources") or {}


def record_index(workspace: Path, doi: str, path: Path, sha256: str,
                 url: str | None = None) -> dict:
    import yaml
    policy.check_doi(doi)
    policy.check_inside_workspace(workspace, path, "source file")
    rows = load_index(workspace)
    rows[doi] = {"file": str(path.relative_to(workspace)), "sha256": sha256,
                 "url": url}
    target = index_path(workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump({"sources": rows}, sort_keys=True))
    return rows
