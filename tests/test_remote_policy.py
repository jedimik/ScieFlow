from pathlib import Path

import pytest

from remote import policy

CFG = """\
remotes:
  meta:
    host: skirit.metacentrum.cz
    user: testuser
    auth: kerberos
    scheduler: pbs
    allowed_dirs:
      - /storage/brno2/home/testuser/projx
    allowed_ops: [check, git-pull, qsub, qstat, logs, fetch]
    limits:
      max_walltime: "24:00:00"
      max_cpus: 16
      max_mem_gb: 64
      max_gpus: 1
      queues: [default]
      max_concurrent_jobs: 4
      max_fix_attempts: 3
"""


@pytest.fixture
def root(tmp_path):
    root_dir = tmp_path / "root"
    root_dir.mkdir()
    (root_dir / "config").mkdir()
    (root_dir / "config" / "remotes.yml").write_text(CFG)
    return root_dir


def test_load_remote(root):
    r = policy.load_remote(root, "meta")
    assert r.host == "skirit.metacentrum.cz"
    assert r.limits["max_fix_attempts"] == 3


def test_load_missing_file_and_name(tmp_path, root):
    with pytest.raises(policy.PolicyError, match="remotes.example.yml"):
        policy.load_remote(tmp_path, "meta")       # no config/ dir
    with pytest.raises(policy.PolicyError, match="'nope' not defined"):
        policy.load_remote(root, "nope")


def test_check_op(root):
    r = policy.load_remote(root, "meta")
    policy.check_op(r, "qsub")
    with pytest.raises(policy.PolicyError, match="'rm-rf' not in allowed_ops"):
        policy.check_op(r, "rm-rf")


def test_check_dir_refuses_shell_metacharacters(root):
    r = policy.load_remote(root, "meta")
    base = "/storage/brno2/home/testuser/projx"
    for bad in (base + "; rm -rf ~", base + "/$(evil)", base + "/a b",
                base + "/x`id`"):
        with pytest.raises(policy.PolicyError, match="unsafe characters"):
            policy.check_dir(r, bad)


def test_check_token_and_script():
    assert policy.check_token("101.meta-pbs", "job id") == "101.meta-pbs"
    with pytest.raises(policy.PolicyError, match="job id"):
        policy.check_token("1;rm", "job id")
    assert policy.check_script("run.sh") == "run.sh"
    assert policy.check_script("scripts/run.sh") == "scripts/run.sh"
    for bad in ("run.sh; evil", "../escape.sh", "-rf", "a b.sh",
                "/storage/other/x.sh", "a/../b.sh"):
        with pytest.raises(policy.PolicyError):
            policy.check_script(bad)


def test_check_dir_normalizes_and_refuses_escape(root):
    r = policy.load_remote(root, "meta")
    base = "/storage/brno2/home/testuser/projx"
    assert policy.check_dir(r, base) == base
    assert policy.check_dir(r, base + "/sub/") == base + "/sub"
    with pytest.raises(policy.PolicyError, match="outside allowed_dirs"):
        policy.check_dir(r, base + "/../other")
    with pytest.raises(policy.PolicyError, match="outside allowed_dirs"):
        policy.check_dir(r, "/storage/brno2/home/testuser/projx-evil")
    with pytest.raises(policy.PolicyError, match="absolute"):
        policy.check_dir(r, "projx")


def test_clamp_resources(root):
    r = policy.load_remote(root, "meta")
    res, warns = policy.clamp_resources(r, "48:00:00", 32, 128, 2)
    assert res == {"walltime": "24:00:00", "cpus": 16, "mem_gb": 64, "gpus": 1}
    assert len(warns) == 4
    res, warns = policy.clamp_resources(r, "01:00:00", 4, 8, 0)
    assert res["gpus"] == 0 and warns == []


def test_check_queue(root):
    r = policy.load_remote(root, "meta")
    policy.check_queue(r, "default")
    with pytest.raises(policy.PolicyError, match="queue 'gpu_long'"):
        policy.check_queue(r, "gpu_long")


def test_walltime_seconds():
    assert policy.walltime_seconds("01:30:10") == 5410
    with pytest.raises(policy.PolicyError, match="HH:MM:SS"):
        policy.walltime_seconds("90m")
