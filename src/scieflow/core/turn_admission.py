"""Protected nonqueueing Linux admission, separate from writable YAML locks.

No descriptor is shared between entrants, and the stable empty lock is never
unlinked. This is a bounded identity check, not an atomic whole-host freeze.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys

from scieflow.core import sandbox
from scieflow.core.project import Project

try:
    import fcntl
except ImportError:  # ordinary unsupported-host compatibility is caller-selected
    fcntl = None


class AdmissionError(ValueError):
    """An activity cannot safely enter the shared conversation boundary."""


class AdmissionBusy(AdmissionError):
    """Another entrant owns this canonical run's reservation."""


class AdmissionUnsafe(AdmissionError):
    """The selected protection or descriptor identity cannot be proved."""


def backend_supported() -> bool:
    """API selection only; a failed supported lock NEVER means unsupported."""
    return (sys.platform == 'linux' and fcntl is not None and
            all(hasattr(fcntl, name) for name in ('flock', 'LOCK_EX', 'LOCK_NB')) and
            all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_CLOEXEC')) and
            os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd)


def _stat_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)


def _publish_mask_noreplace(src_parent_fd: int, src_name: str,
                            dst_parent_fd: int, dst_name: str) -> None:
    """Publish between held directories, never replace even an empty leaf.

    Only libc's Linux renameat2 ABI is supported. Errors prove no authority
    to adopt, retry or fall back, not that the filesystem had no effect.
    """
    for name in (src_name, dst_name):
        if (not isinstance(name, str) or not name or name in ('.', '..') or
                '/' in name or '\x00' in name):
            raise ValueError('mask publication requires direct-child basenames')
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise OSError(errno.ENOSYS, 'libc no-replace publication is unavailable') from exc
    try:
        renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                             ctypes.c_char_p, ctypes.c_uint)
        renameat2.restype = ctypes.c_int
        result = renameat2(src_parent_fd, os.fsencode(src_name), dst_parent_fd,
                           os.fsencode(dst_name), 1)  # Linux RENAME_NOREPLACE only.
        error = ctypes.get_errno()
    except (AttributeError, ctypes.ArgumentError, TypeError, ValueError, OverflowError) as exc:
        raise OSError(errno.EINVAL, 'no-replace publication ABI is unavailable') from exc
    if result != 0:
        raise OSError(error or errno.EIO, 'exclusive mask publication failed')


def _fact_identity(fact: sandbox.ProtectionPath):
    identity = (fact.path, fact.resolved, fact.exists, fact.device, fact.inode,
                fact.mode, fact.link_target)
    # Entry churn in an existing directory is not inode replacement. Regular
    # policy content metadata and symlink identity remain strict; no atime.
    if fact.exists and stat.S_ISDIR(fact.mode):
        return identity
    return (*identity, fact.size, fact.mtime_ns, fact.ctime_ns)


def _validate_facts(facts):
    for fact in facts:
        if _fact_identity(sandbox._path_fact(fact.path)) != _fact_identity(fact):
            raise AdmissionUnsafe(f'protection path identity changed: {fact.path}')


def _safety_closure(facts, held_paths, held_aliases, policy):
    """Finite captured safety routes, not every ordinary grant/run fact.

    Canonical ancestors alone lose the spelling of intermediate aliases.
    Follow captured link targets as well, including relative/.. spellings,
    and transitively include aliases resolving to a protected identity.
    Descriptor-pinned storage paths need no producer recapture.
    """
    captured = dict(facts)
    for fact in held_aliases:
        captured.setdefault(fact.path, fact)
    aliases = {}
    for fact in captured.values():
        if fact.link_target is not None:
            aliases.setdefault(fact.resolved, []).append(fact.path)
    pending = [policy, *held_paths]
    critical = set()
    while pending:
        seed = pending.pop()
        for path in (seed, *seed.parents):
            if path in critical:
                continue
            critical.add(path)
            fact = captured.get(path)
            if fact is None:
                if path not in held_paths:
                    raise AdmissionUnsafe('incomplete captured safety alias/ancestor route')
            else:
                pending.append(fact.resolved)
                if fact.link_target is not None:
                    target = Path(fact.link_target)
                    pending.append(target if target.is_absolute() else path.parent / target)
            pending.extend(aliases.get(path, ()))
    return critical


@dataclass(frozen=True)
class _LaunchMask:
    """One owned host transition, not a recaptured protection envelope."""
    protection: sandbox.TurnProtection
    absence: sandbox.ProtectionPath
    fd: int
    identity: tuple


