"""Shared review helpers for the web layer (ledger §1, §5 W1/W2).

The review rules live in core. This module is the web's glue: the scite toggle
mapping, one form mapper for both the live preview and Apply, the hardened DOI
link, and the degradation rule. A core `ValueError` (ReviewError, SettingsError,
AssignmentError, ...) is rendered as an escaped in-page notice; nothing here
shows a traceback. The review package needs jsonschema (the `research` extra),
so it is imported lazily: without it every review panel shows NEEDS_EXTRA.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from scieflow.core import service

NEEDS_EXTRA = "review needs the research extra"
#: Blank reviewer rows shown after the existing ones, so a user can add reviewers.
BLANK_POOL_ROWS = 2
#: The pool's `covers` values; mirrors scieflow.core.review.settings.COVERS.
COVERS = ("all", "auto")

_DOI = re.compile(r"10\.\d{4,9}/\S+")
_POOL_FIELD = re.compile(r"pool-(\d+)-(agent|covers)")


class FormMapError(ValueError):
    """A review form field the mapper refuses. Core is never called for it."""


def _review():
    """The review service module, or None when the research extra is missing."""
    try:
        from scieflow.core.review import service as review_service
    except ImportError:
        return None
    return review_service


def review_call(name: str, *args, **kwargs):
    """Call one review-service function under the degradation rule.

    Returns (value, notice). The notice is "" on success, NEEDS_EXTRA when the
    review package cannot be imported, or the refusal's own text for a core
    `ValueError`. Never raises a ValueError to the caller.
    """
    svc = _review()
    if svc is None:
        return None, NEEDS_EXTRA
    try:
        return getattr(svc, name)(*args, **kwargs), ""
    except ValueError as exc:
        return None, str(exc)


def _text(form, name: str) -> str:
    value = form.get(name)
    return value.strip() if isinstance(value, str) else ""


# --- W1: the scite toggle -------------------------------------------------------------


def scite_value(form) -> str | None:
    """The `scite` value the toggle asks for, or None to write nothing.

    Sentinel `scite_present` absent -> None (the control was not shown, so
    nothing is written). Present and unchecked -> off. Checked -> ask, or
    approved when "don't ask again" is also checked. "Don't ask again" without
    "use" is off: a consent cannot be given without the use itself.
    """
    if not _text(form, "scite_present"):
        return None
    if not _text(form, "scite_use"):
        return "off"
    return "approved" if _text(form, "scite_no_ask") else "ask"


def scite_control(project, *, use: bool = True, no_ask: bool = False) -> dict:
    """What the scite toggle shows: enabled only when scite is configured.

    `reason` is scite's own explanation (escaped by the template), never the
    token. `quota` is the MCP calls left today when scite reports a number.
    """
    status, notice = review_call("scite_status", project)
    if status is None:
        return {"enabled": False, "reason": notice, "quota": None,
                "use": use, "no_ask": no_ask}
    enabled = bool(status.get("configured"))
    quota = status.get("quota_remaining")
    if isinstance(quota, bool) or not isinstance(quota, (int, float)):
        quota = None
    reason = "" if enabled else str(status.get("reason") or "scite is not configured")
    return {"enabled": enabled, "reason": reason, "quota": quota,
            "use": use, "no_ask": no_ask}


def start_scite(project, form) -> str | None:
    """The scite value a /start submission asks for, honouring configuration.

    Server-side enforcement: when scite is not configured nothing is written,
    even if the form was crafted to carry the sentinel.
    """
    if not scite_control(project)["enabled"]:
        return None
    return scite_value(form)


def write_scite(project, slug: str, value: str | None) -> str:
    """Record one scite value for a run. Returns "" or the refusal text (nothing written)."""
    if value is None:
        return ""
    svc = _review()
    if svc is None:
        return NEEDS_EXTRA
    try:
        svc.set_review_settings(project, slug, {"scite": value}, actor="human")
    except ValueError as exc:
        return str(exc)
    return ""


# --- W2: the reviewer pool editor --------------------------------------------------


def _pool_rows(form) -> list[dict]:
    """The pool rows the form describes, in index order, blank agents dropped."""
    rows: dict[int, dict] = {}
    for key in form.keys():
        match = _POOL_FIELD.fullmatch(key)
        if match:
            index, field = int(match.group(1)), match.group(2)
            rows.setdefault(index, {})[field] = _text(form, key)
    out = []
    for index in sorted(rows):
        agent = rows[index].get("agent", "")
        if not agent:
            continue
        entry = {"agent": agent}
        if rows[index].get("covers"):
            entry["covers"] = rows[index]["covers"]
        out.append(entry)
    return out


def review_changes_from_form(form, *, scite_enabled: bool = True) -> dict:
    """The review changes one form describes. The ONE mapper, used by the live
    preview (GET) and by Apply (POST), so the preview shows what Apply writes.

    It owns exactly: pool, reviews_per_draft, allow_same_family_review,
    allow_self_review and scite. A key is included only when its section was
    shown (a sentinel field), so an unrelated form never wipes a setting.
    "Reset to default" (pool_reset) yields {"pool": None}: the key is removed,
    so the run inherits research.draft-reviewers, which is not an empty list.
    A garbage reviews_per_draft raises FormMapError without reaching core.
    """
    if _text(form, "pool_reset"):
        return {"pool": None}
    changes: dict = {}
    if _text(form, "pool_present"):
        changes["pool"] = _pool_rows(form)
    if "reviews_per_draft" in form.keys():
        raw = _text(form, "reviews_per_draft")
        try:
            count = int(raw)
        except ValueError:
            raise FormMapError("reviews per draft must be a whole number, 1 or more") from None
        if count < 1:
            raise FormMapError("reviews per draft must be a whole number, 1 or more")
        changes["reviews_per_draft"] = count
    if _text(form, "switches_present"):
        changes["allow_same_family_review"] = bool(_text(form, "allow_same_family_review"))
        changes["allow_self_review"] = bool(_text(form, "allow_self_review"))
    if scite_enabled:
        scite = scite_value(form)
        if scite is not None:
            changes["scite"] = scite
    return changes


def apply_changes(project, slug: str, form) -> tuple[bool, str]:
    """Apply the form's review changes to a run. Returns (ok, message)."""
    svc = _review()
    if svc is None:
        return False, NEEDS_EXTRA
    try:
        changes = review_changes_from_form(
            form, scite_enabled=scite_control(project)["enabled"])
        svc.set_review_settings(project, slug, changes, actor="human")
    except ValueError as exc:
        return False, str(exc)
    return True, "review settings saved"


