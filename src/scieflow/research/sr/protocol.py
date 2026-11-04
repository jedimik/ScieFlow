"""The locked review protocol (M-H plan 5.2): the review block that the design lock approved.

Read-only. A protocol is usable only when the design lock is `locked`, the study type is
systematic-review, and the review block is read from the in-force locked copy
(design/locked/v<n>/design.yml). Every other state is refused with its message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scieflow.core.design import lock
from scieflow.research.search import registry
from scieflow.research.sr.errors import SRError

SR_STUDY = "systematic-review"


@dataclass(frozen=True)
class Locked:
    version: int
    content_hash: str
    review: dict
    design: dict
    criteria_include: list = field(default_factory=list)
    criteria_exclude: list = field(default_factory=list)


def require_locked(project, ws) -> Locked:
    """The in-force locked protocol, or SRError naming the rule (plan 5.2)."""
    ws = Path(ws)
    st = lock.state(project, ws)
    if st.status in ("none", "draft", "pending"):
        raise SRError(f"lock the protocol first: scieflow design lock {ws.name}")
    if st.status == "modified":
        raise SRError(f"design.yml differs from its lock (version {st.version}); "
                      "amend it with scieflow design amend or restore it")
    if st.status != "locked":
        raise SRError(f"the design lock does not verify ({st.problem or st.status}); "
                      "nothing is read from it")
    if st.study_type != SR_STUDY:
        raise SRError(f"the locked design's study type is {st.study_type}, not {SR_STUDY}")
    seq = int(str(st.version).split(".")[0])
    copy = ws / "design" / "locked" / f"v{seq}"
    path = copy / "design.yml"
    if copy.is_symlink() or path.is_symlink() or not path.is_file():
        raise SRError(f"design/locked/v{seq}/design.yml is missing or not a plain file")
    try:
        design = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise SRError(f"design/locked/v{seq}/design.yml cannot be read") from None
    review = design.get("review") if isinstance(design, dict) else None
    if not isinstance(review, dict):
        raise SRError("the locked design holds no review block")
    eligibility = review.get("eligibility") if isinstance(review.get("eligibility"), dict) else {}
    return Locked(
        version=seq,
        content_hash=str(st.in_force_hash),
        review=review,
        design=design,
        criteria_include=list(eligibility.get("include") or []),
        criteria_exclude=list(eligibility.get("exclude") or []),
    )


def show(project, ws) -> dict:
    """The locked review block with its version, hash and amendment trail (read-only)."""
    locked = require_locked(project, ws)
    trail = lock.state(project, ws).trail
    return {"version": locked.version, "content_hash": locked.content_hash,
            "review": locked.review, "trail": trail}


def check(project, ws) -> dict:
    """Run the leak check on every strategy query of the locked protocol; nothing is sent.

    A query the check refuses is reported with its reason. A strategy that names pubmed is also
    checked with the PubMed field rules (registry.check_pubmed_query).
    """
    locked = require_locked(project, ws)
    rows = []
    for strategy in locked.review.get("strategies") or []:
        if not isinstance(strategy, dict):
            continue
        query = strategy.get("query")
        sources = strategy.get("sources") if isinstance(strategy.get("sources"), list) else []
        reasons = []
        try:
            registry.check_query(query)
        except registry.LeakError as exc:
            reasons.append(str(exc))
        if "pubmed" in sources:
            try:
                registry.check_pubmed_query(query)
            except registry.LeakError as exc:
                reasons.append(str(exc))
        rows.append({"id": strategy.get("id"),
                     "status": "refused" if reasons else "accepted",
                     "reason": "; ".join(reasons) or None})
    return {"version": locked.version, "content_hash": locked.content_hash, "strategies": rows}
