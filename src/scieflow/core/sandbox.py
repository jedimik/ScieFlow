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
from dataclasses import dataclass
import shlex
import shutil
import subprocess
from pathlib import Path
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

    readable: bound read-only at the same path. links: (link, target) pairs,
    recreated with --symlink. home: the dispatch's own HOME, bound read-write
    after the masks. launcher_dirs: directories of bound launchers, which form
    the start of the sandbox PATH (C6).
    """
    readable: tuple[Path, ...] = ()
    links: tuple[tuple[Path, str], ...] = ()
    home: Path | None = None
    launcher_dirs: tuple[Path, ...] = ()

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


def _is_abs(value) -> bool:
    return isinstance(value, Path) and value.is_absolute()


def _check_agent_name(agent) -> None:
    if type(agent) is not str or AGENT_NAME_RE.fullmatch(agent) is None:
        raise SandboxError(f"agent name {agent!r} cannot name an agent home")


def _homes() -> list[Path]:
    """Both the HOME env and the passwd home: either may be the real one."""
    out = {Path.home().resolve()}
    try:
        import pwd
        out.add(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())
    except (ImportError, KeyError, OSError):
        pass
    return sorted(out, key=str)


def check_not_denied(path: Path, *, kind: str = "readable") -> None:
    """Refuse a path that equals, lies inside, or contains a hard-deny path (A1).

    kind "bind" may name a file inside an agent credential store (C10), so only
    that kind allows a path inside a store. Runtime sockets are refused for
    "readable" (F9) only: the resolver config ("resolv") lives under /run by design,
    so it keeps the store rules but not the socket rule.
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
                if d in c.parents and kind != "bind":
                    raise SandboxError(f"{c} lies inside the agent credential store {d}; never granted")
        if kind == "readable":
            for sock in _RUNTIME_SOCKETS:
                if c == sock or sock in c.parents or c in sock.parents:
                    raise SandboxError(f"{c} touches the runtime socket area {sock}; never readable")


