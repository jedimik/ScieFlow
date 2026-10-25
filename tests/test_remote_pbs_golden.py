"""Golden characterisation of today's PBS command strings (M-E t1, commit A).

These pin the exact text that remote.py sends over SSH for PBS today, before
the scheduler seam moves it behind an adapter. The strings are the ones
recorded in reviews/m-e-plan.md section 4.2. They must stay byte-identical
through the refactor; a failure here means the PBS behaviour changed.
"""

from types import SimpleNamespace

from remote import jobs, policy, remote as cli, transport

GOLD = policy.Remote(
    name="meta", host="h", user="u", auth="kerberos", scheduler="pbs",
    allowed_dirs=["/storage/x"],
    allowed_ops=["qsub", "qstat", "logs"],
    limits={"max_walltime": "24:00:00", "max_cpus": 16, "max_mem_gb": 64,
            "max_gpus": 1, "max_scratch_gb": 100,
            "scratch_types": ["scratch_ssd"], "queues": ["default"],
            "max_concurrent_jobs": 4, "max_fix_attempts": 3},
)


def make(replies):
    """Fake transport: one (rc, stdout, stderr) tuple per SSH call; records argv."""
    calls = []
    queue = list(replies)

    def runner(argv):
        calls.append(argv)
        rc, out, err = queue.pop(0) if queue else (0, "", "")
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return transport.Transport(GOLD, runner=runner), calls


def submit(tmp_path, t, **overrides):
    kwargs = dict(
        workspace=tmp_path, task="run", walltime="01:00:00", cpus=1,
        mem_gb=4, gpus=0, queue="default", name=None,
    )
    kwargs.update(overrides)
    return cli.cmd_submit(GOLD, t, "/storage/x/repo", "run.sh", **kwargs)


def test_submit_defaults_command_is_unchanged(tmp_path, capsys):
    t, calls = make([(0, "101.meta-pbs\n", "")])
    assert submit(tmp_path, t) == 0
    assert calls[0][-1] == (
        "cd /storage/x/repo && qsub -N run -q default "
        "-l walltime=01:00:00 -l select=1:ncpus=1:mem=4gb run.sh"
    )
    assert capsys.readouterr().out == "SUBMITTED: 101.meta-pbs attempt=1\n"


def test_clamp_gpu_and_name_command_and_stdout_are_unchanged(tmp_path, capsys):
    t, calls = make([(0, "101.meta-pbs\n", "")])
    assert submit(tmp_path, t, walltime="48:00:00", cpus=99, mem_gb=999,
                  gpus=1, name="paper1-dag") == 0
    assert calls[0][-1] == (
        "cd /storage/x/repo && qsub -N paper1-dag -q default "
        "-l walltime=24:00:00 -l select=1:ncpus=16:mem=64gb:ngpus=1 run.sh"
    )
    assert capsys.readouterr().out == (
        "CLAMPED: walltime 48:00:00 clamped to 24:00:00\n"
        "CLAMPED: cpus 99 clamped to 16\n"
        "CLAMPED: mem_gb 999 clamped to 64\n"
        "SUBMITTED: 101.meta-pbs attempt=1\n"
    )


def test_scratch_and_environment_command_is_unchanged(tmp_path):
    t, calls = make([(0, "101.meta-pbs\n", "")])
    assert submit(
        tmp_path, t, cpus=4, mem_gb=16, scratch_type="scratch_ssd", scratch_gb=60,
        environment_assignments=["SUBJECT=105216", "RESULTS_ROOT=/storage/x/results"],
    ) == 0
    assert calls[0][-1] == (
        "cd /storage/x/repo && qsub -N run -q default "
        "-l walltime=01:00:00 -l select=1:ncpus=4:mem=16gb:scratch_ssd=60gb "
        "-v SUBJECT=105216,RESULTS_ROOT=/storage/x/results run.sh"
    )
    entry = jobs.load_jobs(tmp_path)[0]
    assert entry["environment"] == {"SUBJECT": "105216", "RESULTS_ROOT": "/storage/x/results"}


