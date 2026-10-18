"""Filesystem confinement for agent dispatches — the only bubblewrap-aware module.

A sandboxed process may write inside the run it was given, plus a private /tmp
and whatever the allowlist grants; its uv cache lives inside that same run. It
may read the repository, because agents read their own protocols, skills and
schemas.

What this deliberately does NOT do: restrict the network, or hide credentials.
Agents need both to reach their model API, so a hostile agent can still send
what it can read. See docs/sandbox.md; that limit is stated, not implied.
"""

from __future__ import annotations

import os
import hashlib
import re
import stat
import sys
import sysconfig
from collections.abc import Callable
from dataclasses import dataclass, replace
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from uuid import uuid4

import yaml

from scieflow.core.project import Project

BWRAP = "bwrap"
VERIFY_TIMEOUT_S = 30
INSTALL_HINT = "install it with: sudo apt-get install -y bubblewrap"

# D1: the system set is read-only. Merged-usr links are recreated from the host's
# own readlink target; /lib64 and friends appear only when the host has them.
RESOLV_CONF = Path("/etc/resolv.conf")   # module constant so tests can point it elsewhere
MERGED_USR = ("bin", "lib", "lib64", "sbin")
MERGED_USR_ROOT = Path("/")   # where the host's merged-usr entries are read; a seam for tests
SYSTEM_TREES = (Path("/usr"), Path("/etc"))
SYSTEM_PATH = ("/usr/local/bin", "/usr/bin", "/bin")

# D2/C5: one persistent, per-run, per-agent HOME inside the run's own grant.
AGENT_HOME_DIR = "agent-home"
AGENT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# D5/C9 (A1): hard deny table, checked lexically and after resolve().
_SECRET_UNDER_HOME = (".ssh", ".gnupg", ".hermes", ".config/scieflow/credentials")
_AGENT_STORES = (".claude", ".claude.json", ".codex", ".gemini")
_RUNTIME_SOCKETS = (Path("/run"), Path("/var/run"), Path("/proc"), Path("/sys"), Path("/dev"))
_MAX_LINK_HOPS = 8
INSTALL_WALK_MAX = 20_000   # A1: the most entries a store-resident install root may hold
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")   # an `env` NAME=VALUE operand

# S4 (C10-C12, A8): the env allow-list names, the credential entry keys, the token size cap.
CREDENTIALS_OVERRIDE: Path | None = None   # test seam: tests/conftest.py points it at a tmp dir (A9)
C12_KEPT = frozenset({"PATH", "HOME", "LANG", "TZ", "TERM", "UV_CACHE_DIR", "SCIEFLOW_STATE_DIR",
                      "SCIEFLOW_PROMPT_ARGV_LIMIT", "SCIEFLOW_LEGACY_QUIET", "SCIEFLOW_MAILTO"})
CREDENTIAL_ENV_KEYS = frozenset({"mode", "var", "file", "reason"})
CREDENTIAL_BIND_KEYS = frozenset({"mode", "source", "target", "writable", "reason"})
ENV_VAR_RE = re.compile(r"[A-Z_][A-Z0-9_]*")
CREDENTIAL_TOKEN_MAX = 4096


class SandboxError(RuntimeError):
    """The sandbox could not be built, or its configuration is not allowed."""


class SandboxUnavailable(SandboxError):
    """bubblewrap is missing, or it did not actually confine a test write."""


def available() -> bool:
    return shutil.which(BWRAP) is not None


def _validate_writable(writable: list[Path]) -> None:
    """Validate that each writable entry is a directory (or does not exist yet).

    Raises SandboxError if any entry exists and is not a directory, which would
    cause cryptic errors when trying to create sentinel files or bind mounts.
    """
    for entry in writable:
        path = Path(entry).resolve()
        if path.exists() and not path.is_dir():
            raise SandboxError(
                f"writable grant must be a directory, not a file: {path}")


def masks_for(writable: list[Path], *, run_dir: Path | None) -> list[Path]:
    """Paths to hide inside an agent dispatch's grant: the run's provenance repo.

    The repo holds manuscript history and is derived, so a corrupt one is
    rebuilt — which loses that history silently. Masking it makes the loss
    impossible rather than merely unmotivated. Only a grant that IS the run
    directory is masked: a coordinator's workspace-root grant is not (that would
    need enumerating runs at wrap time), and allowlisted paths never are,
    because a masked mountpoint materialises on the host and would litter an
    empty `provenance.git` under each. Pure: decided from the arguments alone.
    """
    if run_dir is None:
        return []
    from scieflow.core.provenance import REPO_DIR
    run = Path(run_dir).resolve()
    if run not in [Path(entry).resolve() for entry in writable]:
        return []
    # C13: every agent's home is hidden from the others; wrap() then binds this
    # dispatch's own home back on top of the mask.
    return [run / REPO_DIR, run / "scratch" / AGENT_HOME_DIR]


@dataclass(frozen=True, slots=True)
class Layout:
    """What a dispatch sees beyond the system set and its writable grants (C2).

    readable: bound read-only at the same path. installs: read-only install roots of the
    agent CLI and the uv-managed tools (S3; checked as installs, A1). links: (link, target)
    pairs, recreated with --symlink. home: the dispatch's own HOME, bound read-write
    after the masks. launcher_dirs: directories of bound launchers, which form
    the start of the sandbox PATH (C6). hidden: paths overlaid after the
    readable binds and before the writable ones (C14): a file becomes
    /dev/null, a directory an empty tmpfs.
    """
    readable: tuple[Path, ...] = ()
    links: tuple[tuple[Path, str], ...] = ()
    home: Path | None = None
    launcher_dirs: tuple[Path, ...] = ()
    hidden: tuple[Path, ...] = ()
    installs: tuple[Path, ...] = ()
    binds: tuple[tuple[Path, Path, bool], ...] = ()   # S4 (C10): credential binds, applied last

    def __post_init__(self):
        if type(self.readable) is not tuple or any(not _is_abs(p) for p in self.readable):
            raise TypeError("readable must be an immutable tuple of absolute Paths")
        if type(self.links) is not tuple or any(
                type(pair) is not tuple or len(pair) != 2 or not _is_abs(pair[0])
                or type(pair[1]) is not str for pair in self.links):
            raise TypeError("links must be an immutable tuple of (absolute Path, target text)")
        if self.home is not None and not _is_abs(self.home):
            raise TypeError("home must be an absolute Path or None")
        if type(self.launcher_dirs) is not tuple or any(not _is_abs(p) for p in self.launcher_dirs):
            raise TypeError("launcher_dirs must be an immutable tuple of absolute Paths")
        if type(self.hidden) is not tuple or any(not _is_abs(p) for p in self.hidden):
            raise TypeError("hidden must be an immutable tuple of absolute Paths")
        if type(self.installs) is not tuple or any(not _is_abs(p) for p in self.installs):
            raise TypeError("installs must be an immutable tuple of absolute Paths")
        if type(self.binds) is not tuple or any(
                type(b) is not tuple or len(b) != 3 or not _is_abs(b[0]) or not _is_abs(b[1])
                or type(b[2]) is not bool for b in self.binds):
            raise TypeError("binds must be an immutable tuple of (absolute source, absolute dest, bool) triples")


def _is_abs(value) -> bool:
    return isinstance(value, Path) and value.is_absolute()


def check_agent_name(agent) -> None:
    """Refuse an agent name that cannot name an agent home (C5). Public for the service."""
    if type(agent) is not str or AGENT_NAME_RE.fullmatch(agent) is None:
        raise SandboxError(f"agent name {agent!r} cannot name an agent home")


_check_agent_name = check_agent_name      # the internal spelling used by this module


def _homes() -> list[Path]:
    """Both the HOME env and the passwd home: either may be the real one."""
    out = {Path.home().resolve()}
    try:
        import pwd
        out.add(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())
    except (ImportError, KeyError, OSError):
        pass
    return sorted(out, key=str)


