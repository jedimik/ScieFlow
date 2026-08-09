from types import SimpleNamespace

import pytest

from remote import jobs, policy, remote as cli

CFG_REMOTE = policy.Remote(
    name="meta", host="h", user="u", auth="kerberos", scheduler="pbs",
    allowed_dirs=["/storage/x"],
    allowed_ops=["check", "git-status", "git-switch", "git-pull", "qsub",
                 "qstat", "logs", "fetch"],
    limits={"max_walltime": "24:00:00", "max_cpus": 16, "max_mem_gb": 64,
            "max_gpus": 1, "max_scratch_gb": 100,
            "scratch_types": ["scratch_ssd"], "queues": ["default"],
            "max_concurrent_jobs": 2, "max_fix_attempts": 1},
)


def fake_transport(script):
    """script: list of (returncode, stdout) consumed per call; records argv."""
    calls = []
    replies = list(script)

    def runner(argv):
        calls.append(argv)
        rc, out = replies.pop(0) if replies else (0, "")
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    from remote import transport
    return transport.Transport(CFG_REMOTE, runner=runner), calls


def test_check_ok_and_no_ticket(capsys):
    t, calls = fake_transport([(0, ""), (0, "OK\n")])
    assert cli.cmd_check(CFG_REMOTE, t) == 0
    assert calls[0] == ["klist", "-s"]
    capsys.readouterr()                       # drain the OK output
    t, _ = fake_transport([(1, "")])
    assert cli.cmd_check(CFG_REMOTE, t) == 2
    out = capsys.readouterr().out
    assert "NO_TICKET" in out and "kinit" in out


def test_pull_builds_command_and_respects_policy():
    t, calls = fake_transport([
        (0, ""),
        (0, "Already up to date.\nBRANCH: main\nSHA: abc123\n"),
    ])
    assert cli.cmd_pull(
        CFG_REMOTE, t, "/storage/x/repo", branch="main"
    ) == 0
    assert calls[0][-1] == (
        "cd /storage/x/repo && git status --porcelain --untracked-files=no"
    )
    assert calls[1][-1] == (
        "cd /storage/x/repo && git switch main && "
        "git pull --ff-only origin main && "
        "printf 'BRANCH: ' && git branch --show-current && "
        "printf 'SHA: ' && git rev-parse HEAD"
    )
    with pytest.raises(policy.PolicyError):
        cli.cmd_pull(CFG_REMOTE, t, "/etc")


def test_pull_refuses_tracked_remote_changes(capsys):
    t, calls = fake_transport([(0, " M tracked.py\n")])
    assert cli.cmd_pull(
        CFG_REMOTE, t, "/storage/x/repo", branch="main"
    ) == 1
    assert len(calls) == 1
    assert "DIRTY_TRACKED" in capsys.readouterr().err


def test_submit_clamps_records_and_enforces_ceilings(tmp_path):
    t, calls = fake_transport([(0, "101.meta-pbs\n")])
    rc = cli.cmd_submit(CFG_REMOTE, t, "/storage/x/repo", "run.sh",
                        workspace=tmp_path, task="expA",
                        walltime="48:00:00", cpus=99, mem_gb=999, gpus=0,
                        queue="default", name=None)
    assert rc == 0
    qsub = calls[0][-1]
    assert "qsub" in qsub and "walltime=24:00:00" in qsub
    assert "select=1:ncpus=16:mem=64gb" in qsub and "ngpus" not in qsub
    ledger = jobs.load_jobs(tmp_path)
    assert ledger[0]["job_id"] == "101.meta-pbs" and ledger[0]["attempt"] == 1

    # max_fix_attempts=1 → total ceiling 2 submits for the same task
    t2, _ = fake_transport([(0, "102.meta-pbs\n"), (0, "103.meta-pbs\n")])
    assert cli.cmd_submit(CFG_REMOTE, t2, "/storage/x/repo", "run.sh",
                          workspace=tmp_path, task="expA",
                          walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                          queue="default", name=None) == 0
    assert cli.cmd_submit(CFG_REMOTE, t2, "/storage/x/repo", "run.sh",
                          workspace=tmp_path, task="expA",
                          walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                          queue="default", name=None) == 4


def test_submit_concurrency_ceiling(tmp_path):
    script = [(0, f"{n}.meta\n") for n in (1, 2, 3)]
    t, _ = fake_transport(script)
    for n, task in [(1, "a"), (2, "b")]:
        assert cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "s.sh",
                              workspace=tmp_path, task=task,
                              walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                              queue="default", name=None) == 0
    assert cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "s.sh",
                          workspace=tmp_path, task="c",
                          walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                          queue="default", name=None) == 4


def test_submit_gpu_flag(tmp_path):
    t, calls = fake_transport([(0, "7.meta\n")])
    cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "s.sh", workspace=tmp_path,
                   task="g", walltime="01:00:00", cpus=1, mem_gb=1, gpus=1,
                   queue="default", name=None)
    assert "ngpus=1" in calls[0][-1]


