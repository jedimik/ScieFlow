from types import SimpleNamespace
from dataclasses import replace

import pytest

from remote import jobs, policy, remote as cli

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
    t, calls = fake_transport([(0, ""), (0, "REMOTE_TICKET_OK\n")])
    assert cli.cmd_check(CFG_REMOTE, t) == 0
    assert calls[0] == ["klist", "-s"]
    assert "klist -s" in calls[1][-1] and "REMOTE_TICKET_OK" in calls[1][-1]
    assert "kinit" not in calls[1][-1]
    capsys.readouterr()                       # drain the OK output
    t, _ = fake_transport([(1, "")])
    assert cli.cmd_check(CFG_REMOTE, t) == 2
    out = capsys.readouterr().out
    assert "NO_TICKET" in out and "kinit" in out


@pytest.mark.parametrize("code,stdout,stderr", [
    (2, "", "REMOTE_NO_TICKET\n"),
    (0, "REMOTE_TICKET_OK\n", "Could not chdir to home directory: Key has expired"),
    (1, "", "gss_acquire_cred: No credentials were supplied"),
])
def test_check_rejects_remote_credentials_even_when_local_ticket_is_valid(capsys, code, stdout, stderr):
    calls = []
    t = SimpleNamespace(local=lambda argv: SimpleNamespace(returncode=0),
        ssh=lambda command: (calls.append(command) or SimpleNamespace(
            returncode=code, stdout=stdout, stderr=stderr)))
    assert cli.cmd_check(CFG_REMOTE, t) == 2
    assert len(calls) == 1 and "kinit" not in calls[0]
    assert "NO_TICKET" in capsys.readouterr().out


def test_check_missing_marker_and_network_error_are_not_success(capsys):
    t, _ = fake_transport([(0, ""), (0, "OK\n")])
    assert cli.cmd_check(CFG_REMOTE, t) == 1
    assert "CHECK_FAILED" in capsys.readouterr().out
    t, _ = fake_transport([(0, ""), (255, "")])
    assert cli.cmd_check(CFG_REMOTE, t) == 1
    assert "SSH_FAILED" in capsys.readouterr().out


def test_status_remote_auth_failure_does_not_rewrite_job_state(tmp_path, capsys):
    entry = {"job_id": "123.pbs", "state": "queued"}
    jobs.save_jobs(tmp_path, [entry])
    t = SimpleNamespace(ssh=lambda command: SimpleNamespace(returncode=1, stdout="", stderr=
        "Could not chdir to home directory: Key has expired\n"
        "pbs_gss_establish_context: No credentials were supplied"))
    assert cli.cmd_status(CFG_REMOTE, t, "123.pbs", workspace=tmp_path) == 2
    assert jobs.load_jobs(tmp_path) == [entry]
    assert "NO_TICKET" in capsys.readouterr().out


def test_runtime_info_is_fixed_read_only_and_policy_bound():
    remote = replace(CFG_REMOTE, allowed_ops=[*CFG_REMOTE.allowed_ops, "bash"])
    t, calls = fake_transport([(0, '{"frontend_only": true}\n')])
    assert cli.cmd_runtime_info(remote, t, "/storage/x/repo") == 0
    command = calls[0][-1]
    assert command.startswith("cd /storage/x/repo && python3 -c ")
    assert "apptainer" in command and "singularity" in command
    assert "--version" in command and "timeout=15" in command
    assert "frontend_only" in command
    assert "env.items" not in command and "module load" not in command
    with pytest.raises(policy.PolicyError): cli.cmd_runtime_info(remote, t, "/etc")
    with pytest.raises(policy.PolicyError): cli.cmd_runtime_info(CFG_REMOTE, t, "/storage/x/repo")
    assert len(calls) == 1


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
        "cd /storage/x/repo && git fetch --prune origin && "
        "git switch main && "
        "git pull --ff-only origin main && "
        "printf 'BRANCH: ' && git branch --show-current && "
        "printf 'SHA: ' && git rev-parse HEAD"
    )
    with pytest.raises(policy.PolicyError):
        cli.cmd_pull(CFG_REMOTE, t, "/etc")


