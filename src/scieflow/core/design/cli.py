"""`scieflow design` (M-D1): study-design commands. Read-only in t2.

Exit codes: 0 ok, 1 problems found or checklists cannot be loaded, 2 usage.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from scieflow.core.config import repo_root
from scieflow.core.design import reporting

OK, PROBLEMS = 0, 1


def _root() -> Path:
    return repo_root()


@click.group("design")
def design() -> None:
    """Study design: reporting checklists (read-only); design.yml, power and lock to follow."""


@design.command("guidelines")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
@click.option("--check", is_flag=True, help="Validate every checklist file (exit 1 on problems).")
def guidelines(as_json: bool, check: bool) -> None:
    """List the reporting checklists (CONSORT, STROBE, ...) and their verification status."""
    root = _root()
    try:
        if check:
            problems = reporting.validate_all(root)
            for problem in problems:
                click.echo(str(problem))
            if problems:
                sys.exit(PROBLEMS)
            click.echo(f"ok: {len(reporting.registry(root))} reporting checklists validate")
            return
        checklists = reporting.registry(root)
    except reporting.ReportingError as exc:
        click.echo(f"design guidelines: {exc}", err=True)
        sys.exit(PROBLEMS)
    rows = [
        {"id": c.id, "name": c.name, "applies_to": list(c.applies_to),
         "items": len(c.items), "verification": c.verification["status"]}
        for c in checklists
    ]
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    for row in rows:
        click.echo(f"{row['id']:<14} {row['name']:<14} {','.join(row['applies_to']):<22} "
                   f"{row['items']:>3} items  {row['verification']}")
