"""Compatibility for commands from before the module merge.

ExperimentX, ResearchX and WhatsNEW became `scieflow experiment`, `scieflow
research` and `scieflow news`. About 190 earlier agent chats still call the old
entry points (`uv run scripts/agent_run.py`, `expx`, `whatsnew`, …), and a
resumed chat must keep working. Every shim here prints one notice naming the
new command, then delegates — no behaviour of its own beyond argv translation.

The mapping table is the single source for the shims, docs/MIGRATION.md and
the `scieflow` menu.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys

#: old invocation -> new invocation. Kept in sync with docs/MIGRATION.md.
COMMANDS: dict[str, str] = {
    "uv run scripts/agent_run.py": "uv run scieflow agent run",
    "python scripts/stub_agent.py": "python -m scieflow.core.stub_agent",
    "uv run scripts/search_openalex.py": "uv run scieflow research search openalex",
    "uv run scripts/search_arxiv.py": "uv run scieflow research search arxiv",
    "uv run scripts/search_europepmc.py": "uv run scieflow research search europepmc",
    "uv run scripts/search_crossref.py": "uv run scieflow research search crossref",
    "uv run scripts/validate_findings.py": "uv run scieflow research validate",
    "uv run scripts/check_citations.py": "uv run scieflow research check-citations",
    "uv run scripts/zotero_export.py": "uv run scieflow research zotero-export",
    "expx": "uv run scieflow experiment",
    "whatsnew": "uv run scieflow news",
    "from sflib.config import …": "from scieflow.core.config import …",
}

#: legacy environment variable -> current name. Read as a fallback only.
ENV_VARS: dict[str, str] = {
    "RESEARCHX_MAILTO": "SCIEFLOW_MAILTO",
    "RESEARCHX_PROMPT_ARGV_LIMIT": "SCIEFLOW_PROMPT_ARGV_LIMIT",
    "WHATSNEW_DB": "SCIEFLOW_NEWS_DB",
}

#: `--cwd` values that pointed into the old vendored checkouts.
VENDOR_PREFIXES: tuple[str, ...] = ("vendors/ResearchX", "vendors/ExperimentX")

#: Subcommands of `expx` that now need `--experiments-dir`.
NEEDS_EXPERIMENTS_DIR: frozenset[str] = frozenset({"run", "sweep"})


def notice(old: str, new: str) -> None:
    """One line on stderr, so an agent reading output learns the new name."""
    if os.environ.get("SCIEFLOW_LEGACY_QUIET"):
        return
    print(f"legacy: {old} → {new} (see docs/MIGRATION.md)", file=sys.stderr)


def env(name: str, default: str | None = None) -> str | None:
    """Current variable first, then its legacy name."""
    value = os.environ.get(name)
    if value:
        return value
    for old, new in ENV_VARS.items():
        if new == name and os.environ.get(old):
            return os.environ[old]
    return default


def translate_cwd(argv: list[str]) -> list[str]:
    """`--cwd vendors/X[/…]` -> repo root: the old checkouts are frozen."""
    out: list[str] = []
    skip_next = False
    for index, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        value = None
        if arg == "--cwd" and index + 1 < len(argv):
            value = argv[index + 1]
            skip_next = True
        elif arg.startswith("--cwd="):
            value = arg.split("=", 1)[1]
        if value is None:
            out.append(arg)
            continue
        cleaned = value.lstrip("./")
        if cleaned.startswith(VENDOR_PREFIXES):
            notice(f"--cwd {value}", "--cwd omitted (repo root)")
            continue
        out.extend(["--cwd", value])
    return out


def forward(old: str, module: str, argv: list[str] | None = None) -> None:
    """Announce, then call `module.main(argv)`."""
    notice(old, COMMANDS.get(old, module))
    args = list(sys.argv[1:] if argv is None else argv)
    target = importlib.import_module(module)
    if inspect.signature(target.main).parameters:
        target.main(args)
    else:
        # Entry points without an argv parameter read sys.argv themselves.
        sys.argv = [module, *args]
        target.main()


# -- console scripts ------------------------------------------------------
def expx_main() -> None:
    """`expx …` -> `scieflow experiment …`."""
    from scieflow.experiments.cli import experiment

    args = sys.argv[1:]
    notice("expx", COMMANDS["expx"])
    if args and args[0] in NEEDS_EXPERIMENTS_DIR and not any(
        a == "--experiments-dir" or a.startswith("--experiments-dir=") for a in args
    ):
        print(
            "error: runs are now recorded per workspace. Re-run as:\n"
            f"  uv run scieflow experiment {' '.join(args)} "
            "--experiments-dir workspace/<slug>/experiments",
            file=sys.stderr,
        )
        sys.exit(2)
    experiment.main(args=args, prog_name="expx")


def whatsnew_main() -> None:
    """`whatsnew …` -> `scieflow news …`."""
    from scieflow.news.cli import news

    notice("whatsnew", COMMANDS["whatsnew"])
    news.main(args=sys.argv[1:], prog_name="whatsnew")
