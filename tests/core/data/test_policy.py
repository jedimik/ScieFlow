"""Project data policy (M-B Part A 5.2): user-owned, fail-closed, never written by ScieFlow."""

from pathlib import Path
import sys

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
    ("version: 1.0\n", "version"),
    ('version: 1\nsafe_endpoints:\n  - url: "https://llm.example.org/v1 --evil"\n    reason: x\n', "safe_endpoints"),
    ('version: 1\nsafe_endpoints:\n  - url: "https://user:pw@llm.example.org/v1"\n    reason: x\n', "safe_endpoints"),
    ('version: 1\nsafe_endpoints:\n  - url: "https://@llm.example.org/v1"\n    reason: x\n', "safe_endpoints"),
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


def test_a_credential_in_an_endpoint_is_refused_and_never_echoed(tmp_path):
    put(tmp_path, 'version: 1\nsafe_endpoints:\n  - url: "https://user:SECRET-PLANT@llm.example.org/v1"\n'
                  "    reason: x\n")
    with pytest.raises(PolicyInvalid) as exc:
        policy.load(tmp_path)
    assert "safe_endpoints" in str(exc.value)
    assert "SECRET-PLANT" not in str(exc.value)


def test_a_valid_policy_loads_without_the_research_extra(tmp_path, monkeypatch):
    # F1: on a core-only install `requests` is missing; policy.load must not import the research module.
    monkeypatch.setitem(sys.modules, "requests", None)
    monkeypatch.setitem(sys.modules, "scieflow.research.lib.http", None)
    monkeypatch.setitem(sys.modules, "scieflow.research.lib.providers", None)
    put(tmp_path, "version: 1\nl2_providers:\n  - name: search-europepmc\n    reason: metadata only\n")
    assert policy.load(tmp_path).l2_providers == ("search-europepmc",)


def test_the_static_provider_names_match_the_catalogue():
    from scieflow.research.lib.providers import CATALOGUE

    assert set(policy.KNOWN_PROVIDERS) == set(CATALOGUE) | set(policy.SCITE_PROVIDERS)


@pytest.mark.parametrize("text", [
    "version: 1\nsurprise: 2026-13-45\n",                        # ValueError (impossible date)
    "version: 1\nsurprise: " + "[" * 5000 + "]" * 5000 + "\n",  # RecursionError
])
def test_a_yaml_load_crash_is_refused_as_an_invalid_policy(tmp_path, text):
    # M-B review F1: yaml.safe_load raised ValueError / RecursionError past the loader.
    put(tmp_path, text)
    with pytest.raises(PolicyInvalid):
        policy.load(tmp_path)


def test_an_unhashable_l2_provider_name_is_refused_not_a_type_error(tmp_path):
    # M-B review F1: a list as the provider name reached a set membership test (TypeError).
    put(tmp_path, "version: 1\nl2_providers:\n  - name: [a]\n    reason: x\n")
    with pytest.raises(PolicyInvalid) as exc:
        policy.load(tmp_path)
    assert "l2_providers" in str(exc.value)


def test_a_dangling_policy_symlink_is_invalid_not_the_defaults(tmp_path):
    # F2: exists() is False for a dangling link; that would silently drop enforcement: refuse.
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "data-policy.yml").symlink_to(tmp_path / "missing.yml")
    with pytest.raises(PolicyInvalid):
        policy.load(tmp_path)


def test_a_symlinked_policy_file_is_invalid_even_when_its_target_is_valid(tmp_path):
    target = tmp_path / "elsewhere.yml"
    target.write_text("version: 1\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "data-policy.yml").symlink_to(target)
    with pytest.raises(PolicyInvalid):
        policy.load(tmp_path)