def _install_root_in_store(root: Path, store: Path) -> None:
    """A1 (S1 review F4): an install root may lie inside an agent store only as a clean
    `packages/**/{versions,releases}/<v>` directory: a bounded walk must find no credential file."""
    from scieflow.chats.stores.base import CREDENTIAL_FILES
    rel = root.relative_to(store).parts
    if rel[:1] != ("packages",) or root.parent.name not in ("versions", "releases"):
        raise SandboxError(f"{root} lies inside the agent credential store {store}; never granted")
    if root.is_symlink() or not root.is_dir():
        raise SandboxError(f"install root inside {store} must be a real directory: {root}")
    secrets = {Path(name).name for names in CREDENTIAL_FILES.values() for name in names}
    seen = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        names = [*dirnames, *filenames]
        seen += len(names)
        if seen > INSTALL_WALK_MAX:
            raise SandboxError(f"install root {root} holds more than {INSTALL_WALK_MAX} entries; refused")
        hit = sorted(secrets.intersection(names))
        if hit:
            raise SandboxError(f"install root {root} holds a credential file ({hit[0]}); never granted")


def check_not_denied(path: Path, *, kind: str = "readable") -> None:
    """Refuse a path that equals, lies inside, or contains a hard-deny path (A1).

    kind "bind" may name a file inside an agent credential store (C10). kind "install"
    (S3, A1) may name a clean `packages/**/{versions,releases}/<v>` root inside a store,
    checked by `_install_root_in_store`; any other store path is refused. Runtime sockets
    are refused for "readable" and "install" (F9) only: the resolver config ("resolv")
    lives under /run by design, so it keeps the store rules but not the socket rule.
    """
    candidates = {Path(path).absolute(), Path(path).resolve()}
    for c in candidates:
        for home in _homes():
            if c == home or c in home.parents:
                raise SandboxError(f"{c} is the home directory or contains it; never granted")
            for rel in _SECRET_UNDER_HOME:
                d = home / rel
                if c == d or d in c.parents or c in d.parents:
                    raise SandboxError(f"{c} touches the protected path {d}; never granted")
            for rel in _AGENT_STORES:
                d = home / rel
                if c == d or c in d.parents:
                    raise SandboxError(f"{c} is an agent credential store or contains one; never granted")
                if d in c.parents:
                    if kind == "bind":
                        continue
                    if kind == "install":
                        _install_root_in_store(c, d)
                        continue
                    raise SandboxError(f"{c} lies inside the agent credential store {d}; never granted")
        if kind in ("readable", "install"):
            for sock in _RUNTIME_SOCKETS:
                if c == sock or sock in c.parents or c in sock.parents:
                    raise SandboxError(f"{c} touches the runtime socket area {sock}; never readable")


def _in_system_set(path: Path) -> bool:
    if any(path == tree or tree in path.parents for tree in SYSTEM_TREES):
        return True
    # The merged-usr entries (/bin, /lib, ...) reach the sandbox through /usr: system too.
    return len(path.parts) > 1 and path.parts[0] == "/" and path.parts[1] in MERGED_USR


def _outermost(paths) -> list[Path]:
    """Unique paths, minus any that lie inside another one (A10/A17)."""
    kept: list[Path] = []
    for p in sorted(dict.fromkeys(Path(x) for x in paths), key=lambda q: len(q.parts)):
        if any(q == p or q in p.parents for q in kept):
            continue
        kept.append(p)
    return kept


def _system_args(resolv: Path) -> list[str]:
    """D1: the read-only system set, in C3 order, merged-usr links from the host."""
    out = ["--ro-bind", "/usr", "/usr"]
    for name in MERGED_USR:
        src = MERGED_USR_ROOT / name          # the host's entry (a seam for tests)
        dest = Path("/") / name               # where it appears inside
        if src.is_symlink():
            out += ["--symlink", os.readlink(src), str(dest)]
        elif src.is_dir():
            out += ["--ro-bind", str(src), str(dest)]
    out += ["--ro-bind", "/etc", "/etc", "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp", "--tmpfs", "/run"]
    target = _resolv_target(resolv)
    if target is not None and _resolv_allowed(target):
        out += ["--ro-bind", str(target), str(target)]
    return out


def _resolv_allowed(target: Path) -> bool:
    """F11: the resolved resolver config must pass the deny table; a refused one is skipped."""
    try:
        check_not_denied(target, kind="resolv")
    except SandboxError:
        return False
    return True


def _resolv_target(path: Path) -> Path | None:
    """The resolved DNS config, when it lies outside the system set (A10).

    N1: only a regular file qualifies. A link into /proc, /sys or /dev, or to a
    directory, is never bound, even though the resolver link is host-controlled.
    """
    try:
        target = Path(os.path.realpath(path))
    except OSError:
        return None
    if not target.is_file() or _in_system_set(target):
        return None
    if any(target == area or area in target.parents for area in (Path("/proc"), Path("/sys"), Path("/dev"))):
        return None
    return target


def _bind_home(home: Path, writable: list[Path]) -> Path:
    """C5/C13: the agent home must sit inside a grant; create it host-side."""
    home = Path(home)
    if home.parent.name != AGENT_HOME_DIR or home.parent.parent.name != "scratch":
        raise SandboxError(f"agent home must be <run>/scratch/{AGENT_HOME_DIR}/<agent>: {home}")
    resolved = home.parent.parent.parent
    grants = [Path(entry).resolve() for entry in writable]
    if not any(g in [*home.parents] for g in grants):
        raise SandboxError(f"agent home {home} lies outside every writable grant")
    return agent_home(resolved, home.name)


