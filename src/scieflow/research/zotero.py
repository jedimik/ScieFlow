"""Push selected papers into Zotero and export references.bib.

usage: scieflow research zotero-export --workspace workspace/<slug>
Reads report/selected_dois.txt (one DOI per line). Target library/collection
come from workspace config.yml -> global defaults -> user library.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from scieflow.research import config


def zot(args: list[str], library: str) -> str:
    cmd = ["zot", "--json", "--no-interaction", "--library", library, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"zot {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _collection_names(raw: str) -> set[str]:
    data = json.loads(raw or "{}")
    items = data.get("data", data) if isinstance(data, dict) and "data" in data else data
    if not isinstance(items, list):
        items = [items] if isinstance(items, dict) else []
    return {c.get("name") for c in items if isinstance(c, dict)}


def _item_keys(raw: str) -> list[str]:
    data = json.loads(raw or "{}")
    items = data.get("data", data) if isinstance(data, dict) and "data" in data else data
    if not isinstance(items, list):
        items = [items] if isinstance(items, dict) else []
    return [i["key"] for i in items if isinstance(i, dict) and i.get("key")]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research zotero-export")
    ap.add_argument("--workspace", type=Path, required=True)
    args = ap.parse_args(argv)

    root = config.repo_root()
    ws = args.workspace.resolve()
    merged = config.load_workspace(ws, root)
    target = config.zotero_target(merged)
    library = target["library"]
    collection = target["collection"] or f"ScieFlow/{ws.name}"

    dois_file = ws / "report" / "selected_dois.txt"
    if not dois_file.exists():
        sys.exit(f"missing {dois_file} — run the synthesize phase first")
    dois = []
    for line in dois_file.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # everything from here on is the "no-doi" tail (titles skipped by export)
            break
        if stripped:
            dois.append(stripped)

    if collection not in _collection_names(zot(["collection", "list"], library)):
        zot(["collection", "create", collection], library)

    bib_chunks = []
    for doi in dois:
        keys = _item_keys(zot(["add", "--doi", doi], library))
        for key in keys:
            zot(["collection", "move", key, collection], library)
            bib_chunks.append(zot(["export", key], library))
        if not keys:
            print(f"warning: no item key returned for {doi}", file=sys.stderr)

    out = ws / "report" / "references.bib"
    out.write_text("\n".join(chunk.strip() for chunk in bib_chunks) + "\n")
    print(f"exported {len(bib_chunks)} entries to {out} (library={library}, collection={collection})")


if __name__ == "__main__":
    main()
