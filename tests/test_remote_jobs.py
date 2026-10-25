import pytest

from remote import jobs


def submit(j, task="taskA", job_id="1.meta"):
    return jobs.record_submit(
        j, task=task, job_id=job_id, remote_name="meta",
        remote_dir="/storage/x", script="run.sh",
        resources={"walltime": "01:00:00", "cpus": 2, "mem_gb": 4, "gpus": 0,
                   "queue": "default"},
    )


def test_roundtrip_and_attempts(tmp_path):
    j = jobs.load_jobs(tmp_path)
    assert j == []
    entry = submit(j)
    assert entry["state"] == "queued" and entry["attempt"] == 1
    assert entry["environment"] == {}
    assert entry["job_name"] == "run"
    submit(j, job_id="2.meta")
    assert jobs.attempts(j, "taskA") == 2
    jobs.save_jobs(tmp_path, j)
    assert jobs.load_jobs(tmp_path)[1]["job_id"] == "2.meta"
    assert (tmp_path / "remote" / "jobs.yml").exists()


def test_active_count_and_set_state(tmp_path):
    j = []
    submit(j, job_id="1.meta")
    submit(j, task="taskB", job_id="2.meta")
    assert jobs.active_count(j, "meta") == 2
    jobs.set_state(j, "1.meta", "done")
    assert jobs.active_count(j, "meta") == 1
    with pytest.raises(KeyError):
        jobs.set_state(j, "9.meta", "done")
    with pytest.raises(ValueError):
        jobs.set_state(j, "2.meta", "vanished")


def test_submit_records_environment():
    j = []
    entry = jobs.record_submit(
        j, task="taskA", job_id="1.meta", remote_name="meta",
        remote_dir="/storage/x", script="run.sh",
        resources={"walltime": "01:00:00"},
        environment={"SUBJECT": "105216"},
    )
    assert entry["environment"] == {"SUBJECT": "105216"}


def test_submit_records_custom_job_name():
    j = []
    entry = jobs.record_submit(
        j, task="taskA", job_id="1.meta", remote_name="meta",
        remote_dir="/storage/x", script="run.sh",
        resources={"walltime": "01:00:00"}, job_name="custom-name",
    )
    assert entry["job_name"] == "custom-name"


def test_set_state_accepts_cancelled_and_unknown():
    j = []
    submit(j, job_id="1.meta")
    jobs.set_state(j, "1.meta", "cancelled")
    assert j[0]["state"] == "cancelled"
    jobs.set_state(j, "1.meta", "unknown")
    assert j[0]["state"] == "unknown"


def test_unknown_counts_as_active_so_it_never_frees_a_slot():
    j = []
    submit(j, job_id="1.meta")
    jobs.set_state(j, "1.meta", "unknown")
    assert jobs.active_count(j, "meta") == 1
    jobs.set_state(j, "1.meta", "cancelled")
    assert jobs.active_count(j, "meta") == 0


def test_find_returns_first_match_or_none():
    j = []
    submit(j, job_id="1.meta")
    submit(j, task="taskB", job_id="2.meta")
    assert jobs.find(j, "2.meta") is j[1]
    assert jobs.find(j, "9.meta") is None


def test_record_submit_writes_scheduler_and_legacy_entries_read_as_pbs():
    j = []
    entry = submit(j)
    assert entry["scheduler"] == "pbs"
    assert jobs.entry_scheduler(entry) == "pbs"
    assert jobs.entry_scheduler({"job_id": "old.meta"}) == "pbs"
    slurm = jobs.record_submit(
        j, task="taskC", job_id="3", remote_name="meta", remote_dir="/storage/x",
        script="run.sh", resources={}, scheduler="slurm",
    )
    assert slurm["scheduler"] == "slurm"
