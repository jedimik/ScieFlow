"""M-C1 t2 (Part A 5.5): the snapshot store. Synthetic data; no network."""

from __future__ import annotations

import json

import pytest

from scieflow._vendor.researcher_core.model import canonical_json, sha256_hex
from scieflow.integrations.scite.cache import normalise_params, request_key
from scieflow.research.lib import providers, snapshots

PROVIDER = "search-openalex"
OP = "search"
URL = "https://api.openalex.org/works"
PARAMS = {"search": "type 2 diabetes", "per-page": 25}
KEY = request_key(PROVIDER, OP, {"url": URL, **normalise_params(PARAMS)})
BODY = {"results": [{"display_name": "Demo", "publication_year": 2023}]}
ATOM = "<feed><entry><title>Demo arXiv</title></entry></feed>"


@pytest.fixture
def run(tmp_path):
    ws = tmp_path / "proj" / "workspace" / "demo"
    ws.mkdir(parents=True)
    return ws


def _write(ws, body=BODY, kind="json", key=KEY, url=URL, params=PARAMS):
    return snapshots.write(ws, provider=PROVIDER, op=OP, key=key, url=url,
                           request_params=params, body_kind=kind, response_body=body, results=1)


def _load(ws, key=KEY, url=URL, params=PARAMS):
    return snapshots.load(ws, provider=PROVIDER, op=OP, key=key, url=url, request_params=params)


def _file(ws, key=KEY):
    return ws / "snapshots" / "literature" / PROVIDER / f"{key}.json"


def test_a_corrupt_snapshot_is_a_provider_refusal_with_exit_3():
    assert issubclass(snapshots.SnapshotCorrupt, providers.ProviderRefused)


def test_json_round_trip_and_canonical_hash(run):
    rec = _write(run)
    got = _load(run)
    assert got == rec
    assert got["response_body"] == BODY
    assert got["response_hash"] == sha256_hex(canonical_json(BODY))
    assert got["request_key"] == KEY and got["retrieved_at"].endswith("Z")
    assert set(got) == snapshots.KEYS


def test_text_round_trip_keeps_the_xml_string(run):
    _write(run, body=ATOM, kind="text")
    got = _load(run)
    assert got["response_body"] == ATOM and got["body_kind"] == "text"
    assert got["response_hash"] == sha256_hex(canonical_json(ATOM))


def test_a_missing_snapshot_loads_as_none(run):
    assert _load(run) is None


def test_tampered_body_is_refused_naming_the_path_and_never_the_body(run):
    _write(run)
    doc = json.loads(_file(run).read_text(encoding="utf-8"))
    doc["response_body"] = {"results": [{"display_name": "Tampered"}]}
    _file(run).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(snapshots.SnapshotCorrupt) as exc:
        _load(run)
    message = str(exc.value)
    assert f"snapshots/literature/{PROVIDER}/{KEY}.json" in message
    assert "Tampered" not in message and "Demo" not in message


def test_a_file_whose_recorded_key_differs_from_its_name_is_corrupt(run):
    _write(run)
    other = "f" * 64
    target = _file(run, other)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_file(run).read_bytes())
    with pytest.raises(snapshots.SnapshotCorrupt):
        _load(run, key=other)


@pytest.mark.parametrize("change", [
    {"url": "https://api.openalex.org/other"},
    {"params": {"search": "other", "per-page": 25}},
])
def test_a_request_that_differs_from_the_record_is_corrupt(run, change):
    _write(run)
    with pytest.raises(snapshots.SnapshotCorrupt):
        _load(run, **change)


def test_an_unknown_key_in_the_file_is_corrupt(run):
    _write(run)
    doc = json.loads(_file(run).read_text(encoding="utf-8"))
    doc["extra"] = 1
    _file(run).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(snapshots.SnapshotCorrupt):
        _load(run)


def test_a_symbolic_link_file_is_corrupt(run, tmp_path):
    _write(run)
    real = tmp_path / "copy.json"
    real.write_bytes(_file(run).read_bytes())
    _file(run).unlink()
    _file(run).symlink_to(real)
    with pytest.raises(snapshots.SnapshotCorrupt, match="symbolic link"):
        _load(run)


def test_a_symbolic_link_provider_folder_is_corrupt(run, tmp_path):
    _write(run)
    folder = run / "snapshots" / "literature" / PROVIDER
    moved = tmp_path / "moved"
    folder.rename(moved)
    folder.symlink_to(moved, target_is_directory=True)
    with pytest.raises(snapshots.SnapshotCorrupt):
        _load(run)


def test_a_symbolic_link_snapshots_folder_is_corrupt(run, tmp_path):
    _write(run)
    moved = tmp_path / "moved-snapshots"
    (run / "snapshots").rename(moved)
    (run / "snapshots").symlink_to(moved, target_is_directory=True)
    with pytest.raises(snapshots.SnapshotCorrupt):
        _load(run)


def test_an_interrupted_write_leaves_no_partial_file(run, monkeypatch):
    def boom(src, dst):
        raise OSError("simulated failure before the rename")

    monkeypatch.setattr(snapshots.os, "replace", boom)
    with pytest.raises(OSError, match="simulated"):
        _write(run)
    assert not _file(run).exists()
    folder = run / "snapshots" / "literature" / PROVIDER
    assert not list(folder.glob("*"))