def test_submit_scratch_flag_and_ledger(tmp_path):
    t, calls = fake_transport([(0, "8.meta\n")])
    cli.cmd_submit(
        CFG_REMOTE,
        t,
        "/storage/x",
        "s.sh",
        workspace=tmp_path,
        task="scratch",
        walltime="00:30:00",
        cpus=4,
        mem_gb=16,
        gpus=0,
        queue="default",
        name=None,
        scratch_type="scratch_ssd",
        scratch_gb=60,
    )
    assert "select=1:ncpus=4:mem=16gb:scratch_ssd=60gb" in calls[0][-1]
    resources = jobs.load_jobs(tmp_path)[0]["resources"]
    assert resources["scratch_type"] == "scratch_ssd"
    assert resources["scratch_gb"] == 60


def test_submit_environment_is_quoted_once_and_recorded(tmp_path):
    t, calls = fake_transport([(0, "9.meta\n")])
    assert cli.cmd_submit(
        CFG_REMOTE,
        t,
        "/storage/x",
        "s.sh",
        workspace=tmp_path,
        task="environment",
        walltime="00:30:00",
        cpus=1,
        mem_gb=4,
        gpus=0,
        queue="default",
        name=None,
        environment_assignments=[
            "SUBJECT=105216",
            "RESULTS_ROOT=/storage/x/results",
        ],
    ) == 0
    command = calls[0][-1]
    assert command.count(" -v ") == 1
    assert "-v SUBJECT=105216,RESULTS_ROOT=/storage/x/results" in command
    assert jobs.load_jobs(tmp_path)[0]["environment"] == {
        "SUBJECT": "105216",
        "RESULTS_ROOT": "/storage/x/results",
    }


def test_status_updates_ledger(tmp_path):
    t, calls = fake_transport([(0, "5.meta\n")])
    cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "s.sh", workspace=tmp_path,
                   task="s", walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                   queue="default", name=None)
    t2, calls2 = fake_transport(
        [(0, "    job_state = F\n    Exit_status = 1\n")])
    assert cli.cmd_status(CFG_REMOTE, t2, "5.meta", workspace=tmp_path) == 0
    assert "qstat -xf 5.meta" in calls2[0][-1]
    assert jobs.load_jobs(tmp_path)[0]["state"] == "failed"


def test_fetch_dest_must_be_inside_workspace(tmp_path):
    t, calls = fake_transport([(0, "")])
    dest = tmp_path / "remote" / "data"
    assert cli.cmd_fetch(CFG_REMOTE, t, "/storage/x/out/", str(dest),
                         workspace=tmp_path) == 0
    assert calls[0][0] == "rsync"
    with pytest.raises(policy.PolicyError, match="inside the workspace"):
        cli.cmd_fetch(CFG_REMOTE, t, "/storage/x/out/", "/tmp/elsewhere",
                      workspace=tmp_path)


def test_submit_refuses_metachar_script_before_ssh(tmp_path):
    t, calls = fake_transport([(0, "9.meta\n")])
    with pytest.raises(policy.PolicyError, match="unsafe script"):
        cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "run.sh; rm -rf ~",
                       workspace=tmp_path, task="expA", walltime="01:00:00",
                       cpus=1, mem_gb=1, gpus=0, queue="default", name=None)
    assert calls == []                         # refused before any ssh
    assert jobs.load_jobs(tmp_path) == []      # nothing recorded


def test_submit_refuses_metachar_dir_and_task(tmp_path):
    t, _ = fake_transport([(0, "9.meta\n")])
    with pytest.raises(policy.PolicyError):
        cli.cmd_submit(CFG_REMOTE, t, "/storage/x/$(evil)", "run.sh",
                       workspace=tmp_path, task="expA", walltime="01:00:00",
                       cpus=1, mem_gb=1, gpus=0, queue="default", name=None)
    with pytest.raises(policy.PolicyError, match="task name"):
        cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "run.sh",
                       workspace=tmp_path, task="a;b", walltime="01:00:00",
                       cpus=1, mem_gb=1, gpus=0, queue="default", name=None)


def test_status_refuses_metachar_job_id(tmp_path):
    t, calls = fake_transport([(0, "")])
    with pytest.raises(policy.PolicyError, match="job id"):
        cli.cmd_status(CFG_REMOTE, t, "5.meta; rm -rf ~", workspace=tmp_path)
    assert calls == []


def test_submit_empty_qsub_stdout_returns_one_no_ledger(tmp_path):
    t, _ = fake_transport([(0, "   \n")])       # rc 0 but no job id
    rc = cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "run.sh",
                        workspace=tmp_path, task="expA", walltime="01:00:00",
                        cpus=1, mem_gb=1, gpus=0, queue="default", name=None)
    assert rc == 1
    assert jobs.load_jobs(tmp_path) == []
