import json
import zipfile

import pytest
from click.testing import CliRunner

from scieflow.chats.cli import chats


def run(args):
    return CliRunner().invoke(chats, args)


@pytest.fixture
def written_bundle(tmp_path, source_config, source_home):
    out = tmp_path / "bundle.zip"
    result = run(["backup", "--config", str(source_config), "--all",
                  "--no-encrypt", "--yes", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    return out


def members(path):
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def test_bundle_has_every_store_and_a_manifest(written_bundle):
    names = members(written_bundle)
    assert "manifest.yml" in names
    for tool in ("claude", "codex", "agy", "gemini"):
        assert any(n.startswith(f"chats/{tool}/") for n in names), tool


def test_manifest_validates_and_lists_chats(written_bundle):
    import yaml

    from scieflow.chats.bundle import validate_manifest

    with zipfile.ZipFile(written_bundle) as zf:
        manifest = yaml.safe_load(zf.read("manifest.yml"))
    validate_manifest(manifest)
    assert len(manifest["chats"]) == 4
    assert manifest["source"]["home"].endswith("alice")
    assert set(manifest["members"]) <= set(members(written_bundle))


def test_no_credentials_in_the_bundle(written_bundle):
    joined = " ".join(members(written_bundle))
    for leak in (".credentials.json", "auth.json", "oauth_creds.json",
                 "antigravity-oauth-token"):
        assert leak not in joined
    with zipfile.ZipFile(written_bundle) as zf:
        blob = b" ".join(zf.read(n) for n in zf.namelist() if n.endswith(".json"))
    assert b"top-secret" not in blob
    assert b"sk-should-not-travel" not in blob


def test_referenced_skill_is_bundled_plugin_is_referenced(written_bundle):
    import yaml

    names = members(written_bundle)
    assert any(n.startswith("skills/demo-skill/") for n in names)
    with zipfile.ZipFile(written_bundle) as zf:
        manifest = yaml.safe_load(zf.read("manifest.yml"))
    kinds = {(a["kind"], a["name"]): a for a in manifest["artifacts"]}
    assert kinds[("skill", "demo-skill")]["bundled"] is True


def test_inspect_prints_the_manifest(written_bundle):
    result = run(["inspect", str(written_bundle)])
    assert result.exit_code == 0, result.output
    assert "chats    4" in result.output


def test_zip_slip_member_is_refused(tmp_path):
    import sys

    sys.path.insert(0, str(tmp_path))
    from scieflow.chats.bundle import _archive

    archive = _archive()
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escaped.txt", "nope")
    with pytest.raises(archive.ArchiveError, match="unsafe member path"):
        archive.extract_zip(evil, tmp_path / "out", force=True)


def test_plan_file_round_trip(tmp_path, source_config, source_home):
    plan_file = tmp_path / "plan.yml"
    result = run(["plan", "--config", str(source_config), str(plan_file)])
    assert result.exit_code == 0, result.output
    doc = plan_file.read_text()
    assert "selected: false" in doc
    plan_file.write_text(doc.replace("selected: false", "selected: true", 1))

    out = tmp_path / "from-plan.zip"
    result = run(["backup", "--config", str(source_config), "--plan", str(plan_file),
                  "--no-encrypt", "--yes", "--out", str(out)])
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(out) as zf:
        manifest = json.dumps(zf.read("manifest.yml").decode())
    assert manifest.count("key:") <= 2


def test_plaintext_bundle_requires_yes(tmp_path, source_config, source_home):
    result = run(["backup", "--config", str(source_config), "--all",
                  "--no-encrypt", "--out", str(tmp_path / "x.zip")])
    assert result.exit_code != 0
    assert "--no-encrypt also needs --yes" in result.output


def test_size_limit_refuses(tmp_path, source_home):
    from tests.chats.conftest import write_config

    home, _ = source_home
    cfg = write_config(tmp_path / "small.yml", home, tmp_path, max_bundle_gb=0.000001)
    result = run(["backup", "--config", str(cfg), "--all", "--no-encrypt",
                  "--yes", "--out", str(tmp_path / "y.zip")])
    assert result.exit_code != 0
    assert "max_bundle_gb" in result.output


def test_failed_decryption_is_a_clean_error(tmp_path, monkeypatch):
    from scieflow.chats import bundle as bundle_mod
    from scieflow.chats import crypto

    encrypted = tmp_path / "b.zip.gpg"
    encrypted.write_bytes(b"not really gpg")

    def boom(src, dest):
        raise crypto.CryptoError("gpg failed: 2")

    monkeypatch.setattr(crypto, "decrypt", boom)
    with pytest.raises(bundle_mod.BundleError, match="needs the passphrase"):
        bundle_mod.open_bundle(encrypted)
