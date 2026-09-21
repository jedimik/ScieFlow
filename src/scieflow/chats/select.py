"""Picking what goes in: a checkbox TUI, a prompt fallback, and a plan file.

The plan file is what makes a backup repeatable — `backup --plan FILE` replays
an earlier selection with no prompts at all.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml

from .model import Artifact, ChatRef

PLAN_VERSION = 1


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}G"


def chat_label(ref: ChatRef) -> str:
    date = ref.updated.date().isoformat() if ref.updated else "    ?     "
    return (
        f"{date}  {ref.project_name[:20]:<20} "
        f"{human(ref.size_bytes):>7}  {ref.title[:56]}"
    )


def artifact_label(artifact: Artifact) -> str:
    bits = [f"{artifact.kind}: {artifact.name}"]
    if artifact.version:
        bits.append(f"v{artifact.version}")
    bits.append(f"used by {len(artifact.used_by)} chat(s)")
    if artifact.confidence == "low":
        bits.append("low confidence")
    if artifact.kind == "skill" and artifact.source is None:
        bits.append("not installed locally")
    return "  —  ".join(bits)


def _questionary():
    try:
        import questionary
    except ImportError:
        return None
    return questionary if sys.stdin.isatty() and sys.stdout.isatty() else None


# -- chats --------------------------------------------------------------
def choose_chats(refs: list[ChatRef]) -> list[ChatRef]:
    if not refs:
        return []
    questionary = _questionary()
    if questionary is None:
        return _prompt_subset(refs, chat_label, "chats")
    choices = []
    current = None
    for ref in sorted(refs, key=lambda r: (r.tool, r.project_name, r.chat_id)):
        if ref.tool != current:
            current = ref.tool
            choices.append(questionary.Separator(f"── {ref.tool} ──"))
        choices.append(questionary.Choice(title=chat_label(ref), value=ref))
    picked = questionary.checkbox(
        "Select chats to back up (space toggles, enter confirms):",
        choices=choices,
    ).ask()
    if picked is None:
        raise click.Abort()
    return list(picked)


# -- skills / plugins ---------------------------------------------------
def choose_artifacts(artifacts: list[Artifact]) -> list[Artifact]:
    if not artifacts:
        return []
    questionary = _questionary()
    if questionary is None:
        return _prompt_subset(artifacts, artifact_label, "skills/plugins", preselect=True)
    choices = [
        questionary.Choice(
            title=artifact_label(a),
            value=a,
            checked=a.confidence == "high" and (a.kind != "skill" or a.source is not None),
        )
        for a in artifacts
    ]
    picked = questionary.checkbox(
        "Skills and plugins these chats used (pre-checked; adjust as needed):",
        choices=choices,
    ).ask()
    if picked is None:
        raise click.Abort()
    return list(picked)


# -- fallback -----------------------------------------------------------
def _prompt_subset(items, label, noun, preselect: bool = False):
    """Numbered multi-select for machines without questionary or a TTY."""
    if not sys.stdin.isatty():
        raise click.ClickException(
            f"cannot choose {noun} without a terminal: pass --all, or --plan FILE "
            "with a selection written by 'scieflow chats plan'"
        )
    for index, item in enumerate(items, 1):
        click.echo(f"  {index:>3}. {label(item)}")
    default = "all" if preselect else ""
    answer = click.prompt(
        f"Select {noun} (e.g. 1-5,8 | all | none)",
        default=default,
        show_default=bool(default),
    )
    return [items[i - 1] for i in parse_ranges(answer, len(items))]


def parse_ranges(answer: str, count: int) -> list[int]:
    """`1-5,8` -> [1,2,3,4,5,8]. `all` and `none` are accepted verbatim."""
    text = answer.strip().lower()
    if text in {"", "none"}:
        return []
    if text == "all":
        return list(range(1, count + 1))
    picked: set[int] = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            try:
                lo, hi = int(start), int(end)
            except ValueError as e:
                raise click.BadParameter(f"not a range: {part!r}") from e
            picked.update(range(max(1, lo), min(count, hi) + 1))
        else:
            try:
                value = int(part)
            except ValueError as e:
                raise click.BadParameter(f"not a number: {part!r}") from e
            if 1 <= value <= count:
                picked.add(value)
    return sorted(picked)


# -- plan file ----------------------------------------------------------
def write_plan(
    path: Path, refs: list[ChatRef], artifacts: list[Artifact], selected_keys: set[str]
) -> None:
    doc = {
        "version": PLAN_VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chats": [
            {
                "key": r.key,
                "tool": r.tool,
                "project": r.project_path,
                "title": r.title,
                "updated": r.updated.isoformat() if r.updated else None,
                "selected": r.key in selected_keys,
            }
            for r in refs
        ],
        "artifacts": [
            {
                "kind": a.kind,
                "name": a.name,
                "confidence": a.confidence,
                "selected": True,
            }
            for a in artifacts
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))


def read_plan(path: Path) -> tuple[set[str], set[tuple[str, str]]]:
    try:
        doc = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise click.ClickException(f"unreadable plan file {path}: {e}") from e
    if not isinstance(doc, dict) or doc.get("version") != PLAN_VERSION:
        raise click.ClickException(f"{path}: not a v{PLAN_VERSION} chats plan file")
    chats = {
        str(row["key"])
        for row in doc.get("chats") or []
        if isinstance(row, dict) and row.get("selected") and row.get("key")
    }
    artifacts = {
        (str(row.get("kind")), str(row.get("name")))
        for row in doc.get("artifacts") or []
        if isinstance(row, dict) and row.get("selected") and row.get("name")
    }
    return chats, artifacts
