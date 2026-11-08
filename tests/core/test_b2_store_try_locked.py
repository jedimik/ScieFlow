"""T-9 t1: store.try_locked and store.Busy (B2:5.8 start lock, T8-D14 operation lock).

Hermetic: temporary directories only; the lock holder is a helper subprocess of this test.
"""

import subprocess
import sys
import time

import pytest
from filelock import FileLock

from scieflow.core import store

HOLD = (
    "import sys, time\n"
    "from filelock import FileLock\n"
    "lock = FileLock(sys.argv[1] + '.lock', timeout=0)\n"
    "lock.acquire()\n"
    "print('held', flush=True)\n"
    "time.sleep(60)\n"
)


def test_free_lock_is_entered_and_released(tmp_path):
    path = tmp_path / "archives" / "r1"
    path.parent.mkdir()
    with store.try_locked(path):
        assert (tmp_path / "archives" / "r1.lock").exists()
    # released: a second non-blocking take succeeds
    with store.try_locked(path):
        pass


def test_held_by_another_process_is_busy_at_once(tmp_path):
    path = tmp_path / "r1"
    holder = subprocess.Popen([sys.executable, "-c", HOLD, str(path)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with pytest.raises(store.Busy):
            with store.try_locked(path):
                pytest.fail("the body must not run while the lock is held elsewhere")
        assert time.monotonic() - started < 0.5
    finally:
        holder.kill()
        holder.wait()


def test_busy_is_an_exception_and_held_in_this_process_too(tmp_path):
    assert issubclass(store.Busy, Exception)
    path = tmp_path / "r2"
    outer = FileLock(str(path) + ".lock", timeout=0)
    outer.acquire()
    try:
        with pytest.raises(store.Busy):
            with store.try_locked(path):
                pass
    finally:
        outer.release()


def test_never_creates_a_missing_parent(tmp_path):
    path = tmp_path / "missing" / "r3"
    with pytest.raises(OSError):
        with store.try_locked(path):
            pass
    assert not (tmp_path / "missing").exists()


def test_existing_locked_still_creates_its_parent_and_enters(tmp_path):
    assert store.LOCK_TIMEOUT == 30
    path = tmp_path / "new" / "status.yml"
    with store.locked(path):
        assert (tmp_path / "new").is_dir()
