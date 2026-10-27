"""M-B t4, Part A 5.7: remote push and fetch against the L2 rules (C1: decided from the paths).

The transport is faked: no ssh, no rsync, no network. Synthetic data only.
"""

from types import SimpleNamespace

import pytest
import yaml

from remote import policy, remote as cli, transport
from scieflow.core import events

CFG_REMOTE = policy.Remote(
    name="meta", host="h", user="u", auth="kerberos", scheduler="pbs",
    allowed_dirs=["/storage/x"],
    allowed_ops=["check", "git-status", "git-switch", "git-pull", "mv",
                 "qsub", "qstat", "logs", "fetch", "rsync"],
    limits={"max_walltime": "24:00:00", "max_cpus": 16, "max_mem_gb": 64,
            "max_gpus": 1, "max_scratch_gb": 100,
            "scratch_types": ["scratch_ssd"], "queues": ["default"],
            "max_concurrent_jobs": 2, "max_fix_attempts": 1},
)


def fake_transport():
    calls = []

    def runner(argv):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return transport.Transport(CFG_REMOTE, runner=runner), calls


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    ws = root / "workspace"
    (ws / "l2run" / "outputs").mkdir(parents=True)
    (ws / "l2run" / "released" / "r1").mkdir(parents=True)
    (ws / "l2run" / "released" / "r1" / "agg.csv").write_text("site,n\nA,12\n")
    (ws / "l2run" / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False))
    (ws / "l0run" / "outputs").mkdir(parents=True)
    (ws / "l0run" / "inputs").mkdir(parents=True)
    return root


def _policy(root, text):
    (root / "config" / "data-policy.yml").write_text(text)


def _listed(root, *remotes):
    _policy(root, yaml.safe_dump({"version": 1, "l2_remotes": [
        {"remote": r, "reason": "test: approved remote"} for r in remotes]}))


def _push(root, src, calls_t):
    return cli.cmd_push(CFG_REMOTE, calls_t, str(src), "/storage/x/dest",
                        workspace=root / "workspace", root=root)


def test_push_from_an_l2_run_to_an_unlisted_remote_is_refused_with_no_transport(project):
    t, calls = fake_transport()
    with pytest.raises(policy.PolicyError, match="data protection"):
        _push(project, project / "workspace" / "l2run" / "outputs", t)
    assert calls == []


def test_push_to_a_listed_remote_is_allowed(project):
    _listed(project, "meta")
    t, calls = fake_transport()
    assert _push(project, project / "workspace" / "l2run" / "outputs", t) == 0
    assert len(calls) == 1


def test_push_of_a_released_copy_is_allowed(project):
    t, calls = fake_transport()
    assert _push(project, project / "workspace" / "l2run" / "released" / "r1", t) == 0
    assert len(calls) == 1


def test_push_of_the_workspace_holding_an_l2_run_is_refused(project):
    t, calls = fake_transport()
    with pytest.raises(policy.PolicyError, match="data protection"):
        _push(project, project / "workspace", t)
    assert calls == []


def test_push_from_an_l0_run_is_unchanged(project):
    t, calls = fake_transport()
    assert _push(project, project / "workspace" / "l0run" / "outputs", t) == 0
    assert len(calls) == 1


def test_push_with_an_invalid_policy_is_refused(project):
    _policy(project, "version: 1\nenforcement: maybe\n")
    t, calls = fake_transport()
    with pytest.raises(policy.PolicyError, match="data-policy"):
        _push(project, project / "workspace" / "l0run" / "outputs", t)
    assert calls == []


def _fetch(root, dest, t):
    return cli.cmd_fetch(CFG_REMOTE, t, "/storage/x/out/", str(dest),
                         workspace=root / "workspace", root=root)


def test_fetch_from_a_listed_remote_into_an_l0_run_records_a_warning(project):
    _listed(project, "meta")
    t, calls = fake_transport()
    dest = project / "workspace" / "l0run" / "inputs" / "remote"
    assert _fetch(project, dest, t) == 0
    assert len(calls) == 1
    warned = events.read(project / "workspace" / "l0run", types=("data.warning",))
    assert [(e["data"]["rule"]) for e in warned] == ["l2-remote-fetch"]


def test_fetch_from_a_listed_remote_under_refuse_is_refused(project):
    _policy(project, yaml.safe_dump({"version": 1, "enforcement": "refuse", "l2_remotes": [
        {"remote": "meta", "reason": "test"}]}))
    t, calls = fake_transport()
    dest = project / "workspace" / "l0run" / "inputs" / "remote"
    with pytest.raises(policy.PolicyError, match="l2-remote-fetch"):
        _fetch(project, dest, t)
    assert calls == []


def test_fetch_from_an_unlisted_remote_has_no_rule(project):
    t, calls = fake_transport()
    dest = project / "workspace" / "l0run" / "inputs" / "remote"
    assert _fetch(project, dest, t) == 0
    assert events.read(project / "workspace" / "l0run", types=("data.warning",)) == []


def test_fetch_with_an_invalid_policy_is_refused(project):
    _policy(project, "version: 1\nenforcement: maybe\n")
    t, calls = fake_transport()
    with pytest.raises(policy.PolicyError, match="data-policy"):
        _fetch(project, project / "workspace" / "l0run" / "inputs" / "remote", t)
    assert calls == []