class Reservation:
    """Owns each pinned descriptor for ONE context lifetime."""

    def __init__(self, root: Path, run: Path, aliases):
        self.project_root = root
        self.run_dir = run
        self.workspace_root = root / 'workspace'
        self._aliases = aliases
        self._fds = {}
        self._identities = {}
        self._active = True
        self._lock_path = None
        # Separate from _fds: this leaf is intentionally inside writable run
        # storage, not an immutable neutral-storage safety ancestor.
        self._launch_mask = None

    def _remember(self, path, fd):
        try:
            os.set_inheritable(fd, False)
            info = os.fstat(fd)
            self._fds[path] = fd
            self._identities[path] = _stat_identity(info)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _pin_directory(self, path):
        if path in self._fds:
            return self._fds[path]
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        if path == Path('/'):
            fd = os.open('/', flags)
        else:
            parent = self._pin_directory(path.parent)
            fd = os.open(path.name, flags, dir_fd=parent)
        return self._remember(path, fd)

    def _owned_directory(self, path):
        parent = self._pin_directory(path.parent)
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass  # genuine concurrent creation still undergoes all checks
        fd = self._pin_directory(path)
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise AdmissionUnsafe(f'unsafe admission directory owner/mode: {path}')
        return fd

    def _lock_info(self):
        info = os.fstat(self._fds[self._lock_path])
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0 or
                info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise AdmissionUnsafe('unsafe admission lock type/link/content/owner/mode')
        return info

    def _validate_held(self):
        if not self._active or self._lock_path is None:
            raise AdmissionUnsafe('reservation is not held')
        for path, fd in self._fds.items():
            info = os.fstat(fd)
            actual = path.lstat()
            if (_stat_identity(info) != self._identities[path] or
                    _stat_identity(actual) != self._identities[path]):
                raise AdmissionUnsafe(f'admission path identity changed: {path}')
        self._lock_info()
        _validate_facts(self._aliases)

    def _acquire(self):
        key = json.dumps(['scieflow.turn-admission.v1', str(self.project_root), str(self.run_dir)],
                         ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self._pin_directory(self.project_root)
        self._pin_directory(self.workspace_root)
        self._pin_directory(self.run_dir)
        self._owned_directory(self.project_root / '.scieflow')
        directory = self.project_root / '.scieflow/turn-admission'
        parent = self._owned_directory(directory)
        self._lock_path = directory / ('v1-' + hashlib.sha256(key).hexdigest() + '.lock')
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            fd = os.open(self._lock_path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        except FileExistsError:
            fd = os.open(self._lock_path.name, flags, dir_fd=parent)
        self._remember(self._lock_path, fd)
        self._validate_held()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                # Refuse replacement rather than mislabelling another inode busy.
                self._validate_held()
                raise AdmissionBusy('conversation admission busy; wait or cancel the running turn') from exc
            raise AdmissionUnsafe('supported admission backend cannot safely lock this filesystem') from exc
        self._validate_held()

    def _validate_mask(self, owned):
        path = owned.absence.path
        info = os.fstat(owned.fd)
        actual = os.stat(path.name, dir_fd=self._fds[self.run_dir], follow_symlinks=False)
        fact = sandbox._path_fact(path)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) != 0o700 or os.get_inheritable(owned.fd) or
                _stat_identity(info) != owned.identity or
                _stat_identity(actual) != owned.identity or not fact.exists or
                fact.resolved != path or fact.link_target is not None or
                (fact.device, fact.inode, fact.mode) != owned.identity[:3]):
            raise AdmissionUnsafe(f'owned launch mask identity changed: {path}')

    def _validate_protection_facts(self, protection):
        owned = self._launch_mask
        if owned is not None:
            if protection is not owned.protection:
                raise AdmissionUnsafe('owned launch mask requires the original protection object')
            if owned.absence not in protection.path_facts:
                raise AdmissionUnsafe('owned launch mask captured absence changed')
        for fact in protection.path_facts:
            if owned is not None and fact.path == owned.absence.path:
                if fact != owned.absence:
                    raise AdmissionUnsafe('owned launch mask captured absence changed')
                self._validate_mask(owned)
            else:
                _validate_facts((fact,))

    def prepare_launch_masks(self, protection: sandbox.TurnProtection) -> None:
        """Send-only exclusive preparation of the ONE captured-absent mask.

        Validate first, never adopt strangers or recapture policy. Persistent
        replacement at checked boundaries refuses; this is not privileged
        same-user filesystem atomicity. Failure never unlinks a changed path.
        """
        self.validate_protection(protection)
        if self._launch_mask is not None:
            return  # SAME-object validation already checked the held transition.
        from scieflow.core.provenance import REPO_DIR
        path = self.run_dir / REPO_DIR
        if path not in protection.masks:
            raise AdmissionUnsafe('canonical provenance launch mask is missing')
        absence = next(f for f in protection.path_facts if f.path == path)
        if absence.exists:
            return  # Originally existing facts stay strict, with no rewrite.
        if absence.resolved != path or absence.link_target is not None:
            raise AdmissionUnsafe('unprovable captured provenance mask absence')
        fd = witness = None
        stage = identity = None
        neutral = self._fds[self._lock_path.parent]
        try:
            self._validate_held()
            parent = self._fds[self.run_dir]
            if os.fstat(neutral).st_dev != os.fstat(parent).st_dev:
                raise AdmissionUnsafe('mask publication requires the same mounted filesystem')
            stage = 'mask-v1-' + secrets.token_hex(16) + '.stage'
            route = self._lock_path.parent / stage
            for grant in (*protection.writable, *protection.allowlist, *protection.coordinator_writable):
                if grant == route or grant in route.parents or route in grant.parents:
                    raise AdmissionUnsafe('writable grant reaches protected mask stage')
            # The neutral safety domain excludes all effective child grants.
            # Randomness/0700 alone are not protection from a confined writer.
            os.mkdir(stage, mode=0o700, dir_fd=neutral)  # Collision NEVER adopts/retries.
            fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=neutral)
            os.set_inheritable(fd, False)
            created = os.fstat(fd)
            if (not stat.S_ISDIR(created.st_mode) or created.st_uid != os.geteuid() or
                    stat.S_IMODE(created.st_mode) != 0o700 or os.get_inheritable(fd) or
                    created.st_dev != os.fstat(neutral).st_dev):
                raise AdmissionUnsafe('unsafe created provenance mask owner/type/mode')
            identity = _stat_identity(created)
            if _stat_identity(os.stat(stage, dir_fd=neutral, follow_symlinks=False)) != identity:
                raise AdmissionUnsafe('protected mask stage identity changed')
            self._validate_held()
            self._validate_protection_facts(protection)
            if (_stat_identity(os.fstat(fd)) != identity or
                    _stat_identity(os.stat(stage, dir_fd=neutral, follow_symlinks=False)) != identity):
                raise AdmissionUnsafe('protected mask stage identity changed before publication')
            _publish_mask_noreplace(neutral, stage, parent, path.name)
            # Compare to the PREPUBLICATION inode, never sample a new baseline.
            if (_stat_identity(os.fstat(fd)) != identity or
                    _stat_identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != identity):
                raise AdmissionUnsafe('published mask does not match the pinned stage')
            self._validate_held()
            witness = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=parent)
            os.set_inheritable(witness, False)
            if os.get_inheritable(witness) or _stat_identity(os.fstat(witness)) != identity:
                raise AdmissionUnsafe('published mask witness identity changed')
            os.close(witness)
            witness = None
            owned = _LaunchMask(protection, absence, fd, identity)
            self._validate_mask(owned)
            self._launch_mask = owned
            self._validate_held()
            self._validate_protection_facts(protection)
            self._validate_held()
        except BaseException as exc:
            self._launch_mask = None
            # Remove only this exact, proven still-unpublished empty source.
            # Open/identity/parent uncertainty leaves a bounded orphan instead.
            if fd is not None and identity is not None:
                try:
                    self._validate_held()
                    if (_stat_identity(os.fstat(fd)) == identity and
                            _stat_identity(os.stat(stage, dir_fd=neutral, follow_symlinks=False)) == identity):
                        os.rmdir(stage, dir_fd=neutral)
                except (OSError, AdmissionError, ValueError, RuntimeError, TypeError):
                    pass  # Never repair, scan, or unlink a destination/unknown entry.
            if witness is not None:
                os.close(witness)
            if fd is not None:
                os.close(fd)
            if isinstance(exc, AdmissionError):
                raise
            if isinstance(exc, (OSError, ValueError, RuntimeError, TypeError)):
                raise AdmissionUnsafe('cannot safely prepare owned provenance mask') from exc
            raise

    def validate_protection(self, protection: sandbox.TurnProtection) -> None:
        """Revalidate the SAME captured complete envelope without semantic I/O."""
        try:
            self._validate_held()
            if type(protection) is not sandbox.TurnProtection:
                raise AdmissionUnsafe('a complete typed TurnProtection is required')
            if (protection.project_root, protection.run_dir, protection.workspace_root) != (
                    self.project_root, self.run_dir, self.workspace_root):
                raise AdmissionUnsafe('protection project/run/workspace does not match admission')
            if not protection.sandbox_enabled or protection.writable is None:
                raise AdmissionUnsafe('unconfined writable protection cannot protect admission')
            if (protection.writable != (self.run_dir, *protection.allowlist) or
                    protection.coordinator_writable != (self.workspace_root, *protection.allowlist) or
                    protection.masks != tuple(sandbox.masks_for(list(protection.writable), run_dir=self.run_dir))):
                raise AdmissionUnsafe('incomplete writable/coordinator/mask envelope')
            facts = {fact.path: fact for fact in protection.path_facts}
            if not facts or len(facts) != len(protection.path_facts) or len(facts) > 8192:
                raise AdmissionUnsafe('incomplete or ambiguous protection path facts')
            policy = self.project_root / sandbox.ALLOWLIST_FILE
            seeds = {self.project_root, self.workspace_root, self.run_dir, policy,
                     *protection.writable, *protection.coordinator_writable, *protection.masks,
                     *protection.readable}
            # Require the bounded closure of every captured lexical/canonical
            # ancestor and alias, rather than trusting an omitted safety path.
            for fact in protection.path_facts:
                seeds.update((fact.path, fact.resolved))
                if fact.link_target is not None:
                    target = Path(fact.link_target)
                    seeds.add(target if target.is_absolute() else fact.path.parent / target)
            needed = {p for seed in seeds for p in (seed, *seed.parents)}
            if not needed.issubset(facts):
                raise AdmissionUnsafe('incomplete protection alias/ancestor facts')
            # S2: each captured readable grant must be a live, resolved fact of its own.
            for path in protection.readable:
                fact = facts.get(path)
                if fact is None or not fact.exists or fact.resolved != path:
                    raise AdmissionUnsafe('incomplete readable protection facts')
            policy_fact = facts[policy]
            if policy_fact.exists:
                if not stat.S_ISREG(policy_fact.mode) or protection.policy_sha256 is None:
                    raise AdmissionUnsafe('unprovable captured policy identity')
            elif protection.policy_sha256 is not None:
                raise AdmissionUnsafe('inconsistent captured policy absence')
            self._validate_protection_facts(protection)
            # Any bind covering a protected path OR its replaceable alias or
            # ancestor can destroy the neutral boundary. Include broad grants,
            # even though a run launch deliberately narrows its own bind.
            # Ordinary run/workspace pins are not forbidden grants. They
            # become critical only if a policy/storage safety route uses them.
            held_paths = set(self._fds) - {self.workspace_root, self.run_dir}
            critical = _safety_closure(facts, held_paths, self._aliases, policy)
            for grant in (*protection.writable, *protection.allowlist, *protection.coordinator_writable):
                if facts[grant].resolved != grant or any(target == grant or grant in target.parents for target in critical):
                    raise AdmissionUnsafe(f'writable grant can replace admission/safety identity: {grant}')
            self._validate_held()
        except AdmissionError:
            raise
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            raise AdmissionUnsafe('cannot prove current protection identity') from exc

    def _close(self):
        self._active = False
        try:
            if self._launch_mask is not None:
                os.close(self._launch_mask.fd)
        finally:
            self._launch_mask = None
            for fd in reversed(tuple(self._fds.values())):
                os.close(fd)
            self._fds.clear()


@contextmanager
def reserve(project: Project, ws: Path):
    """Neutral admission BEFORE configuration/state capture; no fallback."""
    if not backend_supported():
        raise AdmissionUnsafe('turn admission unsupported: Linux descriptor flock is required')
    reservation = None
    try:
        try:
            root = project.root.resolve(strict=True)
            workspace = (root / 'workspace').resolve(strict=True)
            run = Path(ws).resolve(strict=True)
            if (not root.is_dir() or not run.is_dir() or workspace != root / 'workspace' or
                    run.parent != workspace):
                raise AdmissionUnsafe('admission requires this project owning direct-child run')
            aliases = sandbox._capture_path_facts([project.root.absolute(), Path(ws).absolute(), root, workspace, run])
            reservation = Reservation(root, run, aliases)
            reservation._acquire()
        except AdmissionError:
            raise
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            raise AdmissionUnsafe('cannot prove safe admission identity') from exc
        yield reservation
    finally:
        if reservation is not None:
            reservation._close()