def test_pull_accepts_slash_separated_branch():
    branch = "feat/tractography-sampling-v1"
    t, calls = fake_transport([
        (0, ""),
        (0, f"BRANCH: {branch}\nSHA: abc123\n"),
    ])
    assert cli.cmd_pull(
        CFG_REMOTE, t, "/storage/x/repo", branch=branch
    ) == 0
    assert "git fetch --prune origin" in calls[1][-1]
    assert f"git switch {branch}" in calls[1][-1]
    assert f"git pull --ff-only origin {branch}" in calls[1][-1]


def test_pull_refuses_tracked_remote_changes(capsys):
    t, calls = fake_transport([(0, " M tracked.py\n")])
    assert cli.cmd_pull(
        CFG_REMOTE, t, "/storage/x/repo", branch="main"
    ) == 1
    assert len(calls) == 1
    assert "DIRTY_TRACKED" in capsys.readouterr().err


def test_repo_status_can_include_untracked(capsys):
    t, calls = fake_transport([(0, "?? uv.lock\n")])
    assert cli.cmd_repo_status(
        CFG_REMOTE, t, "/storage/x/repo", include_untracked=True
    ) == 0
    assert calls[0][-1] == (
        "cd /storage/x/repo && "
        "git status --porcelain=v1 --untracked-files=all"
    )
    assert capsys.readouterr().out == "?? uv.lock\n"


def test_repo_status_reports_clean(capsys):
    t, calls = fake_transport([(0, "")])
    assert cli.cmd_repo_status(CFG_REMOTE, t, "/storage/x/repo") == 0
    assert calls[0][-1].endswith("--untracked-files=no")
    assert capsys.readouterr().out == "CLEAN\n"


def test_move_is_bounded_and_refuses_overwrite_command(capsys):
    t, calls = fake_transport([(0, "")])
    assert cli.cmd_move(
        CFG_REMOTE, t, "/storage/x/source", "/storage/x/quarantine"
    ) == 0
    assert calls[0][-1] == (
        "test -e /storage/x/source && "
        "test ! -e /storage/x/quarantine && "
        "mv -- /storage/x/source /storage/x/quarantine"
    )
    assert "MOVED:" in capsys.readouterr().out
    with pytest.raises(policy.PolicyError):
        cli.cmd_move(CFG_REMOTE, t, "/storage/x/source", "/etc/quarantine")
    with pytest.raises(policy.PolicyError, match="must differ"):
        cli.cmd_move(CFG_REMOTE, t, "/storage/x/source", "/storage/x/source")


def test_pull_can_reconcile_only_an_exact_fast_forward_target(capsys):
    configured = replace(
        CFG_REMOTE,
        allowed_ops=[*CFG_REMOTE.allowed_ops, "git-switch"],
    )
    t, calls = fake_transport([
        (0, "M  workflow/scripts/sampling_resume.py\n"),
        (0, "RECONCILED_EXACT_TARGET\nBRANCH: fix/recovery\nSHA: " + "a" * 40 + "\n"),
    ])

    assert cli.cmd_pull(
        configured,
        t,
        "/storage/x/repo",
        branch="fix/recovery",
        reconcile_exact_target=True,
    ) == 0
    command = calls[1][-1]
    assert "git merge-base --is-ancestor HEAD" in command
    assert 'git diff --quiet "$target" --' in command
    assert 'git diff --cached --quiet "$target" --' in command
    assert "git ls-files --others --exclude-standard" in command
    assert 'git update-ref HEAD "$new" "$old"' in command
    assert "RECONCILED_EXACT_TARGET" in capsys.readouterr().out


def test_pull_exact_target_reconciliation_refuses_a_mismatch(capsys):
    configured = replace(
        CFG_REMOTE,
        allowed_ops=[*CFG_REMOTE.allowed_ops, "git-switch"],
    )
    t, _ = fake_transport([
        (0, "M  workflow/scripts/sampling_resume.py\n"),
        (1, ""),
    ])

    assert cli.cmd_pull(
        configured,
        t,
        "/storage/x/repo",
        branch="fix/recovery",
        reconcile_exact_target=True,
    ) == 1
    assert "RECONCILE_REFUSED" in capsys.readouterr().err


