"""Project data policy (M-B Part A 5.2): user-owned, fail-closed, never written by ScieFlow."""

from pathlib import Path

import pytest

from scieflow.core.data import policy
from scieflow.core.data.policy import PolicyInvalid

ROOT = Path(__file__).resolve().parents[3]
SHA = "0" * 64
SAFE_EP = "safe_endpoints:\n  - url: https://llm.example.org/v1\n    reason: test endpoint\n"


def put(root, text):
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "data-policy.yml").write_text(text)


def test_absent_file_gives_the_safe_defaults(tmp_path):
    p = policy.load(tmp_path)
    assert p.present is False
    assert p.enforcement == "warn"
    assert p.min_cell == 5
    assert p.safe_agents == {}
    assert p.safe_endpoints == ()
    assert p.l2_providers == () and p.l2_remotes == () and p.project_levels == ()
    assert (p.scan_max_files, p.scan_max_bytes_per_file, p.scan_max_seconds) == (2000, 65536, 5)


def test_a_full_valid_policy_loads(tmp_path):
    put(tmp_path, "version: 1\n" + SAFE_EP
        + "safe_agents:\n  my-safe:\n    endpoint: https://llm.example.org/v1\n"
        + f"    templates_sha256: \"{SHA}\"\n    reason: local test\n"
        + "l2_remotes:\n  - remote: meta\n    reason: lab cluster\n"
        + "l2_providers:\n  - name: search-europepmc\n    reason: metadata only\n"
        + "project_levels:\n  - path: ~/datasets/rbd\n    level: L2\n    kind: tabular\n"
        + "    reason: exports\n"
        + "enforcement: refuse\nmin_cell: 5\n"
        + "scan: {max_files: 10, max_bytes_per_file: 100, max_seconds: 1}\n"
        + "ingest: {extra_drop: [notes_code]}\n")
    p = policy.load(tmp_path)
    assert p.present is True
    assert p.enforcement == "refuse"
    assert p.safe_agents["my-safe"].endpoint == "https://llm.example.org/v1"
    assert p.safe_agents["my-safe"].templates_sha256 == SHA
    assert p.l2_providers == ("search-europepmc",)
    assert p.l2_remotes == ("meta",)
    assert p.project_levels[0].level == "L2"
    assert p.scan_max_files == 10
    assert p.extra_drop == ("notes_code",)


def test_loopback_http_endpoint_is_accepted(tmp_path):
    put(tmp_path, "version: 1\nsafe_endpoints:\n  - url: http://127.0.0.1:11434/v1\n"
                  "    reason: local ollama\n")
    assert policy.load(tmp_path).safe_endpoints == ("http://127.0.0.1:11434/v1",)


@pytest.mark.parametrize("text, fragment", [
    ("version: 1\nbogus: 1\n", "bogus"),
    ("version: 2\n", "version"),
    ("version: true\n", "version"),
    ("version: 1\nenforcement: sometimes\n", "enforcement"),
    ("version: 1\nmin_cell: 0\n", "min_cell"),
    ("version: 1\nmin_cell: true\n", "min_cell"),
    ("version: 1\n" + "safe_endpoints:\n  - url: https://llm.example.org/v1\n", "reason"),
    ("version: 1\nsafe_endpoints:\n  - url: http://example.org/v1\n    reason: x\n", "safe_endpoints"),
    ("version: 1\nsafe_endpoints:\n  - url: ftp://example.org/v1\n    reason: x\n", "safe_endpoints"),
    ("version: 1\n" + SAFE_EP + "safe_agents:\n  a:\n    endpoint: https://other.example.org/v1\n"
     + f"    templates_sha256: \"{SHA}\"\n    reason: x\n", "safe_agents"),
    ("version: 1\n" + SAFE_EP + "safe_agents:\n  a:\n    endpoint: https://llm.example.org/v1\n"
     + "    templates_sha256: short\n    reason: x\n", "templates_sha256"),
    ("version: 1\nl2_providers:\n  - name: not-a-provider\n    reason: x\n", "l2_providers"),
    ("version: 1\nl2_remotes:\n  - remote: 'bad remote!'\n    reason: x\n", "l2_remotes"),
    ("version: 1\nproject_levels:\n  - path: ../escape\n    level: L2\n    kind: tabular\n"
     "    reason: x\n", "project_levels"),
    ("version: 1\nproject_levels:\n  - path: inputs\n    level: L1.5\n    kind: tabular\n"
     "    reason: x\n", "project_levels"),
    ("version: 1\nscan: {max_files: 0}\n", "scan"),
    ("version: 1\nscan: {max_seconds: true}\n", "scan"),
    ("version: 1\ningest: {extra_drop: notalist}\n", "ingest"),
])
def test_each_invalid_key_is_refused_naming_the_key(tmp_path, text, fragment):
    put(tmp_path, text)
    with pytest.raises(PolicyInvalid) as exc:
        policy.load(tmp_path)
    assert fragment in str(exc.value)


def test_refusal_names_the_key_never_the_value(tmp_path):
    put(tmp_path, "version: 1\nenforcement: SECRET-PLANT-VALUE\n")
    with pytest.raises(PolicyInvalid) as exc:
        policy.load(tmp_path)
    assert "enforcement" in str(exc.value)
    assert "SECRET-PLANT-VALUE" not in str(exc.value)


def test_the_shipped_example_loads_with_nothing_active(tmp_path):
    put(tmp_path, (ROOT / "config" / "data-policy.example.yml").read_text())
    p = policy.load(tmp_path)
    assert p.present is True
    assert p.safe_agents == {} and p.safe_endpoints == ()
    assert p.l2_remotes == () and p.l2_providers == () and p.project_levels == ()
    assert p.enforcement == "warn"


def test_the_example_file_is_shipped_and_the_real_policy_is_gitignored():
    assert (ROOT / "config" / "data-policy.example.yml").is_file()
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert "config/data-policy.yml" in lines