def _scratch_pins(home: Path | None, mask: list[Path], grants: list[Path]) -> list[Path]:
    """F1: the run's scratch dirs, to be bound onto themselves so they are mount points.

    A mount point cannot be renamed or removed from inside the sandbox, so an agent
    cannot move `scratch` aside and plant files in a sibling home's new tree. Each
    pinned scratch must be a real directory lying inside a writable grant (C5); a
    missing one is created inside its grant.
    """
    wanted: list[Path] = []
    if home is not None:
        wanted.append(home.parent.parent)                       # <run>/scratch
    for path in mask:
        unresolved = Path(path)
        if unresolved.name == AGENT_HOME_DIR and unresolved.parent.name == "scratch":
            # N2: a symlinked scratch is refused before resolve() can follow it, as in the home case.
            try:
                link = unresolved.parent.lstat()
            except FileNotFoundError:
                link = None
            except OSError as exc:
                raise SandboxError(f"cannot inspect scratch {unresolved.parent}: {exc}") from exc
            if link is not None and stat.S_ISLNK(link.st_mode):
                raise SandboxError(f"scratch must be a real directory, refused: {unresolved.parent}")
        resolved = unresolved.resolve()
        if resolved.name == AGENT_HOME_DIR and resolved.parent.name == "scratch":
            wanted.append(resolved.parent)
    pins: list[Path] = []
    for scratch in dict.fromkeys(wanted):
        if not any(scratch == g or g in scratch.parents for g in grants):
            raise SandboxError(f"scratch {scratch} lies outside every writable grant")
        try:
            info = scratch.lstat()
        except FileNotFoundError:
            try:
                os.mkdir(scratch, 0o755)
                info = scratch.lstat()
            except OSError as exc:                              # N4: a refusal, not a raw OSError
                raise SandboxError(f"cannot create scratch {scratch}: {exc}") from exc
        except OSError as exc:
            raise SandboxError(f"cannot inspect scratch {scratch}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SandboxError(f"scratch must be a real directory, refused: {scratch}")
        pins.append(scratch.resolve())
    return pins


def _hidden_args(hidden: tuple[Path, ...], grants: list[Path]) -> list[str]:
    """C14/S2: overlay each hidden path: a directory becomes a tmpfs, anything else /dev/null.

    Fails closed on a symlink, a missing path, or a hidden path that a writable grant
    contains (the grant's bind would re-expose it, since writable binds come later).
    """
    out: list[str] = []
    for path in hidden:
        target = Path(path)
        if any(g == target or g in target.parents for g in grants):
            raise SandboxError(f"protected path {target} lies inside a writable grant; refused")
        try:
            info = target.lstat()
        except OSError as exc:
            raise SandboxError(f"hidden path cannot be checked, refused: {target}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SandboxError(f"hidden path is a symbolic link, refused: {target}")
        if stat.S_ISDIR(info.st_mode):
            out += ["--tmpfs", str(target)]
        else:
            out += ["--ro-bind", "/dev/null", str(target)]
    return out


def wrap(argv: list[str], *, writable: list[Path], cwd: Path,
         mask: list[Path] | None = None, layout: Layout | None = None) -> list[str]:
    """`argv` rewritten to run under bubblewrap, seeing only the allow-list root.

    The root is the read-only system set (D1), then `layout.readable`, then
    `layout.links`, then the writable grants, then the masks, then the agent
    home (C3). There is no `--ro-bind / /`; `layout=None` means the system set
    plus the writable grants only.

    As a side effect, creates missing writable directories and the agent home
    on the host (C5/A12): bubblewrap refuses to bind a source that does not
    exist. Each `mask` is overlaid with an empty tmpfs. The mask comes after
    the writable binds because bwrap applies operations in order.
    """
    _validate_writable(writable)
    if not available():
        raise SandboxUnavailable(f"bubblewrap is not installed; {INSTALL_HINT}")
    layout = layout if layout is not None else Layout()
    bwrap = shutil.which(BWRAP)
    if bwrap is None:
        raise SandboxUnavailable(f"bubblewrap is not installed; {INSTALL_HINT}")
    out = [os.path.abspath(bwrap), *_system_args(RESOLV_CONF)]    # F10 / A13: absolute
    grants: list[Path] = []
    for path in writable:
        resolved = Path(path).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        grants.append(resolved)
    readable: list[Path] = []
    for path in layout.readable:
        resolved = Path(path).resolve()
        if not resolved.exists():
            raise SandboxError(f"readable path does not exist: {path}")
        check_not_denied(resolved, kind="readable")
        if any(g == resolved or g in resolved.parents for g in grants):
            # A grant bind over this path would make it writable; a read-only grant must not be.
            raise SandboxError(f"readable path {resolved} lies inside a writable grant; "
                               "refused, it would not be read-only")
        readable.append(resolved)
        out += ["--ro-bind", str(resolved), str(resolved)]
    installs: list[Path] = []
    for path in layout.installs:                              # S3: install roots, read-only (A1)
        resolved = Path(path).resolve()
        if not resolved.exists():
            raise SandboxError(f"install root does not exist: {path}")
        check_not_denied(resolved, kind="install")
        if any(g == resolved or g in resolved.parents for g in grants):
            raise SandboxError(f"install root {resolved} lies inside a writable grant; "
                               "refused, it would not be read-only")
        installs.append(resolved)
        out += ["--ro-bind", str(resolved), str(resolved)]
    for link, target in layout.links:
        out += ["--symlink", target, str(link)]
    out += _hidden_args(layout.hidden, grants)               # C14: before the writable binds
    for resolved in grants:
        out += ["--bind", str(resolved), str(resolved)]
    # The home is created host-side here, before its binds are emitted (A12).
    home = _bind_home(layout.home, writable) if layout.home is not None else None
    for scratch in _scratch_pins(home, list(mask or []), grants):
        out += ["--bind", str(scratch), str(scratch)]            # F1: a mount point inside
    for path in mask or []:
        out += ["--tmpfs", str(Path(path).resolve())]
    if home is not None:
        out += ["--bind", str(home), str(home)]
    for source, dest, bind_writable in layout.binds:       # S4 (C10): credentials, last
        out += _bind_args(source, dest, bind_writable, home)
    work = Path(cwd).resolve()
    bound = [*readable, *installs, *grants, *([home] if home is not None else [])]
    if not any(work == b or b in work.parents for b in bound):
        raise SandboxError(f"cwd {work} is outside every path the sandbox grants")
    out += ["--chdir", str(work), "--unshare-pid", "--die-with-parent", "--"]
    return out + list(argv)



def probe_dir(writable: list[Path]) -> Path:
    """A host directory that is writable here but granted to nobody inside.

    The negative control has to live somewhere the sandboxed process must not
    be able to write, and that this process *can* write — otherwise the probe's
    later absence proves nothing. `$HOME` is the natural first choice, but a
    coordinator that is itself sandboxed sees a read-only `$HOME`, so the
    location is derived rather than hardcoded: the first candidate that is a
    writable directory and lies outside every writable grant wins. A parent of
    a grant qualifies — binding `workspace/<slug>` does not make `workspace/`
    writable — which is what lets a sandboxed coordinator dispatch at all.
    """
    grants = [Path(entry).resolve() for entry in writable]

    def inside_a_grant(candidate: Path) -> bool:
        return any(candidate == grant or grant in candidate.parents for grant in grants)

    candidates: list[Path] = [Path.home().resolve()]
    for grant in grants:
        candidates.extend(grant.parents)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if inside_a_grant(candidate):
            continue
        if candidate.is_dir() and os.access(candidate, os.W_OK):
            return candidate

    raise SandboxError(
        "confinement cannot be verified: no writable directory outside the "
        f"sandbox's writable set ({', '.join(str(g) for g in grants)}) could be "
        "found to host the probe")


def verify(writable: list[Path], cwd: Path, *, mask: list[Path] | None = None,
           layout: Layout | None = None) -> None:
    """Prove the sandbox confines before trusting it; raise if it does not.

    Runs a throwaway process inside the sandbox that tries to write into a
    directory outside every grant (see `probe_dir`). Proves three things: the
    probe location is actually writable on the host (so absence means
    confinement, not just permission denied); the sandboxed command ran (with a
    sentinel file in a granted path); and the forbidden write was blocked.
    Raises if any check fails.

    `mask` and `layout` must be the SAME values the launch receives, so the
    proof covers the layout that will actually run (A11).

    Both control files carry a per-call unique name: several verify() calls can
    run concurrently in one process, and a name keyed on the pid would let them
    clobber each other into false passes and false refusals alike.

    Requires at least one writable path to host a control file.
    """
    if not writable:
        raise SandboxError("verify() requires at least one writable path for the control")

    _validate_writable(writable)

    tag = uuid4().hex
    probe = probe_dir(writable) / f".scieflow-sandbox-probe-{tag}"
    sentinel = Path(writable[0]).resolve() / f".scieflow-verify-sentinel-{tag}"
    extra: dict = {}
    if mask is not None:
        extra["mask"] = mask
    if layout is not None:
        extra["layout"] = layout

    try:
        # Positive control 1: prove the probe location is writable on the host.
        # If this fails, we cannot draw any conclusion from its later absence.
        try:
            probe.unlink(missing_ok=True)
            probe.touch()
            probe.unlink()
        except OSError as exc:
            raise SandboxUnavailable(
                f"confinement could not be verified because the probe location "
                f"is not writable: {exc}") from exc

        # Positive control 2: run a command that writes a sentinel into the granted path,
        # then tries to write to the forbidden path. If the sentinel doesn't appear,
        # the sandboxed command never ran.
        sentinel.unlink(missing_ok=True)
        script = (f"touch {shlex.quote(str(sentinel))} && "
                  f"touch {shlex.quote(str(probe))} 2>/dev/null; true")
        try:
            subprocess.run(wrap(["/bin/sh", "-c", script], writable=writable, cwd=cwd, **extra),
                           capture_output=True, timeout=VERIFY_TIMEOUT_S, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxUnavailable(f"the sandbox probe could not run: {exc}") from exc

        # Check positive control 2: did the sandboxed command actually run?
        if not sentinel.exists():
            raise SandboxUnavailable(
                "the sandbox did not run the probe at all — refusing to dispatch")

        # Positive control 3: was the forbidden write blocked?
        if probe.exists():
            raise SandboxUnavailable(
                f"the sandbox did not block a write to {probe.parent} — "
                "refusing to dispatch")
    finally:
        # Clean up on every path through this function, defensively.
        try:
            probe.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            sentinel.unlink(missing_ok=True)
        except Exception:
            pass


ALLOWLIST_FILE = "config/sandbox.yml"


CACHE_DIRNAME = ".uv-cache"


def cache_dir(base: Path) -> Path:
    """This dispatch's own uv cache, inside the area it may already write.

    `uv run` fails outright without a writable cache, so every agent that runs
    a `scieflow` command needs one — but the shared host cache cannot be it.
    uv hardlinks cache files into `.venv`, so a dispatch that could write the
    shared cache would mutate the host virtualenv in place, and the next
    `uv run` on the host would execute whatever the agent put there. A cache
    per dispatch costs a few tens of kilobytes and closes that path.
    """
    return Path(base).resolve() / CACHE_DIRNAME


def environment(base: Path, env: dict | None = None) -> dict:
    """`env` (default: this process's) with uv pointed at the dispatch's cache."""
    out = dict(os.environ if env is None else env)
    out["UV_CACHE_DIR"] = str(cache_dir(base))
    return out


def agent_home_path(run_dir: Path, agent: str) -> Path:
    """`<run>/scratch/agent-home/<agent>`, computed read-only (A12).

    Every existing component must be a real directory: a symlink is refused and
    never followed, so nothing outside the run can be chmod-ed or populated.
    """
    _check_agent_name(agent)
    run = Path(run_dir).resolve()
    scratch = run / "scratch"
    home_root = scratch / AGENT_HOME_DIR
    home = home_root / agent
    for part in (scratch, home_root, home):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SandboxError(f"cannot inspect agent home component {part}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SandboxError(f"agent home component is a symbolic link, refused: {part}")
        if not stat.S_ISDIR(info.st_mode):
            raise SandboxError(f"agent home component is not a directory: {part}")
    return home


def _restrict_home(home: Path) -> None:
    """C5: mode 0700 on the directory opened here, never on a path a link could redirect."""
    try:
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise SandboxError(f"agent home is not a real directory, refused: {home}: {exc}") from exc
    try:
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def agent_home(run_dir: Path, agent: str) -> Path:
    """Create (if needed) and return the agent's private home, mode 0700 (C5).

    Components are created one at a time and re-checked with lstat, so a link
    planted between the check and the mkdir is still refused, not followed.
    """
    home = agent_home_path(run_dir, agent)
    for part, mode in ((home.parent.parent, 0o755), (home.parent, 0o755), (home, 0o700)):
        try:
            os.mkdir(part, mode)
        except FileExistsError:
            pass
        except OSError as exc:
            raise SandboxError(f"cannot create agent home component {part}: {exc}") from exc
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SandboxError(f"agent home component is not a real directory: {part}")
    _restrict_home(home)
    return home


def _python_runtime() -> list[Path]:
    """C4: the interpreter that dispatches, plus the editable-install roots it sees."""
    out = [Path(sys.prefix).resolve(), Path(os.path.realpath(sys.base_prefix))]
    purelib = sysconfig.get_paths().get("purelib")
    site = Path(purelib) if purelib else None
    if site is not None and site.is_dir():
        for pth in sorted(site.glob("*.pth")):
            try:
                lines = pth.read_text().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line in lines:
                entry = line.strip()
                if not entry or entry.startswith(("#", "import ")):
                    continue
                candidate = Path(entry) if Path(entry).is_absolute() else site / entry
                if candidate.is_dir():
                    out.append(candidate.resolve())
    return [p for p in out if p.is_dir()]


def _scieflow_root() -> Path:
    """The source checkout of the running scieflow package, else its install dir."""
    import scieflow
    pkg = Path(scieflow.__file__).resolve().parent
    if pkg.parent.name == "src" and (pkg.parent.parent / "pyproject.toml").is_file():
        return pkg.parent.parent
    return pkg.parent


def _launcher(name: str) -> tuple[Path | None, list[tuple[Path, str]], Path | None]:
    """A host launcher's directory, its symlink hops, and its final file (C7)."""
    which = shutil.which(name)
    if which is None:
        return None, [], None
    path = Path(os.path.abspath(which))
    hops: list[tuple[Path, str]] = []
    for _ in range(_MAX_LINK_HOPS):
        if not path.is_symlink():
            break
        target = os.readlink(path)
        hops.append((path, target))
        path = Path(os.path.normpath(target if Path(target).is_absolute() else path.parent / target))
    return Path(os.path.abspath(which)).parent, hops, Path(os.path.realpath(which))


def _secret_masks(root: Path) -> list[Path]:
    """C14: the project-root secret files that exist now, masked from every dispatch.

    `.env`, `.env.*` (but not `.env_template`) and `.dvc/config.local`. A symlink is
    refused, never followed: the overlay would otherwise land on its target.
    """
    if (root / ".dvc").is_symlink():
        raise SandboxError(f"project .dvc is a symbolic link; secret masks cannot be proven: {root / '.dvc'}")
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        raise SandboxError(f"cannot list project root {root}: {exc}") from exc
    found = [root / name for name in names
             if name == ".env" or (name.startswith(".env.") and name != ".env_template")]
    local = root / ".dvc" / "config.local"
    if os.path.lexists(local):
        found.append(local)
    for path in found:
        if os.path.islink(path):
            raise SandboxError(f"project secret file is a symbolic link, refused: {path}")
    return found


def _other_run_homes(root: Path, run: Path) -> list[Path]:
    """S2 (S1 review F6): every existing agent home of a run other than this dispatch's.

    Listed with lstat only: a symlinked `scratch` or `agent-home` is skipped, and its
    target is never read (the link itself stays visible, as the plan records).
    """
    ws = root / "workspace"
    try:
        info = ws.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise SandboxError(f"cannot inspect workspace {ws}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SandboxError(f"workspace is a symbolic link; other runs cannot be hidden: {ws}")
    if not stat.S_ISDIR(info.st_mode):
        return []
    out: list[Path] = []
    try:
        entries = sorted(os.scandir(ws), key=lambda e: e.name)
    except OSError as exc:
        raise SandboxError(f"cannot list workspace {ws}: {exc}") from exc
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        other = ws / entry.name
        if other.resolve() == run:
            continue
        scratch = other / "scratch"
        try:
            scratch_info = scratch.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(scratch_info.st_mode):
            continue
        home_root = scratch / AGENT_HOME_DIR
        try:
            home_info = home_root.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(home_info.st_mode):
            out.append(home_root)
    return out


def _readable_of(data: dict, root: Path, path: Path) -> tuple[Path, ...]:
    """C8/D5: the `readable:` grants, each refused on any grammar or deny-table failure.

    Entries: a mapping with a non-empty `path:` (absolute, `~/`, or relative to the
    project root; it must exist) and a non-empty `reason:`. The deny table applies
    here at parse time and again in wrap() on each bind source.
    """
    entries = data.get("readable")
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise SandboxError(
            f"allowlist 'readable:' must be a list, not {type(entries).__name__}: {path}")
    root = root.resolve()
    out: list[Path] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SandboxError(
                f"readable entry {i} must be a mapping with 'path:' and 'reason:', "
                f"not {type(entry).__name__}: {path}")
        raw = entry.get("path")
        if not isinstance(raw, str) or not raw:
            raise SandboxError(f"readable entry {i} has no 'path:' or an empty path: {path}")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise SandboxError(
                f"readable entry {raw!r} needs a 'reason:' saying why the agent may read it: {path}")
        if raw.startswith("~/"):
            candidate = Path.home() / raw[2:]
        elif Path(raw).is_absolute():
            candidate = Path(raw)
        else:
            candidate = root / raw
        if not candidate.exists():
            raise SandboxError(f"readable path does not exist: {raw!r} ({candidate})")
        check_not_denied(candidate, kind="readable")
        out.append(candidate.resolve())
    return tuple(dict.fromkeys(out))


def read_policy(project: Project) -> dict:
    """The one captured mapping of config/sandbox.yml a dispatch decides from (A2)."""
    return _load_allowlist(project)


def readable_paths(project: Project, *, policy: dict | None = None) -> tuple[Path, ...]:
    """The validated `readable:` grants of config/sandbox.yml (S2)."""
    data = _load_allowlist(project) if policy is None else policy
    return _readable_of(data, project.root, project.root / ALLOWLIST_FILE)


def agent_cli_index(argv) -> int:
    """C7/A7: the index of the agent CLI in `argv`: 0, or the first command after an `env` prefix.

    An `env` prefix may carry only NAME=VALUE operands. An option (-i, -S, -u, --, ...)
    changes what runs, so it is refused, as is a prefix with no command.
    """
    if not argv or type(argv[0]) is not str:
        raise SandboxError("agent command is empty")
    if Path(argv[0]).name != "env":
        return 0
    index = 1
    while index < len(argv) and _ENV_ASSIGNMENT.match(argv[index]):
        index += 1
    if index >= len(argv):
        raise SandboxError("env prefix names no command to run")
    if argv[index].startswith("-"):
        raise SandboxError(f"env prefix option {argv[index]!r} is not allowed; "
                           "use NAME=VALUE operands only")
    return index


def host_launcher(token: str, cwd: Path) -> Path | None:
    """The agent CLI as the host resolves it, spelled absolutely and not resolved (C6/C7).

    Mirrors `agent_run._capture_execution`: a bare name is searched on PATH, a path with a
    slash is taken relative to `cwd`. None when no executable file is found.
    """
    if not token or "\0" in token:
        return None
    if "/" in token:
        located = Path(token) if Path(token).is_absolute() else Path(cwd) / token
        if located.is_file() and os.access(located, os.X_OK):
            return located.absolute()
        return None
    found = shutil.which(token)
    return Path(found).absolute() if found else None


def _expand_links(path: Path) -> tuple[list[tuple[Path, str]], Path]:
    """Every symlink on the way to an absolute `path`, directory components included (C7).

    Returns the hops, (link path, target text), and the real location of the final file.
    A directory symlink in the middle of the path (codex's `current`) is a hop too: the
    sandbox must recreate it, or the launcher dangles. Bounded by _MAX_LINK_HOPS.
    """
    hops: list[tuple[Path, str]] = []
    current = Path(path.anchor)
    pending = list(reversed(path.parts[1:]))
    while pending:
        part = pending.pop()
        if part == "..":
            current = current.parent
            continue
        candidate = current / part
        if not candidate.is_symlink():
            current = candidate
            continue
        if len(hops) >= _MAX_LINK_HOPS:
            raise SandboxError(f"too many symbolic links behind {path}")
        target = os.readlink(candidate)
        hops.append((candidate, target))
        if Path(target).is_absolute():
            current = Path(current.anchor)
            pending.extend(reversed(Path(target).parts[1:]))
        else:
            pending.extend(reversed(Path(target).parts))
    return hops, current


def _install_root(final: Path) -> Path:
    """C7/A17: the first of the two ancestors of the final file whose parent is `versions` or
    `releases` is the install root (a directory); otherwise the final file itself."""
    for ancestor in (final.parent, final.parent.parent):
        if ancestor.parent.name in ("versions", "releases"):
            return ancestor
    return final


def dispatch_layout(project: Project, *, run_dir: Path, agent: str,
                    readable: tuple[Path, ...] = (), launcher: Path | None = None) -> Layout:
    """The layout for one agent dispatch: project, Python, ScieFlow, uv, `readable:`, own home.

    Reads only (stat/lstat/readlink/listdir), so a resolution can happen before any write.
    The project root is bound read-only (D3); the writable run is bound over it.
    The interpreter's symlinked ancestors are recreated as links unless a bound
    path already shows them, so `sys.executable` resolves inside the sandbox (C4).
    `readable` is the validated `readable:` tuple captured from the one policy read
    (A2); it is appended after the de-duplication (A10).
    C14 and S2 add the hidden paths: the project's secret files and every other run's
    agent home.
    """
    check_agent_name(agent)
    run = Path(run_dir).resolve()
    home = agent_home_path(run, agent)
    root = project.root.resolve()
    launcher_dir, hops, uv_final = _launcher("uv")
    candidates = [root, *_python_runtime(), _scieflow_root().resolve()]
    if uv_final is not None:
        candidates.append(uv_final)
    base = tuple(p for p in _outermost(candidates) if not _in_system_set(p))
    for entry in base:
        check_not_denied(entry, kind="readable")
    extra = [p for p in dict.fromkeys(Path(r) for r in readable)
             if not any(p == b or b in p.parents for b in base)]
    granted = tuple([*base, *_outermost(extra)])
    cli_hops: list[tuple[Path, str]] = []
    cli_dir: Path | None = None
    installs: list[Path] = []
    if launcher is not None:
        located = Path(launcher)
        if not located.is_absolute():
            raise SandboxError(f"agent launcher must be an absolute path: {located}")
        cli_hops, final = _expand_links(located)
        cli_root = _install_root(final)
        check_not_denied(cli_root, kind="install")              # A1, before any use of the root
        cli_dir = located.parent
        if not _in_system_set(cli_root) and not any(
                cli_root == g or g in cli_root.parents for g in granted):
            installs.append(cli_root)
    installs_t = tuple(installs)
    interpreter = Path(os.path.abspath(sys.executable))
    links = [*hops, *cli_hops,
             *((p, os.readlink(p)) for p in reversed(interpreter.parents) if p.is_symlink())]
    inside = (*granted, *installs_t)
    kept_links: list[tuple[Path, str]] = []
    for link, target in links:
        covered = _in_system_set(link) or any(link == r or r in link.parents for r in inside)
        if not covered and link not in [k for k, _ in kept_links]:
            kept_links.append((link, target))
    hidden = [*_secret_masks(root), *_other_run_homes(root, run)]
    dirs = [d for d in (launcher_dir, cli_dir) if d is not None]
    return Layout(readable=granted, links=tuple(kept_links), home=home,
                  launcher_dirs=tuple(dict.fromkeys(dirs)),
                  hidden=tuple(dict.fromkeys(hidden)), installs=installs_t)


def _launcher_path_needed(directory: Path, layout: Layout) -> bool:
    """A launcher dir joins PATH only when no bound system or readable path covers it (A17)."""
    if str(directory) in SYSTEM_PATH or _in_system_set(directory):
        return False
    return not any(directory == r or r in directory.parents for r in (*layout.readable, *layout.installs))


def dispatch_environment(base: Path, *, layout: Layout, env: dict | None = None,
                         credential_env: dict | None = None) -> dict:
    """The C12 allow-list environment: HOME = the agent home, PATH = the sandbox PATH (C6).

    Every inherited variable not on the allow-list is dropped (D7, C12). What `environment()`
    itself adds is kept, so `environment(base)` stays the one place that builds the base.
    `credential_env` holds only this agent's own token variable (C10, attach_credential).
    """
    inherited = os.environ if env is None else env
    out = environment(base) if env is None else environment(base, env)
    for name in inherited:
        if not _c12_kept(name):
            out.pop(name, None)
    out.update(credential_env or {})
    if layout.home is not None:
        out["HOME"] = str(layout.home)
    dirs = [str(d) for d in layout.launcher_dirs if _launcher_path_needed(d, layout)]
    out["PATH"] = ":".join([*dict.fromkeys(dirs), *SYSTEM_PATH])
    return out


def _load_allowlist(project: Project) -> dict:
    """config/sandbox.yml as a mapping, or {} when it is absent or empty.

    The file lives in config/, which is never writable inside a sandbox, so an
    agent cannot extend its own permissions — every entry is a human edit.
    """
    path = project.root / ALLOWLIST_FILE
    if not path.exists():
        return {}

    try:
        return _policy_mapping(path.read_text(), path)
    except (OSError, yaml.YAMLError) as exc:
        raise SandboxError(
            f"allowlist file is malformed YAML: {path}\n{exc}") from exc

def _policy_mapping(raw: bytes | str, path: Path) -> dict:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SandboxError(f"allowlist file is malformed YAML: {path}\n{exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SandboxError(f"allowlist file must be a mapping, not {type(data).__name__}: {path}")
    return data


def unsandboxed_runs(project: Project, *, policy: dict | None = None) -> set[str]:
    """Run slugs config/sandbox.yml permits to dispatch unsandboxed.

    The per-run escape hatch lives here rather than in the run's own
    `config.yml`, because that file is inside the dispatch's writable bind: an
    agent could append `sandbox: off` and, with `agent_overrides:` in the same
    file, choose what the next unconfined dispatch runs. Here it is a human
    decision made outside every run, which is what makes the `sandbox.disabled`
    event's `actor: human` true. `policy` is the mapping of one captured read.
    """
    path = project.root / ALLOWLIST_FILE
    return _unsandboxed_of(_load_allowlist(project) if policy is None else policy, path)


def _unsandboxed_of(data: dict, path: Path) -> set[str]:
    entries = data.get("unsandboxed_runs")
    if entries is None:
        return set()

    if not isinstance(entries, list):
        raise SandboxError(
            f"allowlist 'unsandboxed_runs:' must be a list, not "
            f"{type(entries).__name__}: {path}")

    out: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SandboxError(
                f"unsandboxed_runs entry {i} must be a mapping with a 'slug:' key, "
                f"not {type(entry).__name__}: {path}")
        raw = entry.get("slug")
        if not raw or not isinstance(raw, str):
            raise SandboxError(
                f"unsandboxed_runs entry {i} has no 'slug:' or an empty slug: {path}")
        if "/" in raw or raw in {".", ".."}:
            raise SandboxError(
                f"unsandboxed_runs entry {raw!r} must name one run slug, not a path: {path}")
        if not entry.get("reason"):
            raise SandboxError(
                f"unsandboxed_runs entry {raw!r} needs a 'reason:' saying why this "
                f"run may run unconfined: {path}")
        out.add(raw)

    return out


def allowlist_paths(project: Project, *, policy: dict | None = None) -> list[Path]:
    """Extra writable paths from config/sandbox.yml, deny-by-default."""
    path = project.root / ALLOWLIST_FILE
    data = _load_allowlist(project) if policy is None else policy
    return _allowlist_of(data, project.root, path)


def _allowlist_of(data: dict, root: Path, path: Path) -> list[Path]:
    writable = data.get("writable")
    if writable is None:
        return []

    if not isinstance(writable, list):
        raise SandboxError(
            f"allowlist 'writable:' must be a list, not {type(writable).__name__}: {path}")

    root = root.resolve()
    forbidden = {root, (root / "config").resolve(), (root / "src").resolve()}
    out: list[Path] = []

    for i, entry in enumerate(writable):
        if not isinstance(entry, dict):
            raise SandboxError(
                f"allowlist entry {i} must be a mapping with a 'path:' key, not {type(entry).__name__}: {path}")

        raw = entry.get("path")
        if not raw:
            raise SandboxError(
                f"allowlist entry {i} has no 'path:' or an empty path: {path}")

        candidate = (root / str(raw)).resolve()
        if candidate in forbidden:
            raise SandboxError(
                f"allowlist entry {raw!r} would grant the whole repository, all of "
                "config/ or all of src/; grant the specific directory instead")
        if root not in candidate.parents:
            raise SandboxError(f"allowlist entry {raw!r} escapes the repository")
        if candidate == (root / ALLOWLIST_FILE).resolve():
            raise SandboxError("the allowlist cannot grant write access to itself")
        out.append(candidate)

    return out


# --- S4: per-agent credentials and the environment allow-list (C10-C12, A5, A8) -----

def credentials_dir() -> Path:
    """`~/.config/scieflow/credentials`. Under test the suite points it at a tmp dir (A9)."""
    if CREDENTIALS_OVERRIDE is not None:
        return Path(CREDENTIALS_OVERRIDE)
    return Path.home() / ".config" / "scieflow" / "credentials"


def _c12_kept(name: str) -> bool:
    """C12: a name the dispatch environment keeps from the host (PATH and HOME are reset later)."""
    return name in C12_KEPT or name.startswith("LC_")


@dataclass(frozen=True, slots=True)
class Credential:
    """One parsed `credentials:` entry. Token-free: a token value is read only by read_token().

    env: `var` is the variable set to the token, `file` the token file (not opened here).
    bind: `source` is the host file or dir, `target` its path inside the agent home.
    """
    agent: str
    mode: str
    var: str | None = None
    file: Path | None = None
    source: Path | None = None
    target: str | None = None
    writable: bool = False
    reason: str = ""

    def __post_init__(self):
        if self.mode not in ("env", "bind"):
            raise TypeError("credential mode must be 'env' or 'bind'")


def _only_keys(spec: dict, allowed: frozenset, where: str, path: Path) -> None:
    unknown = sorted(str(key) for key in spec if key not in allowed)
    if unknown:
        raise SandboxError(f"{where} has unknown key(s) {', '.join(unknown)}: {path}")


def _credentials_of(data: dict, path: Path, *, check_sources: bool = True) -> tuple[Credential, ...]:
    """C10/A8: the `credentials:` grammar, parsed from the one captured mapping (A2).

    check_sources=False (doctor only) skips the bind-source existence and deny checks, so a
    missing source is reported rather than refused. Every dispatch uses the default.
    """
    entries = data.get("credentials")
    if entries is None:
        return ()
    if not isinstance(entries, dict):
        raise SandboxError(f"allowlist 'credentials:' must be a mapping keyed by agent, "
                           f"not {type(entries).__name__}: {path}")
    out: list[Credential] = []
    for agent in sorted(entries, key=str):
        if type(agent) is not str:
            raise SandboxError(f"credentials key {agent!r} must be an agent name: {path}")
        check_agent_name(agent)
        spec = entries[agent]
        where = f"credentials entry {agent!r}"
        if not isinstance(spec, dict):
            raise SandboxError(f"{where} must be a mapping with 'mode:' and 'reason:': {path}")
        reason = spec.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise SandboxError(f"{where} needs a 'reason:' saying why the agent needs this login: {path}")
        mode = spec.get("mode")
        if mode == "env":
            _only_keys(spec, CREDENTIAL_ENV_KEYS, where, path)
            var = spec.get("var")
            if not isinstance(var, str) or ENV_VAR_RE.fullmatch(var) is None:
                raise SandboxError(f"{where} needs 'var:' matching [A-Z_][A-Z0-9_]*: {path}")
            if _c12_kept(var) or var.startswith("LD_"):
                raise SandboxError(f"{where}: {var} is reserved by the environment allow-list: {path}")
            raw_file = spec.get("file")
            if raw_file is None:
                token_file = credentials_dir() / f"{agent}.token"
            elif isinstance(raw_file, str) and raw_file:
                token_file = _credential_path(raw_file, credentials_dir())
            else:
                raise SandboxError(f"{where}: 'file:' must be a non-empty path: {path}")
            out.append(Credential(agent, "env", var=var, file=token_file, reason=reason))
        elif mode == "bind":
            _only_keys(spec, CREDENTIAL_BIND_KEYS, where, path)
            raw_source = spec.get("source")
            target = spec.get("target")
            writable = spec.get("writable", False)
            if not isinstance(raw_source, str) or not raw_source:
                raise SandboxError(f"{where} needs 'source:' (a host file or dir): {path}")
            if not (raw_source.startswith("~/") or Path(raw_source).is_absolute()):
                raise SandboxError(f"{where}: 'source:' must be absolute or ~/-prefixed: {path}")
            if not isinstance(target, str) or not target:
                raise SandboxError(f"{where} needs 'target:' (a path inside the agent home): {path}")
            target_path = PurePosixPath(target)
            if target_path.is_absolute() or ".." in target_path.parts or target in (".", ""):
                raise SandboxError(f"{where}: 'target:' must be relative inside the agent home: {path}")
            if type(writable) is not bool:
                raise SandboxError(f"{where}: 'writable:' must be true or false: {path}")
            source = _credential_path(raw_source, Path("/"))
            if check_sources:
                if not os.path.exists(source):
                    raise SandboxError(f"{where}: bind source does not exist: {source}")
                check_not_denied(source, kind="bind")
                source = source.resolve()
            out.append(Credential(agent, "bind", source=source, target=target,
                                  writable=writable, reason=reason))
        else:
            raise SandboxError(f"{where} needs mode 'env' or 'bind', not {mode!r}: {path}")
    return tuple(out)


def _credential_path(raw: str, base: Path) -> Path:
    if raw.startswith("~/"):
        return Path.home() / raw[2:]
    if Path(raw).is_absolute():
        return Path(raw)
    return base / raw


def credentials_of(project: Project, *, policy: dict | None = None,
                   check_sources: bool = True) -> tuple[Credential, ...]:
    """The token-free credential records of config/sandbox.yml (`policy`: one captured read)."""
    data = _load_allowlist(project) if policy is None else policy
    return _credentials_of(data, project.root / ALLOWLIST_FILE, check_sources=check_sources)


def missing_credential_message(agent: str) -> str:
    """C10: the exact setup step for an agent with no `credentials:` entry."""
    base = (f"agent {agent!r} has no `credentials:` entry in {ALLOWLIST_FILE}, and a sandboxed "
            "dispatch needs one; refused")
    if agent == "claude":
        token = credentials_dir() / "claude.token"
        return (f"{base}. Run `claude setup-token`, save the token to {token}, `chmod 600` it, "
                "and add to config/sandbox.yml under credentials: a claude entry with "
                "mode: env, var: CLAUDE_CODE_OAUTH_TOKEN, file: claude.token, and a reason")
    return (f"{base}. Add a `credentials:` entry for {agent} to {ALLOWLIST_FILE}: mode env with "
            "a 0600 token file, or mode bind with the agent's own login file, and a reason")


def read_token(cred: Credential, *, forbidden=()) -> str:
    """The env token value, read once (C10). Refused unless a private, one-line regular file.

    No refusal message carries the content. `forbidden` lists the dispatch's grants and binds:
    a token inside any of them would be readable by the agent, so it is refused.
    """
    if cred.mode != "env" or cred.file is None:
        raise SandboxError(f"credential for {cred.agent} is not an env token")
    path = Path(cred.file)
    if os.path.islink(path):
        raise SandboxError(f"token file for {cred.agent} is a symbolic link, refused: {path}")
    real = Path(os.path.realpath(path))
    for area in forbidden:
        area_path = Path(area).absolute()
        for grant in {area_path, area_path.resolve()}:
            for location in {path.absolute(), real}:
                if location == grant or grant in location.parents:
                    raise SandboxError(f"token file for {cred.agent} lies inside a grant or bind; "
                                       f"refused: {path}")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise SandboxError(f"token file for {cred.agent} is missing: {path}") from None
    except OSError as exc:
        raise SandboxError(f"token file for {cred.agent} cannot be opened safely: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SandboxError(f"token file for {cred.agent} is not a regular file: {path}")
        if info.st_uid != os.getuid():
            raise SandboxError(f"token file for {cred.agent} is not owned by this user: {path}")
        if info.st_mode & 0o077:
            raise SandboxError(f"token file for {cred.agent} is readable by group or others "
                               f"(mode {stat.S_IMODE(info.st_mode):o}); chmod 600 it: {path}")
        if not 0 < info.st_size <= CREDENTIAL_TOKEN_MAX:
            raise SandboxError(f"token file for {cred.agent} must hold 1 to "
                               f"{CREDENTIAL_TOKEN_MAX} bytes: {path}")
        chunks: list[bytes] = []
        total = 0
        while total <= CREDENTIAL_TOKEN_MAX:
            chunk = os.read(fd, CREDENTIAL_TOKEN_MAX + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    if len(raw) > CREDENTIAL_TOKEN_MAX:
        raise SandboxError(f"token file for {cred.agent} grew past {CREDENTIAL_TOKEN_MAX} bytes: {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SandboxError(f"token file for {cred.agent} is not UTF-8 text: {path}") from None
    if text.endswith("\n"):
        text = text[:-1]
    if not text or "\n" in text or "\r" in text or "\0" in text:
        raise SandboxError(f"token file for {cred.agent} must hold exactly one line: {path}")
    return text


def _check_prefix(argv) -> None:
    """A7: with a credential, an `env` prefix may set only MAX_THINKING_TOKENS."""
    if not argv or Path(argv[0]).name != "env":
        return
    index = agent_cli_index(argv)
    for operand in argv[1:index]:
        name = operand.split("=", 1)[0]
        if name != "MAX_THINKING_TOKENS":
            raise SandboxError(f"an env prefix may set only MAX_THINKING_TOKENS when a credential "
                               f"is handed over, not {name}; refused")


def attach_credential(layout: Layout, credentials: tuple, *, agent: str, argv, located: Path | None,
                      writable, root: Path) -> tuple[Layout, dict]:
    """Hand a sandboxed agent its one credential, or refuse (C10, C11, A7).

    Returns (layout, env additions). A missing entry is refused with the exact setup step. A CLI
    that lies inside a writable grant is refused (C11). env mode reads the token here, once, and
    returns it as the only addition; bind mode adds one bind to the layout instead.
    """
    check_agent_name(agent)
    cred = next((c for c in credentials if c.agent == agent), None)
    if cred is None:
        raise SandboxError(missing_credential_message(agent))
    _check_prefix(argv)
    if located is None:
        raise SandboxError(f"agent {agent!r} CLI {argv[0]!r} cannot be found on the host; refused")
    grants = [Path(entry).resolve() for entry in writable]
    for where in {located.absolute(), Path(os.path.realpath(located))}:
        if any(where == grant or grant in where.parents for grant in grants):
            raise SandboxError(f"agent {agent!r} CLI {where} lies inside a writable grant; "
                               "a credential is refused for it (C11)")
    if cred.mode == "bind":
        if layout.home is None:
            raise SandboxError(f"agent {agent!r} has no home to bind its credential into")
        dest = layout.home / cred.target
        return replace(layout, binds=(*layout.binds, (cred.source, dest, cred.writable))), {}
    forbidden = [Path(root), *writable, *layout.readable, *(bind[0] for bind in layout.binds)]
    return layout, {cred.var: read_token(cred, forbidden=forbidden)}


def _bind_args(source: Path, dest: Path, writable_bind: bool, home: Path | None) -> list[str]:
    """S4 bind (C10): the source read-only (or writable), at a target inside the agent home.

    No symlink may appear in the target on the host; the source must pass the deny table
    (kind "bind"). On the host the run keeps at most an empty mountpoint.
    """
    if home is None:
        raise SandboxError("a credential bind needs the agent home")
    dest = Path(dest)
    try:
        rel = dest.relative_to(home)
    except ValueError:
        raise SandboxError(f"bind target {dest} lies outside the agent home {home}") from None
    current = home
    for part in rel.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode):
            raise SandboxError(f"bind target has a symbolic link on the host, refused: {current}")
    if not os.path.exists(source):
        raise SandboxError(f"bind source does not exist: {source}")
    check_not_denied(Path(source), kind="bind")
    return ["--bind" if writable_bind else "--ro-bind", str(Path(source).resolve()), str(dest)]


def _status_of(cred: Credential) -> tuple[str, str]:
    """A11: stat only. The file is never opened, read or hashed."""
    if cred.mode == "bind":
        present = cred.source is not None and os.path.exists(cred.source)
        return ("present" if present else "missing"), str(cred.source)
    path = cred.file
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing", str(path)
    except OSError:
        return "unreadable", str(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return "bad-mode", f"not a regular file: {path}"
    if (info.st_mode & 0o077 or info.st_uid != os.getuid()
            or not 0 < info.st_size <= CREDENTIAL_TOKEN_MAX):
        return "bad-mode", f"needs a private 0600 one-line file: {path}"
    return "present", str(path)


def doctor_lines(project: Project) -> list[str]:
    """A11: one line per configured credential: its status from stat only, never content or a hash."""
    try:
        creds = credentials_of(project, check_sources=False)
    except SandboxError as exc:
        return [f"policy: invalid, {exc}"]
    if not creds:
        return [f"no credentials: entries in {ALLOWLIST_FILE}"]
    lines = []
    for cred in creds:
        state, detail = _status_of(cred)
        lines.append(f"{cred.agent}: {cred.mode} {state} ({detail})")
    return lines


def main(argv: list[str] | None = None) -> int:
    """`python -m scieflow.core.sandbox doctor`: the credential status of this project (A11)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args != ["doctor"]:
        print("usage: python -m scieflow.core.sandbox doctor", file=sys.stderr)
        return 2
    for line in doctor_lines(Project.discover()):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def writable_for(project: Project, *, run_dir: Path | None,
                 coordinator: bool, policy: dict | None = None) -> list[Path]:
    """What this dispatch may write. The caller declares which kind it is —
    the sandbox never guesses, because guessing wrong widens the boundary.
    `policy`, when given, is the one captured read of config/sandbox.yml (A2)."""
    if coordinator:
        base = [project.workspace_root]
    elif run_dir is None:
        raise SandboxError(
            "a sandboxed agent dispatch must name the run it belongs to; "
            "use --no-sandbox for a one-off dispatch outside a run")
    else:
        base = [Path(run_dir)]
    return [*base, *allowlist_paths(project, policy=policy)]


@dataclass(frozen=True, slots=True)
class ProtectionPath:
    """Lexical lstat identity, separately from canonical target identity."""
    path: Path
    resolved: Path
    exists: bool
    device: int | None
    inode: int | None
    mode: int | None
    size: int | None
    mtime_ns: int | None
    ctime_ns: int | None
    link_target: str | None

    def __post_init__(self):
        for name in ('path', 'resolved'):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute():
                raise TypeError('protection paths must be absolute Path values')
        if type(self.exists) is not bool:
            raise TypeError('exists must be bool')
        for name in ('device', 'inode', 'mode', 'size', 'mtime_ns', 'ctime_ns'):
            value = getattr(self, name)
            if value is not None and type(value) is not int:
                raise TypeError('path identity must be immutable integer scalars')
            if self.exists != (value is not None):
                raise TypeError('path identity must agree with existence')
        if self.link_target is not None and type(self.link_target) is not str:
            raise TypeError('link target must be text or None')


@dataclass(frozen=True, slots=True)
class TurnProtection:
    """Independent policy/path capture, not admission or a filesystem lock."""
    project_root: Path
    run_dir: Path
    workspace_root: Path
    sandbox_enabled: bool
    writable: tuple[Path, ...] | None
    masks: tuple[Path, ...]
    allowlist: tuple[Path, ...]
    coordinator_writable: tuple[Path, ...]
    policy_sha256: str | None
    path_facts: tuple[ProtectionPath, ...]
    readable: tuple[Path, ...] = ()        # S2: from the same captured mapping (A2)
    credentials: tuple[Credential, ...] = ()   # S4: token-free records, same mapping (A2)

    def __post_init__(self):
        for name in ('project_root', 'run_dir', 'workspace_root'):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute():
                raise TypeError('protection roots must be absolute Paths')
        if type(self.sandbox_enabled) is not bool or self.sandbox_enabled != (self.writable is not None):
            raise TypeError('sandbox state and grants must agree')
        for name in ('writable', 'masks', 'allowlist', 'coordinator_writable', 'readable'):
            value = getattr(self, name)
            if name == 'writable' and value is None:
                continue
            if type(value) is not tuple or any(not isinstance(p, Path) or not p.is_absolute() for p in value):
                raise TypeError('grants and masks must be immutable absolute Path tuples')
        if not self.sandbox_enabled and self.readable:
            raise TypeError('readable grants exist only for a sandboxed run')
        if type(self.credentials) is not tuple or any(type(c) is not Credential for c in self.credentials):
            raise TypeError('credentials must be an immutable Credential tuple')
        if type(self.path_facts) is not tuple or any(type(p) is not ProtectionPath for p in self.path_facts):
            raise TypeError('path facts must be an immutable ProtectionPath tuple')
        if self.policy_sha256 is not None and (type(self.policy_sha256) is not str or
                len(self.policy_sha256) != 64 or any(c not in '0123456789abcdef' for c in self.policy_sha256)):
            raise TypeError('policy digest must be complete lowercase SHA256 or None')


def _policy_stat_identity(info: os.stat_result) -> tuple[int, ...]:
    """Policy security/content identity; reading may legitimately advance atime."""
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _capture_turn_policy(path: Path) -> tuple[dict, str | None, ProtectionPath]:
    """One bounded regular-file read. Absence is explicit, never a symlink opt-out."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        if path.is_symlink():
            raise SandboxError(f'unsafe sandbox policy: {path}') from None
        fact = _path_fact(path)
        if fact.exists:
            raise SandboxError(f'sandbox policy changed during capture: {path}')
        return {}, None, fact
    except OSError as exc:
        raise SandboxError(f'cannot capture sandbox policy: {path}') from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
            raise SandboxError(f'unprovable sandbox policy: {path}')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(1024 * 1024 + 1)
        after = os.fstat(fd)
        if (_policy_stat_identity(before) != _policy_stat_identity(after) or
                len(raw) != before.st_size or
                _policy_stat_identity(path.lstat()) != _policy_stat_identity(after)):
            raise SandboxError(f'sandbox policy changed during capture: {path}')
        fact = _path_fact(path)
        if (fact.device, fact.inode, fact.mode, fact.size, fact.mtime_ns, fact.ctime_ns) != (
                before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
            raise SandboxError(f'sandbox policy changed during capture: {path}')
        return _policy_mapping(raw, path), hashlib.sha256(raw).hexdigest(), fact
    except OSError as exc:
        raise SandboxError(f'cannot capture sandbox policy: {path}') from exc
    finally:
        os.close(fd)


def _path_fact(path: Path) -> ProtectionPath:
    try:
        resolved = path.resolve()
        try:
            info = path.lstat()
        except FileNotFoundError:
            return ProtectionPath(path, resolved, False, None, None, None, None, None, None, None)
        target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
        if target is not None and not path.exists():
            raise SandboxError(f'dangling safety alias: {path}')
        return ProtectionPath(path, resolved, True, info.st_dev, info.st_ino, info.st_mode,
                              info.st_size, info.st_mtime_ns, info.st_ctime_ns, target)
    except (OSError, RuntimeError) as exc:
        raise SandboxError(f'unprovable safety path: {path}') from exc


def _capture_path_facts(seeds: list[Path]) -> tuple[ProtectionPath, ...]:
    """Finite policy paths and alias hops, NEVER recursive directory discovery."""
    pending = list(seeds)
    visited = set()
    facts = {}
    while pending:
        seed = pending.pop()
        if seed in visited:
            continue
        visited.add(seed)
        if len(visited) > 8192:
            raise SandboxError('safety alias/path capture exceeds provable bound')
        for path in (seed, *seed.parents):
            if path not in facts:
                facts[path] = _path_fact(path)
            fact = facts[path]
            if fact.link_target is not None:
                target = Path(fact.link_target)
                if not target.is_absolute():
                    target = path.parent / target
                # Preserve intermediate aliases, suffixes and their ancestors.
                pending.append(target / seed.relative_to(path))
        resolved = seed.resolve()
        if resolved != seed:
            pending.append(resolved)
    return tuple(facts[p] for p in sorted(facts, key=str))


def resolve_turn_protection(project: Project, *, run_dir: Path,
                            prepare_home: Callable[[Path], None] | None = None) -> TurnProtection:
    """Capture policy and both grant envelopes without run/agent/charter semantics.

    `prepare_home`, when given, runs for a SANDBOXED run only, right after the one policy
    read and before any path fact is captured. The turn path passes the agent's home
    creation here (F3): the sandbox flag comes from this single read, and the directory
    exists before the admission facts record it.
    """
    try:
        root = project.root.resolve(strict=True)
        workspace = (root / 'workspace').resolve(strict=True)
        run = Path(run_dir).resolve(strict=True)
        if not root.is_dir() or not workspace.is_dir() or not run.is_dir() or run.parent != workspace:
            raise SandboxError('turn protection requires this project owning run')
        if workspace != root / 'workspace':
            raise SandboxError('workspace escapes canonical project')
        policy = root / ALLOWLIST_FILE
        data, digest, policy_fact = _capture_turn_policy(policy)
        enabled = run.name not in _unsandboxed_of(data, policy)
        readable = tuple(_readable_of(data, root, policy)) if enabled else ()
        if enabled and prepare_home is not None:
            prepare_home(run)
        allowlist = tuple(_allowlist_of(data, root, policy))
        narrowed = (run, *allowlist)
        broad = (workspace, *allowlist)
        _validate_writable(list(narrowed))
        _validate_writable(list(broad))
        masks = tuple(masks_for(list(narrowed), run_dir=run))
        originals = [root / str(e['path']) for e in data.get('writable') or []]
        seeds = [project.root.absolute(), Path(run_dir).absolute(), root, run, workspace,
                 policy, *narrowed, *broad, *masks, *originals, *readable]
        credentials = _credentials_of(data, policy) if enabled else ()
        facts = _capture_path_facts(seeds)
        if next(f for f in facts if f.path == policy) != policy_fact:
            raise SandboxError('sandbox policy changed during protection capture')
        return TurnProtection(root, run, workspace, enabled, narrowed if enabled else None,
                              masks, allowlist, broad, digest, facts, readable, credentials)
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, SandboxError):
            raise
        raise SandboxError('cannot capture turn protection') from exc
