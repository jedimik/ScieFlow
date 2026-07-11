#!/usr/bin/env python3
"""Graceful stop: record stop reason + resume instructions in status.yml.

usage: checkpoint.py <workspace> --reason REASON [--detail TEXT]
"""

import argparse
from pathlib import Path

import status as status_mod


def checkpoint(ws: Path, reason: str, detail: str = "") -> dict:
    st = status_mod.read_status(ws)
    pending = status_mod.next_pending(st)
    resume = (
        f"Resume: read status.yml; continue at iteration {st['iteration']}, "
        f"phase '{pending or 'advance-iteration'}'. Phase artifacts are in "
        f"iterations/{st['iteration']}/; budget ledger in budget.yml."
    )
    st = status_mod.stop(st, reason, detail, resume)
    status_mod.write_status(ws, st)
    return st


def resume_info(ws: Path) -> str:
    st = status_mod.read_status(ws)
    if st.get("stopped"):
        return st["stopped"]["resume"]
    pending = status_mod.next_pending(st)
    return (f"Run active: iteration {st['iteration']}, "
            f"next phase '{pending or 'advance-iteration'}'.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", type=Path)
    ap.add_argument("--reason", required=True, choices=sorted(status_mod.STOP_REASONS))
    ap.add_argument("--detail", default="")
    args = ap.parse_args()
    st = checkpoint(args.workspace, args.reason, args.detail)
    print(st["stopped"]["resume"])


if __name__ == "__main__":
    main()
