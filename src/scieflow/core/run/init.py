"""Create a run workspace: workspace/<slug>/ with goal, config, status, budget, notebook.

usage: sfx_init.py <slug> --goal <goal.md> [--approval MODE] [--max-iterations N]
       [--max-experiment-runs N] [--max-wall-minutes N] [--workspace-root DIR]
"""

import argparse
import json
import shutil
from contextlib import ExitStack
from pathlib import Path

import yaml

from scieflow.core import config, events, store
from scieflow.core.project import clean_slug
from scieflow.core.run import budget as budget_mod
from scieflow.core.run import status as status_mod


def init_workspace(slug: str, goal_file: Path, workspace_root: Path,
                   overrides: dict, root: Path) -> Path:
    """Create a run workspace under `workspace_root`.

    `slug` is validated and normalised here with `clean_slug` — the same
    check `Project.run_dir` applies — so this is safe to call directly, as
    `scieflow run init` does, not just through `service.create_run`, whose
    own check ahead of this call is defence in depth, not the only guard.
    """
    slug = clean_slug(slug)
    workflow = overrides.get("workflow")
    paper = workflow in status_mod.PAPER_WORKFLOWS
    if paper and overrides.get("approval") is None:
        raise ValueError(f"{workflow} initialization requires explicit --approval")
    cfg = {} if paper else dict(config.load_defaults(root))
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    caps = [cfg.get(k) for k in budget_mod.DIMENSIONS.values()]
    if paper and any(v is not None for v in caps) and any(v is None for v in caps):
        raise ValueError(f"supply all three budget limits, or none, for {workflow}")
    ws = _workspace_path(workspace_root, slug)
    with _initialization_lock(ws):
        _workspace_path(workspace_root, slug)
        # Ownership is established by an exclusive mkdir OUTSIDE cleanup.
        # A losing creator must never remove the winner's directory.
        ws.mkdir()
        _populate_new_workspace(ws, goal_file, cfg, paper, workflow if paper else "research-loop")
    return ws


def _populate_new_workspace(ws: Path, goal_file: Path, cfg: dict, paper: bool,
                            workflow: str) -> None:
    try:
        if not paper:
            (ws / "iterations").mkdir()
        (ws / "logs").mkdir()
        # tools/: scripts the run writes. scratch/: tests, envs, clones and
        # caches — left out of archives and DVC (workspace/README.md).
        (ws / "tools").mkdir()
        (ws / "scratch").mkdir()
        shutil.copy(goal_file, ws / "goal.md")
        (ws / "config.yml").write_text(yaml.safe_dump(cfg, sort_keys=False))
        status_mod.write_status(ws, status_mod.new_status(ws.name, cfg["approval"], workflow))
        if not paper or any(cfg.get(k) is not None for k in budget_mod.DIMENSIONS.values()):
            budget_mod.write_budget(ws, budget_mod.new_budget(
                cfg["max_iterations"], cfg["max_experiment_runs"], cfg["max_wall_minutes"]))
        if not paper:
            (ws / "notebook.md").write_text(
                f"# Research notebook — {ws.name}\n\nGoal: see goal.md\n")
        events.emit(ws, "run.created", "system", approval=cfg["approval"])
    except BaseException:
        shutil.rmtree(ws, ignore_errors=True)
        raise


def _regular_or_missing(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"control must be a regular file, not a symlink: {path}")


def _workspace_path(workspace_root: Path, slug: str) -> Path:
    ws = Path(workspace_root).absolute() / clean_slug(slug)
    if any(p.is_symlink() for p in (ws, *ws.parents)):
        raise ValueError(f"workspace path must not contain symlinks: {ws}")
    return ws


def _initialization_lock(ws: Path):
    # Outside the run, so creation and adoption share it even before mkdir.
    path = ws.parent / f".{ws.name}.init"
    _regular_or_missing(Path(str(path) + ".lock"))
    return store.locked(path)