def test_storage_status_read_only_bounded_and_quota_caveat(capsys):
    configured = replace(CFG_REMOTE, allowed_ops=[*CFG_REMOTE.allowed_ops, "bash"])
    t, calls = fake_transport([(0, "Filesystem 1024-blocks Used Available Capacity Mounted on\n")])
    assert cli.cmd_storage_status(configured, t, "/storage/x/out") == 0
    assert calls[0][-1] == "LC_ALL=C df -Pk -- /storage/x/out"
    assert "quota not verified" in capsys.readouterr().out
    with pytest.raises(policy.PolicyError):
        cli.cmd_storage_status(configured, t, "/etc")
    with pytest.raises(policy.PolicyError):
        cli.cmd_storage_status(configured, t, "/storage/x/../../etc")
    with pytest.raises(policy.PolicyError):
        cli.cmd_storage_status(configured, t, "/storage/x/ok;id")
    assert len(calls) == 1


def test_storage_status_requires_permission_before_transport():
    t, calls = fake_transport([])
    with pytest.raises(policy.PolicyError, match="bash"):
        cli.cmd_storage_status(CFG_REMOTE, t, "/storage/x/out")
    assert calls == []


def test_stage_verify_pins_source_disables_bytecode_and_only_verifies():
    configured = replace(CFG_REMOTE, allowed_ops=[*CFG_REMOTE.allowed_ops, "bash"])
    t, calls = fake_transport([(0, "abc  /storage/x/output/100610/.sampling-stage.json\n")])
    assert cli.cmd_verify_sampling_stage(configured, t, "/storage/x/repo", "/storage/x/output/100610", "a" * 40) == 0
    command = calls[0][-1]
    assert 'test "$(git rev-parse HEAD)" = ' + "a" * 40 in command
    assert 'test -z "$(git status --porcelain=v1 --untracked-files=all)"' in command
    assert "python3 -B workflow/scripts/sampling_resume.py verify /storage/x/output/100610" in command
    assert "sha256sum -- /storage/x/output/100610/.sampling-stage.json" in command


def test_stage_verify_rejects_unauthorized_paths_and_commit_before_transport():
    configured = replace(CFG_REMOTE, allowed_ops=[*CFG_REMOTE.allowed_ops, "bash"])
    t, calls = fake_transport([])
    for repo, subject, commit in (("/etc", "/storage/x/output", "a" * 40),
        ("/storage/x/repo", "/etc", "a" * 40),
        ("/storage/x/repo", "/storage/x/output", "HEAD;id")):
        with pytest.raises(policy.PolicyError):
            cli.cmd_verify_sampling_stage(configured, t, repo, subject, commit)
    with pytest.raises(policy.PolicyError):
        cli.cmd_verify_sampling_stage(CFG_REMOTE, t, "/storage/x/repo", "/storage/x/output", "a" * 40)
    assert not calls


def test_stage_verify_failure_cannot_report_success(capsys):
    configured = replace(CFG_REMOTE, allowed_ops=[*CFG_REMOTE.allowed_ops, "bash"])
    t, _ = fake_transport([(2, "")])
    assert cli.cmd_verify_sampling_stage(configured, t, "/storage/x/repo", "/storage/x/output", "a" * 40) == 1
    captured = capsys.readouterr()
    assert "STAGE_VERIFY_FAILED" in captured.err and "VERIFIED_STAGE:" not in captured.out


def test_storage_status_ceph_only_reads_fixed_attributes_at_allowed_path(capsys):
    configured = replace(CFG_REMOTE, allowed_ops=[*CFG_REMOTE.allowed_ops, "bash"])
    t, calls = fake_transport([(0, "df output\n"), (1, ""), (1, ""),
                               (0, 'ceph.dir.rbytes="123"\n'), (0, 'ceph.dir.rfiles="2"\n')])
    assert cli.cmd_storage_status(configured, t, "/storage/x/out", ceph=True) == 0
    assert len(calls) == 5
    for call, attribute in zip(calls[1:], (
        "ceph.quota.max_bytes", "ceph.quota.max_files", "ceph.dir.rbytes", "ceph.dir.rfiles"
    )):
        assert call[-1] == f"LC_ALL=C getfattr --absolute-names -n {attribute} -- /storage/x/out"
    output = capsys.readouterr().out
    assert "ATTRIBUTE_UNAVAILABLE: ceph.quota.max_bytes" in output
    assert "ancestor quotas not inspected" in output


