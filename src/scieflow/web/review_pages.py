"""The review pages (ledger §4, §5 W3-W5): the review home, the per-draft round
view, the matrix with its decision forms, and the one decision POST.

Every read goes through `review_views.review_call`, so a core refusal (a
ValueError) or a missing research extra renders as an in-page notice and never
a 500 (ledger F7). The only mutation is the decision POST: one `decide()` per
request, `actor="human"`, answered 303 to the matrix page with `?ok=` or
`?error=` (ledger F8). An unknown run or round is 404.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from scieflow.core import gates as core_gates
from scieflow.web import auth, review_views
from scieflow.web.app import TEMPLATES
from scieflow.web.pages import MUTATE, _project, _run_dir_or_404

router = APIRouter(dependencies=[Depends(auth.require_session)])

#: The two pass files a round view shows for each reviewer (ledger §5 W3).
PASSES = ("blind", "evidence")
#: Gate kinds the matrix lists (core.review.decision_gates.KINDS).
REVIEW_GATE_KINDS = ("review-decision", "review-blocker")
DECISIONS = ("accept", "reject", "defer")


def _round_row(project, slug: str, round_: int) -> tuple[dict | None, str]:
    """This round's row from rounds(), or 404 for a round that does not exist.

    (None, notice) when the review package is unavailable, so the page can still
    say why instead of failing.
    """
    rows, notice = review_views.review_call("rounds", project, slug)
    if rows is None:
        return None, notice
    for row in rows:
        if row["round"] == round_:
            return row, ""
    raise HTTPException(status_code=404, detail="no such review round")


def _authors(pairs: list[dict]) -> list[str]:
    out: list[str] = []
    for pair in pairs:
        if pair["author"] not in out:
            out.append(pair["author"])
    return out


def _members(cluster: dict) -> list[str]:
    return [f"{m['reviewer']} on {m['author']} ({m['pass']}, {m['id']})"
            for m in cluster.get("members", [])]


def _open_review_gates(project, ws, round_: int, decided: dict) -> tuple[list[dict], str]:
    """Open review gates of this round whose cluster has no decision yet."""
    try:
        from scieflow.core.review import decision_gates
    except ImportError:
        return [], review_views.NEEDS_EXTRA
    found = []
    for gate in core_gates.list_gates(ws, "open"):
        if gate.get("kind") not in REVIEW_GATE_KINDS:
            continue
        ref = decision_gates.parse(gate)
        if ref is None or ref["round"] != round_ or ref["cluster"] in decided:
            continue
        found.append({"id": gate["id"], "kind": gate["kind"],
                      "cluster": ref["cluster"], "question": gate.get("question", "")})
    return found, ""


@router.get("/runs/{slug}/review", response_class=HTMLResponse)
def review_home(request: Request, slug: str) -> HTMLResponse:
    project = _project(request)
    ws = _run_dir_or_404(project, slug)
    rounds, notice = review_views.review_call("rounds", project, slug)
    blockers, blocker_notice = review_views.review_call("blockers", project, slug)
    return TEMPLATES.TemplateResponse(request, "review_home.html", {
        "slug": slug,
        "rounds": rounds or [],
        "notice": notice,
        "blockers": blockers or [],
        "blocker_notice": blocker_notice,
        "scite": review_views.scite_control(project),
        "has_discovery": (ws / "discovery").is_dir(),
        "doi_link": review_views.doi_link,
    })


@router.get("/runs/{slug}/review/{round}", response_class=HTMLResponse)
def round_page(request: Request, slug: str, round: int, author: str = "") -> HTMLResponse:
    project = _project(request)
    _run_dir_or_404(project, slug)
    row, notice = _round_row(project, slug, round)
    pairs = (row or {}).get("pairs", [])
    authors = _authors(pairs)
    if author and row is not None and author not in authors:
        raise HTTPException(status_code=404, detail="no such author in this round")
    selected = author or (authors[0] if authors else "")
    panels = []
    for pair in pairs:
        if pair["author"] != selected:
            continue
        panel = {"reviewer": pair["reviewer"]}
        for pass_ in PASSES:
            doc, pass_notice = review_views.review_call(
                "review_pass", project, slug, round, pair["reviewer"], selected, pass_)
            panel[pass_] = doc
            panel[f"{pass_}_notice"] = pass_notice
        panels.append(panel)
    summary, summary_notice = review_views.review_call(
        "evidence_summary", project, slug, round)
    return TEMPLATES.TemplateResponse(request, "review_round.html", {
        "slug": slug,
        "round": round,
        "row": row,
        "notice": notice,
        "authors": authors,
        "selected": selected,
        "panels": panels,
        "summary": summary,
        "summary_notice": summary_notice,
        "doi_link": review_views.doi_link,
    })


@router.get("/runs/{slug}/review/{round}/matrix", response_class=HTMLResponse)
def matrix_page(request: Request, slug: str, round: int, error: str = "",
                ok: str = "") -> HTMLResponse:
    project = _project(request)
    ws = _run_dir_or_404(project, slug)
    _row, notice = _round_row(project, slug, round)
    matrix, matrix_notice = review_views.review_call("matrix", project, slug, round)
    doc, decisions_notice = review_views.review_call("decisions", project, slug, round)
    settings, _ = review_views.review_call("review_settings", project, slug)
    blockers, blocker_notice = review_views.review_call("blockers", project, slug)
    decided = (doc or {}).get("decisions", {})
    mode = (settings or {}).get("review_decisions", "human")
    in_round = [b for b in (blockers or []) if b["round"] == round]
    open_by_cluster = {b["cluster"]: b for b in in_round if b["cluster"]}
    unplaced = [b for b in in_round if b["cluster"] is None]
    clusters = []
    # Blocker clusters first; sorted() is stable, so the matrix order is kept otherwise.
    for c in sorted((matrix or {}).get("clusters", []), key=lambda c: not c["blocker"]):
        cid = c["cluster"]
        verdict = decided.get(cid)
        if c["blocker"]:
            form = "overrule" if cid in open_by_cluster else "none"
        elif mode == "coordinator" and verdict:
            form = "none"
        else:
            form = "decide"
        clusters.append({
            "cluster": cid,
            "title": c.get("title"),
            "severity": c.get("severity"),
            "agreement": c.get("agreement"),
            "recommendation": c.get("recommendation"),
            "rationale": c.get("rationale"),
            "flags": c.get("flags", []),
            "members": _members(c),
            "blocker": c["blocker"],
            "open": open_by_cluster.get(cid),
            "verdict": verdict,
            "form": form,
        })
    gates, gate_notice = _open_review_gates(project, ws, round, decided)
    return TEMPLATES.TemplateResponse(request, "review_matrix.html", {
        "slug": slug,
        "round": round,
        "notice": notice,
        "matrix": matrix,
        "matrix_notice": matrix_notice,
        "decisions_notice": decisions_notice,
        "blocker_notice": blocker_notice,
        "gate_notice": gate_notice,
        "clusters": clusters,
        "unplaced": unplaced,
        "gates": gates,
        "error": error,
        "ok": ok,
        "doi_link": review_views.doi_link,
        "csrf": auth.csrf_token(request),
    })


@router.post("/runs/{slug}/review/{round}/decisions", dependencies=MUTATE)
def record_decision(request: Request, slug: str, round: int,
                    cluster: str = Form(""), decision: str = Form(""),
                    note: str = Form(""), overrule_reason: str = Form("")) -> RedirectResponse:
    """One cluster's decision, from the matrix page. Empty optional fields are
    left out of the decision, so core sees only what the user typed."""
    project = _project(request)
    _run_dir_or_404(project, slug)
    _round_row(project, slug, round)
    entry: dict = {"decision": decision}
    if note.strip():
        entry["note"] = note
    if overrule_reason.strip():
        entry["overrule_reason"] = overrule_reason
    _doc, notice = review_views.review_call(
        "decide", project, slug, round, {cluster: entry}, "human")
    target = f"/runs/{quote(slug)}/review/{round}/matrix"
    if notice:
        return RedirectResponse(f"{target}?error={quote(notice)}", status_code=303)
    return RedirectResponse(f"{target}?ok={quote('decision recorded')}", status_code=303)
