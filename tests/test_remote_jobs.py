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