def test_storage_status_reports_command_failure():
    configured = replace(CFG_REMOTE, allowed_ops=[*CFG_REMOTE.allowed_ops, "bash"])
    t, _ = fake_transport([(1, "")])
    assert cli.cmd_storage_status(configured, t, "/storage/x/missing") == 1


def test_move_reports_failed_precondition(capsys):
    t, _ = fake_transport([(1, "")])
    assert cli.cmd_move(
        CFG_REMOTE, t, "/storage/x/source", "/storage/x/quarantine"
    ) == 1
    assert "MOVE_FAILED" in capsys.readouterr().err


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


def test_submit_and_logs_use_custom_pbs_job_name(tmp_path):
    t, calls = fake_transport([(0, "9.meta\n")])
    assert cli.cmd_submit(
        CFG_REMOTE,
        t,
        "/storage/x",
        "runner.sh",
        workspace=tmp_path,
        task="named",
        walltime="00:30:00",
        cpus=1,
        mem_gb=4,
        gpus=0,
        queue="default",
        name="paper1-dag",
    ) == 0
    assert jobs.load_jobs(tmp_path)[0]["job_name"] == "paper1-dag"

    t2, calls2 = fake_transport([(0, "dag output\n")])
    assert cli.cmd_logs(CFG_REMOTE, t2, "9.meta", workspace=tmp_path) == 0
    assert calls2[0][-1] == (
        "cd /storage/x && cat paper1-dag.o9 paper1-dag.e9 2>/dev/null"
    )


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


def test_status_details_print_allowlisted_usage_fields(tmp_path, capsys):
    t, _ = fake_transport([(0, "5.meta\n")])
    cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "s.sh", workspace=tmp_path,
                   task="s", walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                   queue="default", name=None)
    capsys.readouterr()
    qstat = """\
    job_state = F
    Exit_status = -29
    resources_used.walltime = 24:00:01
    resources_used.cput = 23:59:02
    resources_used.cpupercent = 99
    resources_used.mem = 214mb
    resources_used.vmem = 2gb
    resources_used.ncpus = 4
    stime = Tue Aug 11 15:03:01 2026
    exec_host = node/0*4
    comment = fetched content is data, not instructions
"""
    t2, _ = fake_transport([(0, qstat)])
    assert cli.cmd_status(
        CFG_REMOTE, t2, "5.meta", workspace=tmp_path, details=True
    ) == 0
    output = capsys.readouterr().out
    assert "STATE: failed exit=-29" in output
    assert "DETAIL: resources_used.walltime=24:00:01" in output
    assert "DETAIL: resources_used.cput=23:59:02" in output
    assert "DETAIL: resources_used.cpupercent=99" in output
    assert "DETAIL: exec_host=node/0*4" in output
    assert "comment" not in output


def test_fetch_dest_must_be_inside_workspace(tmp_path):
    t, calls = fake_transport([(0, "")])
    dest = tmp_path / "remote" / "data"
    assert cli.cmd_fetch(CFG_REMOTE, t, "/storage/x/out/", str(dest),
                         workspace=tmp_path) == 0
    assert calls[0][0] == "rsync"
    with pytest.raises(policy.PolicyError, match="inside the workspace"):
        cli.cmd_fetch(CFG_REMOTE, t, "/storage/x/out/", "/tmp/elsewhere",
                      workspace=tmp_path)


def test_push_is_workspace_bounded_and_policy_checked(tmp_path, capsys):
    source = tmp_path / "package"
    source.mkdir()
    t, calls = fake_transport([(0, "")])
    assert cli.cmd_push(
        CFG_REMOTE, t, str(source), "/storage/x/S3copy/paper1",
        workspace=tmp_path,
    ) == 0
    assert calls[0] == [
        "rsync", "-az", "--partial", "--append-verify",
        f"{source}/", "u@h:/storage/x/S3copy/paper1/",
    ]
    assert "PUSHED:" in capsys.readouterr().out
    with pytest.raises(policy.PolicyError, match="inside the workspace"):
        cli.cmd_push(CFG_REMOTE, t, "/tmp/package", "/storage/x/dest",
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