def editor_context(project, slug: str, submitted=None) -> dict:
    """The agents page's review section for one run.

    `submitted` is the GET preview's query (or None). When it is given the
    editor shows what was typed and a live preview of the assignment (nothing
    is written); otherwise it shows the run's saved settings.
    """
    svc = _review()
    if svc is None:
        return {"available": False, "notice": NEEDS_EXTRA}
    settings, notice = review_call("review_settings", project, slug)
    settings = settings or {}
    try:
        agent_states = service.agent_settings(project, slug)["agents"]
    except ValueError as exc:
        agent_states = {}
        notice = notice or str(exc)
    enabled = sorted(name for name, fields in agent_states.items()
                     if (fields.get("enabled") or {}).get("value") is True)

    pool = settings.get("pool")
    same = bool(settings.get("allow_same_family_review"))
    self_review = bool(settings.get("allow_self_review"))
    reviews = settings.get("reviews_per_draft", "")
    scite = settings.get("scite", "ask")
    preview, preview_notice = None, ""
    if submitted is not None:
        try:
            changes = review_changes_from_form(
                submitted, scite_enabled=scite_control(project)["enabled"])
        except FormMapError as exc:
            changes, preview_notice = None, str(exc)
        if changes is not None:
            if "pool" in changes:
                pool = changes["pool"]
            same = changes.get("allow_same_family_review", same)
            self_review = changes.get("allow_self_review", self_review)
            reviews = changes.get("reviews_per_draft", reviews)
            scite = changes.get("scite", scite)
            preview, preview_notice = _preview(svc, project, slug, changes)
    rows = [dict(row) for row in (pool or [])]
    rows += [{"agent": "", "covers": "auto"} for _ in range(BLANK_POOL_ROWS)]
    return {
        "available": True,
        "notice": notice,
        "preview": preview,
        "preview_notice": preview_notice,
        "agents": enabled,
        "rows": rows,
        "covers": COVERS,
        "inherits": pool is None,
        "same": same,
        "self": self_review,
        "reviews": reviews,
        "scite": scite_control(project, use=scite != "off", no_ask=scite == "approved"),
    }


def _preview(svc, project, slug: str, changes: dict):
    try:
        return svc.preview_assignment(project, slug, changes), ""
    except ValueError as exc:
        return None, str(exc)


# --- §1: the DOI link helper ------------------------------------------------------


def doi_link(doi) -> str | None:
    """A doi.org link for a plain DOI, or None (print the DOI as text instead).

    Only `10.NNNN/<non-space>` values pass; a query, a fragment or a `..`
    segment is refused. The suffix is percent-quoted with `/` kept.
    """
    if not isinstance(doi, str) or not _DOI.fullmatch(doi):
        return None
    if any(mark in doi for mark in "?#") or ".." in doi:
        return None
    return "https://doi.org/" + quote(doi, safe="/")