def adopt_workspace(slug: str, workspace_root: Path, *, workflow: str,
                    approval: str, goal_file: Path | None = None,
                    actor: str = "human") -> Path:
    """Attach native paper metadata without rewriting prepared run files.

    Status is authoritative for workflow/approval; an existing config need
    not duplicate them, but may not contradict them. No defaults or budget
    are imported, and an existing goal always wins over the supplied source.
    """
    if workflow not in status_mod.PAPER_WORKFLOWS:
        raise ValueError("adoption supports only paper-draft and paper-revision")
    st = status_mod.new_status(clean_slug(slug), approval, workflow)
    if actor not in events.ACTORS:
        raise ValueError(f"unknown actor: {actor}")
    ws = _workspace_path(workspace_root, slug)
    if not ws.is_dir():
        raise ValueError(f"adoption needs an existing workspace: {ws}")
    names = ("status.yml", "config.yml", "goal.md", "events.jsonl")
    for name in (*names, "budget.yml", "charter.yml"):
        _regular_or_missing(ws / name)
        _regular_or_missing(ws / (name + ".lock"))
    with ExitStack() as locks:
        locks.enter_context(_initialization_lock(ws))
        _workspace_path(workspace_root, slug)
        if not ws.is_dir():
            raise ValueError(f"adoption needs an existing workspace: {ws}")
        # The status lock serializes adoption with both another adopter and
        # native phase writers. Use the same sibling locks as store writers.
        for name in names:
            _regular_or_missing(ws / name)
            _regular_or_missing(ws / (name + ".lock"))
            locks.enter_context(store.locked(ws / name))
        _workspace_path(workspace_root, slug)
        for name in names:
            _regular_or_missing(ws / name)
        # A failed cleanup may leave config/goal/history without any status.
        # Do not mistake those leftovers for approved pre-existing metadata.
        incomplete = ws / ".adoption-incomplete"
        _regular_or_missing(incomplete)
        if incomplete.exists():
            raise OSError(f"incomplete adoption requires inspection before retry: {incomplete}")
        if (ws / "status.yml").exists():
            raise FileExistsError(f"workspace is already initialized: {ws}")
        try:
            cfg = store.read_yaml(ws / "config.yml", default={})
        except yaml.YAMLError as exc:
            raise ValueError("cannot parse existing config.yml") from exc
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            raise ValueError("existing config.yml must be a mapping")
        for key, value in (("workflow", workflow), ("approval", approval)):
            if key in cfg and cfg[key] != value:
                raise ValueError(f"existing config {key} conflicts with {value}")
        writes = {}
        if not (ws / "config.yml").exists():
            writes["config.yml"] = yaml.safe_dump(
                {"workflow": workflow, "approval": approval}, sort_keys=False)
        if not (ws / "goal.md").exists():
            if goal_file is None:
                raise ValueError("goal.md is missing; supply --goal")
            _regular_or_missing(goal_file)
            writes["goal.md"] = goal_file.read_bytes().decode("utf-8")
        writes["status.yml"] = yaml.safe_dump(st, sort_keys=False)
        history = (ws / "events.jsonl").read_bytes() if (ws / "events.jsonl").exists() else None
        prefix = (history or b"").decode("utf-8")
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        # events.emit takes this same lock itself. Build its standard envelope
        # here so the event and metadata can be rolled back under one lock set;
        # never truncate a concurrent gate answer's appended event on failure.
        event = {"id": store.new_id(), "ts": st["created"], "run": st["id"],
                 "slug": ws.name, "type": "note.run-adopted", "actor": actor,
                 "data": {"workflow": workflow, "approval": approval}}
        writes["events.jsonl"] = prefix + json.dumps(event, ensure_ascii=False) + "\n"
        attempted = []
        # Persist a retry guard before publishing anything. Remove it only on
        # success or complete rollback; it is not status and grants no approval.
        incomplete.touch(exist_ok=False)
        try:
            for name, text in writes.items():
                attempted.append(name)
                store._atomic_write(ws / name, text)
            incomplete.unlink()
        except BaseException as exc:
            # Only this operation's new files (and its own event append) are
            # undone. One cleanup failure must not prevent independent ones.
            cleanup_errors = []
            for name in reversed(attempted):
                path = ws / name
                try:
                    if name == "events.jsonl" and history is not None:
                        # Atomic replacement may have failed before changing
                        # anything. Avoid a needless (possibly refused) rewrite.
                        if not path.exists() or path.read_bytes() != history:
                            store._atomic_write(path, history.decode("utf-8"))
                    else:
                        path.unlink(missing_ok=True)
                except BaseException as cleanup_exc:
                    cleanup_errors.append(f"{name}: {cleanup_exc}")
            if not cleanup_errors:
                try:
                    incomplete.unlink(missing_ok=True)
                except BaseException as cleanup_exc:
                    cleanup_errors.append(f"{incomplete.name}: {cleanup_exc}")
            if cleanup_errors:
                raise OSError(
                    f"adoption failed: {exc}; rollback incomplete: "
                    + "; ".join(cleanup_errors)
                    + f"; inspect {incomplete} before retry"
                ) from exc
            raise
    return ws


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="sfx_init.py")
    ap.add_argument("slug")
    ap.add_argument("--goal", type=Path, required=True)
    ap.add_argument("--approval", choices=sorted(status_mod.vocab()["approval_modes"]))
    ap.add_argument("--max-iterations", type=int)
    ap.add_argument("--max-experiment-runs", type=int)
    ap.add_argument("--max-wall-minutes", type=int)
    ap.add_argument("--workspace-root", type=Path, default=None)
    args = ap.parse_args(argv)
    root = config.repo_root()
    overrides = {
        "approval": args.approval,
        "max_iterations": args.max_iterations,
        "max_experiment_runs": args.max_experiment_runs,
        "max_wall_minutes": args.max_wall_minutes,
    }
    ws = init_workspace(args.slug, args.goal, args.workspace_root or root / "workspace",
                        overrides, root)
    print(f"initialized {ws}")