def test_submit_dir_command_and_ledger_entry_are_unchanged(tmp_path):
    t, calls = make([(0, "101.meta-pbs\n", "")])
    assert submit(tmp_path, t, submit_dir="/storage/x/logs") == 0
    assert calls[0][-1] == (
        "cd /storage/x/logs && qsub -N run -q default "
        "-l walltime=01:00:00 -l select=1:ncpus=1:mem=4gb /storage/x/repo/run.sh"
    )
    entry = jobs.load_jobs(tmp_path)[0]
    assert entry["dir"] == "/storage/x/logs"
    assert entry["code_dir"] == "/storage/x/repo"
    assert entry["job_name"] == "run"
    assert entry["state"] == "queued" and entry["attempt"] == 1


def test_status_command_and_stdout_are_unchanged(tmp_path, capsys):
    t, _ = make([(0, "101.meta-pbs\n", "")])
    submit(tmp_path, t)
    capsys.readouterr()
    t2, calls2 = make([(0, "    job_state = R\n", "")])
    assert cli.cmd_status(GOLD, t2, "101.meta-pbs", workspace=tmp_path) == 0
    assert calls2[0][-1] == "qstat -xf 101.meta-pbs"
    assert capsys.readouterr().out == "STATE: running exit=-\n"
    assert jobs.load_jobs(tmp_path)[0]["state"] == "running"


def test_status_done_and_failed_are_unchanged(tmp_path, capsys):
    t, _ = make([(0, "101.meta-pbs\n", "")])
    submit(tmp_path, t)
    capsys.readouterr()
    t2, _ = make([(0, "    job_state = F\n    Exit_status = 0\n", "")])
    assert cli.cmd_status(GOLD, t2, "101.meta-pbs", workspace=tmp_path) == 0
    assert capsys.readouterr().out == "STATE: done exit=0\n"
    t3, _ = make([(0, "    job_state = F\n    Exit_status = 1\n", "")])
    assert cli.cmd_status(GOLD, t3, "101.meta-pbs", workspace=tmp_path) == 0
    assert capsys.readouterr().out == "STATE: failed exit=1\n"


def test_logs_command_and_stdout_are_unchanged(tmp_path, capsys):
    t, _ = make([(0, "101.meta-pbs\n", "")])
    submit(tmp_path, t, name="run")
    capsys.readouterr()
    t2, calls2 = make([(0, "log text\n", "")])
    assert cli.cmd_logs(GOLD, t2, "101.meta-pbs", workspace=tmp_path) == 0
    assert calls2[0][-1] == "cd /storage/x/repo && cat run.o101 run.e101 2>/dev/null"
    assert capsys.readouterr().out == "log text\n"


def test_qsub_failure_messages_are_unchanged(tmp_path, capsys):
    t, _ = make([(1, "", "boom")])
    assert submit(tmp_path, t) == 1
    assert capsys.readouterr().err == "QSUB_FAILED: boom\n"
    assert jobs.load_jobs(tmp_path) == []


def test_qsub_empty_stdout_is_qsub_failed_without_ledger_row(tmp_path, capsys):
    t, _ = make([(0, "", "")])
    assert submit(tmp_path, t) == 1
    assert capsys.readouterr().err == (
        "QSUB_FAILED: qsub returned success but no job id\n"
    )
    assert jobs.load_jobs(tmp_path) == []


def test_unparseable_status_leaves_ledger_untouched(tmp_path, capsys):
    t, _ = make([(0, "101.meta-pbs\n", "")])
    submit(tmp_path, t)
    before = jobs.load_jobs(tmp_path)
    capsys.readouterr()
    t2, _ = make([(0, "garbage\n", "")])
    assert cli.cmd_status(GOLD, t2, "101.meta-pbs", workspace=tmp_path) == 1
    assert capsys.readouterr().err == "UNPARSEABLE: garbage\n\n"
    assert jobs.load_jobs(tmp_path) == before
