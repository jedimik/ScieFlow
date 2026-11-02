"""Claim waivers (M-G 5.7, M-G-D7): the user accepts one finding of one claim, recorded as a human action.

A waiver is refused inside a ScieFlow dispatch (sub-agents never waive), for a blocked reference (C005) and
for unreleased L2 data (not waivable at all). It names the claim's current text hash, so editing the sentence
voids it. Only `actor: human` events count (compile._apply_waivers).
"""

from __future__ import annotations

from pathlib import Path

from scieflow.core import events
from scieflow.integrations.scite.mcp import in_dispatch
from scieflow.research.manuscript.claims import compile as compile_mod

MIN_REASON, MAX_REASON = 10, 500


class WaiverError(ValueError):
    """A waiver is refused: `rule` names the refusal, `message` says why (no values)."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def waive(run_folder, project, claim_id: str, code: str, reason: str) -> dict:
    """Record a human waiver of `code` on claim `claim_id` (note.claims_waived). Returns the event payload."""
    run = Path(run_folder)
    if in_dispatch():
        raise WaiverError("dispatch", "refused: the user waives; sub-agents never do (SCIEFLOW_DISPATCH is set)")
    if code == "C005":
        raise WaiverError("not-waivable", "a blocked reference is cleared by fixing the reference or by a human "
                                          "overrule of the review-blocker gate, never by a claim waiver")
    if code not in compile_mod.WAIVABLE:
        raise WaiverError("unknown-code", f"{code!r} cannot be waived (waivable: "
                                          f"{', '.join(sorted(compile_mod.WAIVABLE))})")
    if not MIN_REASON <= len(reason) <= MAX_REASON:
        raise WaiverError("reason", f"the reason must be {MIN_REASON} to {MAX_REASON} characters")
    current = compile_mod.check_run(project, run)
    record = next((r for r in current.claims if r["id"] == claim_id), None)
    if record is None:
        raise WaiverError("claim-unknown", "no claim with that id in the current ledger")
    if not any(f["claim"] == claim_id and f["code"] == code for f in current.findings + current.waived):
        raise WaiverError("no-finding", f"claim {claim_id} has no {code} finding to waive")
    payload = {"claim": claim_id, "code": code, "text_sha256": record["text_sha256"], "reason": reason}
    events.emit(run, "note.claims_waived", "human", **payload)
    return payload
