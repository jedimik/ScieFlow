"""Lock primitives for the configurator owner (T-12 t2).

`exclusive` locks exactly the given path; `held_locks` takes sibling
`<path>.lock` files once each in sorted order, and `LockSet.replace` writes
only a path the set holds. Neither creates directories.
"""

import subprocess
import sys

import filelock
import pytest

from scieflow.core import store

HOLDER = ("import sys, time, filelock\n"
          "lock = filelock.FileLock(sys.argv[1])\n"
          "lock.acquire()\n"
          "print('held', flush=True)\n"
          "time.sleep(60)\n")


def test_exclusive_locks_the_path_itself_not_a_lock_suffix(tmp_path):
    path = tmp_path / ".agent-configure.lock"
    with store.exclusive(path, 1):
        assert path.exists()
        assert not (tmp_path / ".agent-configure.lock.lock").exists()


def test_exclusive_does_not_create_a_missing_directory(tmp_path):
    missing = tmp_path / "absent"
    with pytest.raises(OSError):
        with store.exclusive(missing / ".agent-configure.lock", 1):
            pass
    assert not missing.exists()


def test_held_locks_acquire_sorted_unique_paths_once(tmp_path, monkeypatch):
    order = []
    real_acquire = filelock.FileLock.acquire

    def spy(self, *args, **kwargs):
        order.append(self.lock_file)
        return real_acquire(self, *args, **kwargs)

    monkeypatch.setattr(filelock.FileLock, "acquire", spy)
    paths = [tmp_path / "b.yml", tmp_path / "a.yml", tmp_path / "a.yml"]
    with store.held_locks(paths, 1):
        pass
    assert order == [str(tmp_path / "a.yml.lock"), str(tmp_path / "b.yml.lock")]


def test_replace_writes_only_a_path_the_set_holds(tmp_path):
    held = tmp_path / "a.yml"
    other = tmp_path / "b.yml"
    held.write_text("old\n")
    with store.held_locks([held], 1) as locks:
        with pytest.raises(RuntimeError):
            locks.replace(other, "new\n")
        assert not other.exists()
        locks.replace(held, "new\n")
    assert held.read_text() == "new\n"


def test_replace_does_not_go_through_write_text(tmp_path, monkeypatch):
    held = tmp_path / "a.yml"
    held.write_text("old\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("LockSet.replace must not call store.write_text")

    monkeypatch.setattr(store, "write_text", forbidden)
    with store.held_locks([held], 1) as locks:
        locks.replace(held, "new\n")
    assert held.read_text() == "new\n"


def test_exclusive_times_out_while_another_process_holds_it(tmp_path):
    path = tmp_path / "lock"
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(path)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(filelock.Timeout):
            with store.exclusive(path, 0.2):
                pass
    finally:
        holder.kill()
        holder.wait()
