"""The claim check report (M-G 5.6): report/claims/check.json and check.md, and one note.claims_checked event.

Deterministic bytes (sorted keys, no timestamps, UTF-8, \\n line ends). Messages never carry an evidence value
(M-G 5.16); the markdown table escapes pipes.
"""

from __future__ import annotations

import json
from pathlib import Path

from scieflow.core import events
from scieflow.research.journal import state
from scieflow.research.manuscript.claims import ledger as ledger_mod

CHECK_JSON = "check.json"
CHECK_MD = "check.md"


def _cell(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(document: dict) -> str:
    counts = document["counts"]
    lines = [
        "# Claim check",
        "",
        f"manuscript tree sha256: {document['manuscript_tree_sha256']}",
        f"ledger sha256: {document['ledger_sha256']}",
        f"errors: {counts['errors']}, warnings: {counts['warnings']}, waived: {counts['waived']}",
        "",
        "| Severity | Code | Rule | Claim | Location | Message |",
        "|---|---|---|---|---|---|",
    ]
    for item in document["findings"]:
        where = f"{item['file']}:{item['line']}" if item["file"] else ""
        lines.append(f"| {_cell(item['severity'])} | {_cell(item['code'])} | {_cell(item['rule'])} | "
                     f"{_cell(item['claim'])} | {_cell(where)} | {_cell(item['message'])} |")
    if document["waived"]:
        lines += ["", "Waived (a human decision):", "", "| Code | Rule | Claim |", "|---|---|---|"]
        for item in document["waived"]:
            lines.append(f"| {_cell(item['code'])} | {_cell(item['rule'])} | {_cell(item['claim'])} |")
    return "\n".join(lines) + "\n"


def _refuse_links(run: Path) -> None:
    folder = ledger_mod.ledger_folder(run)
    for part in (run / "report", folder, folder / CHECK_JSON, folder / CHECK_MD):
        if part.is_symlink():
            raise ledger_mod.ClaimsError("claims-path", f"{part.relative_to(run)} is a symbolic link")


def write(run_folder, document: dict) -> tuple[Path, Path]:
    """Write check.json and check.md (atomic). Refuses a symbolic link on the path (M-G 5.1)."""
    run = Path(run_folder)
    _refuse_links(run)
    folder = ledger_mod.ledger_folder(run)
    json_path, md_path = folder / CHECK_JSON, folder / CHECK_MD
    try:
        folder.mkdir(parents=True, exist_ok=True)
        state.atomic_write(json_path, (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)
                                       + "\n").encode("utf-8"))
        state.atomic_write(md_path, render_markdown(document).encode("utf-8"))
    except (state.StateError, OSError) as exc:
        raise ledger_mod.ClaimsError("claims-path", f"the claim check cannot be written: {exc}") from exc
    return json_path, md_path


def note_checked(run_folder, document: dict) -> dict:
    """One note.claims_checked event with counts only (no values, no text)."""
    counts = document["counts"]
    return events.emit(Path(run_folder), "note.claims_checked", "system", errors=counts["errors"],
                       warnings=counts["warnings"], waived=counts["waived"])
