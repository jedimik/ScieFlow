"""Validate a research-module artifact against its JSON schema.

usage: scieflow research validate <file> [--schema findings|review|gaps|manifest|manuscript-review]
JSON by default; files ending .yml/.yaml are parsed as YAML (dates coerced to
ISO strings so schemas can pattern-match them).
Prints OK (exit 0) or INVALID lines (exit 1) suitable for feeding back to an agent.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"


def load(path: Path):
    if path.suffix in {".yml", ".yaml"}:
        data = yaml.safe_load(path.read_text())
        # YAML parses bare dates as datetime.date; round-trip through JSON
        # with default=str so schema "string" + pattern checks apply.
        return json.loads(json.dumps(data, default=str))
    return json.loads(path.read_text())


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research validate")
    ap.add_argument("file", type=Path)
    ap.add_argument(
        "--schema", choices=["findings", "review", "gaps", "manifest", "manuscript-review"], default="findings"
    )
    args = ap.parse_args(argv)

    schema = json.loads((SCHEMAS_DIR / f"{args.schema}.schema.json").read_text())
    try:
        data = load(args.file)
    except (json.JSONDecodeError, yaml.YAMLError, OSError) as exc:
        print(f"INVALID: not readable JSON/YAML: {exc}")
        sys.exit(1)

    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: e.json_path)
    if errors:
        for err in errors:
            print(f"INVALID: {err.json_path}: {err.message}")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
