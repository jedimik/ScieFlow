"""Writing and opening a bundle.

The archive itself is `scripts/sflib/archive.py`: it already does atomic
partial-then-rename packing, CRC verification, a disk-space preflight and —
the part that matters most here — a zip-slip guard on extraction.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scieflow.core import config as config_mod

from .config import ChatsConfig
from .model import Artifact, CopySpec, Selection
from .stores.base import home

MANIFEST = "manifest.yml"
SCHEMA_VERSION = 1


class BundleError(Exception):
    """A bundle could not be written or opened."""


def _archive():
    """`scripts/sflib` is part of the repo but not of the installed package."""
    try:
        from sflib import archive
    except ImportError:
        scripts = config_mod.repo_root() / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from sflib import archive
    return archive


def bundle_name(host: str | None = None, when: datetime | None = None) -> str:
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M")
    return f"scieflow-chats-{host or socket.gethostname()}-{stamp}.zip"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# -- staging ------------------------------------------------------------
def stage(specs: list[CopySpec], stage_dir: Path) -> dict[str, str]:
    """Materialise every member under `stage_dir`; return member -> sha256."""
    digests: dict[str, str] = {}
    for spec in specs:
        target = stage_dir / spec.member
        target.parent.mkdir(parents=True, exist_ok=True)
        if spec.data is not None:
            target.write_bytes(spec.data)
        else:
            try:
                # follow_symlinks: skills are symlinks into ~/.agents; the
                # archiver refuses links, so they are dereferenced here.
                shutil.copy2(spec.source, target, follow_symlinks=True)
            except OSError as e:
                raise BundleError(f"cannot read {spec.source}: {e}") from e
        digests[spec.member] = sha256(target)
    return digests


def stage_artifacts(artifacts: list[Artifact], stage_dir: Path) -> list[str]:
    """Copy skill directories in; plugins are recorded as references only."""
    members: list[str] = []
    for artifact in artifacts:
        if artifact.kind != "skill" or artifact.source is None:
            continue
        dest = stage_dir / "skills" / artifact.name.replace(":", "__")
        if dest.exists():
            continue
        try:
            shutil.copytree(artifact.source, dest, symlinks=False, dirs_exist_ok=True)
        except OSError as e:
            raise BundleError(f"cannot copy skill {artifact.name}: {e}") from e
        members.append(str(dest.relative_to(stage_dir)))
    locks = [
        {
            "name": a.name,
            "version": a.version,
            "marketplace": a.marketplace,
            "used_by": a.used_by,
        }
        for a in artifacts
        if a.kind == "plugin"
    ]
    if locks:
        path = stage_dir / "plugins" / "plugins.lock.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"plugins": locks}, sort_keys=False))
        members.append("plugins/plugins.lock.yml")
    return members


# -- manifest -----------------------------------------------------------
def build_manifest(
    config: ChatsConfig,
    selection: Selection,
    digests: dict[str, str],
    secrets: dict[str, dict[str, int]],
    excluded: list[str],
) -> dict:
    source_home = str(home())
    return {
        "schema_version": SCHEMA_VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "host": socket.gethostname(),
            "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
            "home": source_home,
            "platform": platform.platform(),
        },
        "tools": sorted({c.tool for c in selection.chats}),
        "path_map": sorted(
            {c.project_path for c in selection.chats if c.project_path}
        ),
        "chats": [
            {
                "key": c.key,
                "tool": c.tool,
                "chat_id": c.chat_id,
                "project": c.project_path,
                "title": c.title,
                "updated": c.updated.isoformat() if c.updated else None,
                "messages": c.message_count,
                "bytes": c.size_bytes,
            }
            for c in selection.chats
        ],
        "artifacts": [
            {
                "kind": a.kind,
                "name": a.name,
                "version": a.version,
                "marketplace": a.marketplace,
                "confidence": a.confidence,
                "bundled": a.kind == "skill" and a.source is not None,
                "used_by": a.used_by,
            }
            for a in selection.artifacts
        ],
        "members": dict(sorted(digests.items())),
        "exclusions": excluded,
        # Counts only — a secret's value never enters the manifest.
        "secret_scan": {key: dict(sorted(v.items())) for key, v in sorted(secrets.items())},
    }


def validate_manifest(manifest: dict) -> None:
    schema_path = Path(__file__).parent / "schemas" / "bundle.schema.json"
    try:
        import jsonschema
    except ImportError:
        return
    try:
        jsonschema.validate(manifest, json.loads(schema_path.read_text()))
    except jsonschema.ValidationError as e:
        raise BundleError(f"manifest failed validation: {e.message}") from e


# -- write / read -------------------------------------------------------
def write(stage_dir: Path, out_path: Path) -> Path:
    """Pack a staged directory into a deflated zip, then verify it."""
    archive = _archive()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # A bundle is already exactly what the user picked: no skip rules
        # (Gemini keeps chats under tmp/, which runs treat as scratch).
        archive.ensure_space(stage_dir, out_path.parent, skip_rebuildable=False)
        archive.build_zip(
            stage_dir, out_path, compression=zipfile.ZIP_DEFLATED, skip_rebuildable=False
        )
        archive.verify_zip(out_path)
    except archive.ArchiveError as e:
        raise BundleError(str(e)) from e
    os.chmod(out_path, 0o600)
    return out_path


def open_bundle(path: Path) -> tuple[Path, dict]:
    """Decrypt (if needed) and extract to a 0700 temp dir. Caller cleans up."""
    from . import crypto

    work = Path(tempfile.mkdtemp(prefix="scieflow-chats-"))
    os.chmod(work, 0o700)
    zip_path = path
    try:
        if crypto.is_encrypted(path):
            zip_path = work / "bundle.zip"
            crypto.decrypt(path, zip_path)
        archive = _archive()
        extracted = work / "bundle"
        archive.extract_zip(zip_path, extracted, force=True)
        manifest_path = extracted / MANIFEST
        if not manifest_path.exists():
            raise BundleError(f"{path.name}: no {MANIFEST} inside; not a chats bundle")
        manifest = yaml.safe_load(manifest_path.read_text())
        if not isinstance(manifest, dict):
            raise BundleError(f"{path.name}: unreadable manifest")
        return extracted, manifest
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
