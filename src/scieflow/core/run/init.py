"""Create a run workspace: workspace/<slug>/ with goal, config, status, budget, notebook.

usage: sfx_init.py <slug> --goal <goal.md> [--approval MODE] [--max-iterations N]
       [--max-experiment-runs N] [--max-wall-minutes N] [--workspace-root DIR]
"""

import argparse
import shutil
from pathlib import Path

import yaml

from scieflow.core import config
from scieflow.core.run import budget as budget_mod
from scieflow.core.run import status as status_mod


def init_workspace(slug: str, goal_file: Path, workspace_root: Path,
                   overrides: dict, root: Path) -> Path:
    cfg = dict(config.load_defaults(root))
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    ws = workspace_root / slug
    if ws.exists():
        raise FileExistsError(f"workspace already exists: {ws}")
    try:
        (ws / "iterations").mkdir(parents=True)
        (ws / "logs").mkdir()
        # tools/: scripts the run writes. scratch/: tests, envs, clones and
        # caches — left out of archives and DVC (workspace/README.md).
        (ws / "tools").mkdir()
        (ws / "scratch").mkdir()
        shutil.copy(goal_file, ws / "goal.md")
        (ws / "config.yml").write_text(yaml.safe_dump(cfg, sort_keys=False))
        status_mod.write_status(ws, status_mod.new_status(slug, cfg["approval"]))
        budget_mod.write_budget(ws, budget_mod.new_budget(
            cfg["max_iterations"], cfg["max_experiment_runs"], cfg["max_wall_minutes"]))
        (ws / "notebook.md").write_text(f"# Research notebook — {slug}\n\nGoal: see goal.md\n")
        from scieflow.core import events

        events.emit(ws, "run.created", "system", approval=cfg["approval"])
    except BaseException:
        shutil.rmtree(ws, ignore_errors=True)
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

