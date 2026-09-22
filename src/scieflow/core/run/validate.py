"""Validate ScieFlow artifacts against schemas/.

usage: validate.py <file> --schema status|notebook-entry|manifest|claim-audit
Prints 'INVALID: <error>' per problem; exit 1 if any.
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

from scieflow.core.project import Project


def _schema(name: str) -> dict:
    return Project.discover().schema(name)


def validate_status(st: dict) -> list[str]:
    s = _schema("status")
    errors = [f"missing key: {k}" for k in
              ["run", "created", "approval", "iteration", "phases", "stopped"] if k not in st]
    if errors:
        return errors
    if st["approval"] not in s["approval_modes"]:
        errors.append(f"bad approval: {st['approval']}")
    if list(st["phases"]) != s["phases"]:
        errors.append(f"phases must be exactly {s['phases']}")
    for p, state in st["phases"].items():
        if state not in s["states"]:
            errors.append(f"bad state for {p}: {state}")
    if st["stopped"] is not None and st["stopped"].get("reason") not in s["stop_reasons"]:
        errors.append(f"bad stop reason: {st['stopped'].get('reason')}")
    return errors


def validate_status_research(st: dict) -> list[str]:
    s = _schema("status-research")
    workflow = st.get("workflow")
    if workflow not in s["workflows"]:
        return [f"unknown workflow: {workflow!r} (known: {', '.join(s['workflows'])})"]
    errors = []
    declared = s["workflows"][workflow]
    for phase, state in (st.get("phases") or {}).items():
        if declared and phase not in declared:
            errors.append(f"unknown phase for {workflow}: {phase}")
        if isinstance(state, str) and not any(
                state == known or state.startswith(f"{known}-") for known in s["states"]):
            errors.append(f"bad state for {phase}: {state}")
    return errors


def validate_notebook_entry(text: str) -> list[str]:
    s = _schema("notebook-entry")
    errors = []
    if not re.search(r"^## Iteration \d+", text, re.M):
        errors.append("missing '## Iteration <n>' heading")
    for section in s["required_sections"]:
        if not re.search(rf"^### {re.escape(section)}\s*$", text, re.M):
            errors.append(f"missing section: ### {section}")
    return errors


def validate_manifest(d: dict) -> list[str]:
    s = _schema("manifest")
    errors = [f"missing key: {k}" for k in s["required_keys"] if k not in d]
    for i, a in enumerate(d.get("artifacts") or []):
        for k in s["artifact_keys"]:
            if k not in a:
                errors.append(f"artifacts[{i}]: missing {k}")
        if a.get("kind") is not None and a["kind"] not in s["kinds"]:
            errors.append(f"artifacts[{i}]: bad kind: {a['kind']}")
    return errors


def validate_claim_audit(text: str) -> list[str]:
    s = _schema("claim-audit")
    errors = []
    for section in s["required_sections"]:
        if not re.search(rf"^## {re.escape(section)}\s*$", text, re.M):
            errors.append(f"missing section: ## {section}")
    for heading in re.findall(r"^### ([a-z-]+) \(\d+\)\s*$", text, re.M):
        if heading not in s["verdicts"]:
            errors.append(f"unknown verdict section: {heading}")
    return errors


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="validate.py")
    ap.add_argument("file", type=Path)
    ap.add_argument("--schema", required=True,
                    choices=["status", "status-research", "notebook-entry", "manifest",
                             "claim-audit"])
    args = ap.parse_args(argv)
    text = args.file.read_text()
    if args.schema == "notebook-entry":
        errors = validate_notebook_entry(text)
    elif args.schema == "claim-audit":
        errors = validate_claim_audit(text)
    elif args.schema == "status":
        errors = validate_status(yaml.safe_load(text))
    elif args.schema == "status-research":
        errors = validate_status_research(yaml.safe_load(text))
    else:
        errors = validate_manifest(yaml.safe_load(text))
    for e in errors:
        print(f"INVALID: {e}")
    sys.exit(1 if errors else 0)