def _in_system_set(path: Path) -> bool:
    return any(path == tree or tree in path.parents for tree in SYSTEM_TREES)


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
    """The resolved DNS config, when it lies outside the system set (A10)."""
    try:
        target = Path(os.path.realpath(path))
    except OSError:
        return None
    if not target.exists() or _in_system_set(target):
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
        resolved = Path(path).resolve()
        if resolved.name == AGENT_HOME_DIR and resolved.parent.name == "scratch":
            wanted.append(resolved.parent)
    pins: list[Path] = []
    for scratch in dict.fromkeys(wanted):
        if not any(scratch == g or g in scratch.parents for g in grants):
            raise SandboxError(f"scratch {scratch} lies outside every writable grant")
        try:
            info = scratch.lstat()
        except FileNotFoundError:
            os.mkdir(scratch, 0o755)
            info = scratch.lstat()
        except OSError as exc:
            raise SandboxError(f"cannot inspect scratch {scratch}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SandboxError(f"scratch must be a real directory, refused: {scratch}")
        pins.append(scratch.resolve())
    return pins


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
    readable: list[Path] = []
    for path in layout.readable:
        resolved = Path(path).resolve()
        if not resolved.exists():
            raise SandboxError(f"readable path does not exist: {path}")
        check_not_denied(resolved, kind="readable")
        readable.append(resolved)
        out += ["--ro-bind", str(resolved), str(resolved)]
    for link, target in layout.links:
        out += ["--symlink", target, str(link)]
    grants: list[Path] = []
    for path in writable:
        resolved = Path(path).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        grants.append(resolved)
        out += ["--bind", str(resolved), str(resolved)]
    # The home is created host-side here, before its binds are emitted (A12).
    home = _bind_home(layout.home, writable) if layout.home is not None else None
    for scratch in _scratch_pins(home, list(mask or []), grants):
        out += ["--bind", str(scratch), str(scratch)]            # F1: a mount point inside
    for path in mask or []:
        out += ["--tmpfs", str(Path(path).resolve())]
    if home is not None:
        out += ["--bind", str(home), str(home)]
    work = Path(cwd).resolve()
    bound = [*readable, *grants, *([home] if home is not None else [])]
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


def dispatch_layout(project: Project, *, run_dir: Path, agent: str) -> Layout:
    """The S1 layout for one agent dispatch: project, Python, ScieFlow, uv, own home.

    Reads only (stat/lstat/readlink), so a resolution can happen before any write.
    The project root is bound read-only (D3); the writable run is bound over it.
    The interpreter's symlinked ancestors are recreated as links unless a bound
    path already shows them, so `sys.executable` resolves inside the sandbox (C4).
    """
    _check_agent_name(agent)
    run = Path(run_dir).resolve()
    home = agent_home_path(run, agent)
    launcher_dir, hops, uv_final = _launcher("uv")
    candidates = [project.root.resolve(), *_python_runtime(), _scieflow_root().resolve()]
    if uv_final is not None:
        candidates.append(uv_final)
    readable = tuple(p for p in _outermost(candidates) if not _in_system_set(p))
    for entry in readable:
        check_not_denied(entry, kind="readable")
    interpreter = Path(os.path.abspath(sys.executable))
    links = [*hops, *((p, os.readlink(p)) for p in reversed(interpreter.parents) if p.is_symlink())]
    kept_links: list[tuple[Path, str]] = []
    for link, target in links:
        covered = _in_system_set(link) or any(link == r or r in link.parents for r in readable)
        if not covered and link not in [k for k, _ in kept_links]:
            kept_links.append((link, target))
    return Layout(readable=readable, links=tuple(kept_links), home=home,
                  launcher_dirs=(launcher_dir,) if launcher_dir is not None else ())


def _launcher_path_needed(directory: Path, layout: Layout) -> bool:
    """A launcher dir joins PATH only when no bound system or readable path covers it (A17)."""
    if str(directory) in SYSTEM_PATH or _in_system_set(directory):
        return False
    return not any(directory == r or r in directory.parents for r in layout.readable)


def dispatch_environment(base: Path, *, layout: Layout, env: dict | None = None) -> dict:
    """`environment(base)` with HOME = the agent home and PATH = the sandbox PATH (C6)."""
    out = environment(base) if env is None else environment(base, env)
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


def unsandboxed_runs(project: Project) -> set[str]:
    """Run slugs config/sandbox.yml permits to dispatch unsandboxed.

    The per-run escape hatch lives here rather than in the run's own
    `config.yml`, because that file is inside the dispatch's writable bind: an
    agent could append `sandbox: off` and, with `agent_overrides:` in the same
    file, choose what the next unconfined dispatch runs. Here it is a human
    decision made outside every run, which is what makes the `sandbox.disabled`
    event's `actor: human` true.
    """
    path = project.root / ALLOWLIST_FILE
    return _unsandboxed_of(_load_allowlist(project), path)


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


def allowlist_paths(project: Project) -> list[Path]:
    """Extra writable paths from config/sandbox.yml, deny-by-default."""
    path = project.root / ALLOWLIST_FILE
    return _allowlist_of(_load_allowlist(project), project.root, path)


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


def writable_for(project: Project, *, run_dir: Path | None,
                 coordinator: bool) -> list[Path]:
    """What this dispatch may write. The caller declares which kind it is —
    the sandbox never guesses, because guessing wrong widens the boundary."""
    if coordinator:
        base = [project.workspace_root]
    elif run_dir is None:
        raise SandboxError(
            "a sandboxed agent dispatch must name the run it belongs to; "
            "use --no-sandbox for a one-off dispatch outside a run")
    else:
        base = [Path(run_dir)]
    return [*base, *allowlist_paths(project)]


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

    def __post_init__(self):
        for name in ('project_root', 'run_dir', 'workspace_root'):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute():
                raise TypeError('protection roots must be absolute Paths')
        if type(self.sandbox_enabled) is not bool or self.sandbox_enabled != (self.writable is not None):
            raise TypeError('sandbox state and grants must agree')
        for name in ('writable', 'masks', 'allowlist', 'coordinator_writable'):
            value = getattr(self, name)
            if name == 'writable' and value is None:
                continue
            if type(value) is not tuple or any(not isinstance(p, Path) or not p.is_absolute() for p in value):
                raise TypeError('grants and masks must be immutable absolute Path tuples')
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
                 policy, *narrowed, *broad, *masks, *originals]
        facts = _capture_path_facts(seeds)
        if next(f for f in facts if f.path == policy) != policy_fact:
            raise SandboxError('sandbox policy changed during protection capture')
        return TurnProtection(root, run, workspace, enabled, narrowed if enabled else None,
                              masks, allowlist, broad, digest, facts)
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, SandboxError):
            raise
        raise SandboxError('cannot capture turn protection') from exc
