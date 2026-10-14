"""Shared review helpers for the web layer (ledger §1, §5 W1/W2).

The review rules live in core. This module is the web's glue: the scite toggle
mapping, one form mapper for both the live preview and Apply, the hardened DOI
link, and the degradation rule. A core `ValueError` (ReviewError, SettingsError,
AssignmentError, ...) is rendered as an escaped in-page notice; nothing here
shows a traceback. The review package needs jsonschema (the `research` extra),
so it is imported lazily: without it every review panel shows NEEDS_EXTRA.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import quote

from scieflow.core import gates as core_gates
from scieflow.core import service

NEEDS_EXTRA = "review needs the research extra"
#: Blank reviewer rows shown after the existing ones, so a user can add reviewers.
BLANK_POOL_ROWS = 2
#: The pool's `covers` values; mirrors scieflow.core.review.settings.COVERS.
COVERS = ("all", "auto")

#: ASCII only: \d must not accept other Unicode digits in a DOI (R4 C6.1).
_DOI = re.compile(r"10\.\d{4,9}/\S+", re.ASCII)
_POOL_FIELD = re.compile(r"pool-(\d+)-(agent|covers)")
#: What a RecursionError from json.loads on deep nesting says to the user (R4 C2).
DEEP_NOTICE = "nested too deeply; not shown"


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
    `ValueError`. A RecursionError (deep JSON, R4 C2) or an OSError is a notice
    too. Never raises to the caller.
    """
    svc = _review()
    if svc is None:
        return None, NEEDS_EXTRA
    try:
        return getattr(svc, name)(*args, **kwargs), ""
    except RecursionError:
        return None, f"review file {DEEP_NOTICE}"
    except (ValueError, OSError) as exc:
        return None, str(exc)


def gates_safe(ws, state: str | None, label: str) -> tuple[list[dict], str]:
    """core.gates.list_gates under the degradation rule (ledger O4).

    list_gates parses every gate file and indexes `state`, so one corrupt,
    non-object or state-less file raises here. That is one notice line
    (escaped by the template), never a 500 on the page that asked.
    """
    try:
        return core_gates.list_gates(Path(ws), state), ""
    except RecursionError:
        return [], f"{label} could not be read: {DEEP_NOTICE}"
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return [], f"{label} could not be read: {' '.join(str(exc).split())}"


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
    preview (POST) and by Apply (POST), so the preview shows what Apply writes.

    It owns exactly: pool, reviews_per_draft, allow_same_family_review,
    allow_self_review and scite. A key is included only when its section was
    shown (a sentinel field), so an unrelated form never wipes a setting.
    "Reset to default" (pool_reset) yields {"pool": None}: the key is removed,
    so the run inherits research.draft-reviewers, which is not an empty list.
    An inheriting run (pool_inherits, owner finding O1) that gained no filled
    row keeps inheriting: its pool is left out, not written as [].
    A garbage reviews_per_draft raises FormMapError without reaching core.
    """
    if _text(form, "pool_reset"):
        return {"pool": None}
    changes: dict = {}
    if _text(form, "pool_present"):
        rows = _pool_rows(form)
        if not (_text(form, "pool_inherits") and not rows):
            changes["pool"] = rows
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
    """Apply the form's review changes to a run. Returns (ok, message).

    Only keys whose mapped value differs from the run's current effective
    setting are written (owner finding O1): an unchanged key is never pinned
    into the run's config, so applying one section cannot rewrite another.
    """
    svc = _review()
    if svc is None:
        return False, NEEDS_EXTRA
    try:
        changes = review_changes_from_form(
            form, scite_enabled=scite_control(project)["enabled"])
        current = svc.review_settings(project, slug)
        changes = {key: value for key, value in changes.items()
                   if current.get(key) != value}
        if not changes:
            return True, "no review settings changed"
        svc.set_review_settings(project, slug, changes, actor="human")
    except ValueError as exc:
        return False, str(exc)
    return True, "review settings saved"


def editor_context(project, slug: str, submitted=None) -> dict:
    """The agents page's review section for one run.

    `submitted` is the POST preview's form (or None). When it is given the
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
    rows += [{"agent": "", "covers": ""} for _ in range(BLANK_POOL_ROWS)]
    inherited, inherited_notice = [], ""
    if pool is None:
        plan, inherited_notice = review_call("preview_assignment", project, slug)
        for reviewer in (plan or {}).get("reviewers", []):
            name = reviewer.get("agent") if isinstance(reviewer, dict) else None
            if name and name not in inherited:
                inherited.append(name)
    return {
        "available": True,
        "notice": notice,
        "preview": preview,
        "preview_notice": preview_notice,
        "agents": enabled,
        "rows": rows,
        "covers": COVERS,
        "inherits": pool is None,
        "inherited": inherited,
        "inherited_notice": inherited_notice,
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


def run_review_context(project, slug: str, ws) -> dict:
    """What the run page shows of review: the blocker banner and the nav links.

    Never raises (ledger F7): a core refusal or a missing research extra is one
    notice line, and the run page renders around it.
    """
    rounds, _ = review_call("rounds", project, slug)
    blockers, notice = review_call("blockers", project, slug)
    blockers = blockers or []
    return {
        "has_review": bool(rounds) or (Path(ws) / "review").is_dir(),
        "has_discovery": (Path(ws) / "discovery").is_dir(),
        "blockers": blockers,
        "blocker_notice": notice,
        "newest_round": max((b["round"] for b in blockers), default=None),
    }


# --- W6: the workbench's read-only review outcome --------------------------------------


def _comment_text(comment: dict) -> str:
    """A major comment carries `problem`, a minor one `comment` (revise._comments)."""
    return str(comment.get("problem") or comment.get("comment") or "")


def _outcome_author(ws, n: int, author: str, cluster_for) -> dict:
    """One draft's accepted clusters and the ids decided otherwise, or a notice."""
    entry = {"author": author, "accepted": [], "rejected": [], "deferred": [],
             "undecided": [], "notice": ""}
    try:
        view = cluster_for(ws, n, author)
    except RecursionError:
        entry["notice"] = f"review outcome for {author} could not be read: {DEEP_NOTICE}"
        return entry
    except (ValueError, OSError) as exc:
        entry["notice"] = f"review outcome for {author} could not be read: " \
            f"{' '.join(str(exc).split())}"
        return entry
    for cluster in view["accepted"]:
        decision = cluster["decision"]
        entry["accepted"].append({
            "id": cluster["cluster"],
            "title": cluster.get("title") or "",
            "severity": cluster.get("severity") or "",
            "decision": decision.get("decision") or "",
            "actor": decision.get("actor") or "",
            "note": decision.get("note") or "",
            "comments": [{"reviewer": c.get("reviewer", ""), "pass": c.get("pass", ""),
                          "severity": c.get("severity", ""), "id": c.get("id", ""),
                          "text": _comment_text(c)} for c in cluster["comments"]],
        })
    for key in ("rejected", "deferred", "undecided"):
        entry[key] = list(view[key])
    return entry


def drafts_review_context(project, slug: str, ws, fallback_authors) -> dict | None:
    """The workbench's review outcome for the latest round with recorded decisions.

    None when no round is decided, so the page shows no panel. Read-only, and it
    never raises (ledger F7, D2): a missing research extra or a core refusal is one
    notice line, and a draft whose review cannot be read gets its own notice.
    The authors are the decided round's assignment pairs, else `fallback_authors`.
    """
    rounds, notice = review_call("rounds", project, slug)
    if rounds is None:
        return {"round": None, "notice": notice, "authors": []}
    decided = [row for row in rounds if row.get("decisions")]
    if not decided:
        return None
    row = max(decided, key=lambda r: r["round"])
    n = row["round"]
    authors: list[str] = []
    for pair in row.get("pairs", []):
        if pair["author"] not in authors:
            authors.append(pair["author"])
    if not authors:
        authors = list(fallback_authors)
    try:
        from scieflow.core.review import revise
    except ImportError:
        return {"round": n, "notice": NEEDS_EXTRA, "authors": []}
    ws = Path(ws)
    return {"round": n, "notice": "", "authors": [
        _outcome_author(ws, n, author, revise.clusters_for) for author in authors]}


# --- W7: the hypothesis tournament view ----------------------------------------------

#: The largest tournament.json the page will read (ledger W7: refuse > 2 MiB).
REPORT_LIMIT = 2 * 1024 * 1024

_SYMLINK_NOTICE = "tournament.json is reached through a symlink; not shown"
_NOT_DIR_NOTICE = "report/ is not a directory; tournament.json not shown"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW


class _Refused(Exception):
    """A symlink or a non-directory on the report path; its message is the notice."""


def _is_symlink(dir_fd: int, name: str) -> bool:
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _open_report(ws: Path) -> int:
    """Open ws/report/tournament.json by a descriptor chain that never follows a symlink (O7).

    ws -> report (a directory, no follow) -> tournament.json (no follow). Each hop is opened
    relative to the descriptor before it, so no path is resolved by name after a check.
    Raises FileNotFoundError for an absent entry, _Refused for a symlink or non-directory at
    report/, and OSError otherwise. Every directory descriptor is closed on every path.
    """
    ws_fd = os.open(ws, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            rep_fd = os.open("report", _DIR_FLAGS, dir_fd=ws_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP or (exc.errno == errno.ENOTDIR
                                            and _is_symlink(ws_fd, "report")):
                raise _Refused(_SYMLINK_NOTICE) from None
            if exc.errno == errno.ENOTDIR:
                raise _Refused(_NOT_DIR_NOTICE) from None
            raise
        try:
            return os.open("tournament.json", _FILE_FLAGS, dir_fd=rep_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _Refused(_SYMLINK_NOTICE) from None
            raise
        finally:
            os.close(rep_fd)
    finally:
        os.close(ws_fd)


def _decisions_module():
    """The core decisions module (it validates the schema), or None without the extra."""
    try:
        from scieflow.core.review import decisions
    except ImportError:
        return None
    return decisions


def load_tournament(ws) -> tuple[dict | None, str]:
    """ws/report/tournament.json, validated, or a notice saying why it is not shown.

    The one fixed path the web reads (ledger F9): nothing request-derived. An absent
    report is (None, "") so the page shows its empty state. A symlink at report/ or at
    tournament.json, a non-directory at report/, a symlink or non-regular file at the
    path (a FIFO would block the page, R4 C1), an oversized file, invalid or deeply
    nested JSON, a schema mismatch, or an unreadable file is a notice.

    The path is opened by a descriptor chain that never follows a symlink (O7), and the
    checks run on that descriptor: the size is what was read, not what stat reported.
    """
    try:
        fd = _open_report(Path(ws))
    except FileNotFoundError:
        return None, ""
    except _Refused as exc:
        return None, str(exc)
    except OSError as exc:
        return None, f"tournament.json could not be read: {' '.join(str(exc).split())}"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "tournament.json is not a regular file; not shown"
        if st.st_size > REPORT_LIMIT:
            return None, "tournament.json is larger than 2 MiB; not shown"
        raw = _read_bounded(fd)
    except OSError as exc:
        return None, f"tournament.json could not be read: {' '.join(str(exc).split())}"
    finally:
        os.close(fd)
    if len(raw) > REPORT_LIMIT:
        return None, "tournament.json is larger than 2 MiB; not shown"
    try:
        data = json.loads(raw.decode("utf-8"))
    except RecursionError:
        return None, f"tournament.json is not valid JSON: {DEEP_NOTICE}"
    except ValueError as exc:
        return None, f"tournament.json is not valid JSON: {' '.join(str(exc).split())}"
    decisions = _decisions_module()
    if decisions is None:
        return None, NEEDS_EXTRA
    error = decisions.schema_error("hypothesis-tournament", data)
    if error:
        return None, f"tournament.json does not match the contract: {error}"
    return _with_elo_text(data), ""


def _read_bounded(fd: int) -> bytes:
    """At most REPORT_LIMIT + 1 bytes from the descriptor: enough to see it is too big."""
    chunks: list[bytes] = []
    total = 0
    while total <= REPORT_LIMIT:
        chunk = os.read(fd, min(65536, REPORT_LIMIT + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _elo_text(elo) -> str:
    """An Elo for display: one decimal, or the value's own text capped at 32 chars.

    The schema accepts any JSON number, so a 4 000-digit integer must not reach
    `"%.1f"` (OverflowError, R4 C4).
    """
    try:
        return f"{float(elo):.1f}"
    except OverflowError:
        return str(elo)[:32]


def _with_elo_text(data: dict) -> dict:
    """A copy of a validated tournament with each hypothesis's `elo_text` added."""
    out = dict(data)
    out["hypotheses"] = [dict(h, elo_text=_elo_text(h["elo"])) for h in data["hypotheses"]]
    return out


def hypothesis_gate(ws) -> tuple[dict | None, str]:
    """The newest hypothesis-selection gate (any state), or (None, notice).

    list_gates returns non-object gate files untouched when no state is asked for
    (core/gates.py), so each entry is checked before it is read (R4 C4).
    """
    gates, notice = gates_safe(ws, None, "gates")
    chosen = None
    for gate in gates:
        if (not isinstance(gate, dict) or gate.get("kind") != "hypothesis-selection"
                or not gate.get("id")):
            continue
        key = (str(gate.get("opened") or ""), str(gate["id"]))
        if chosen is None or key > chosen[0]:
            chosen = (key, gate)
    return (chosen[1] if chosen else None), notice


# --- R4 C3: a reviewer pass normalised before the round page renders it -----------------

_PASS_LISTS = ("major", "minor")


def _scalar(value):
    """Display text for a scalar field, or None when the value is a container."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return None
    return str(value)


def _item(mapping: dict, path: str, problems: list[str]) -> dict:
    """Copy one major/minor item: scalar fields as text; its `evidence` list is checked."""
    out = {}
    for key, value in mapping.items():
        if key == "evidence":
            continue
        text = _scalar(value)
        if text is None:
            problems.append(f"{path}.{key}")
            text = ""
        out[key] = text
    if "evidence" in mapping:
        out["evidence"] = _evidence(mapping["evidence"], f"{path}.evidence", problems)
    return out


def _evidence(value, path: str, problems: list[str]) -> list[dict]:
    """The evidence list of one item. Each entry is scalar-only (R5 N1)."""
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(path)
        return []
    entries = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            problems.append(f"{path}[{index}]")
            continue
        entries.append(_entry(entry, f"{path}[{index}]", problems))
    return entries


def _entry(mapping: dict, path: str, problems: list[str]) -> dict:
    """One evidence entry: scalar fields only. A container value, a nested `evidence` included,
    is left out and named in problems. The walk stops here, so depth never matters (R5 N1)."""
    out = {}
    for key, value in mapping.items():
        text = _scalar(value)
        if text is None:
            problems.append(f"{path}.{key}")
            continue
        out[key] = text
    return out


#: R5 NIT-1: at most this many problem names, each cut to this many characters.
PROBLEM_LIMIT = 10
PROBLEM_NAME_LIMIT = 60


def _capped(problems: list[str]) -> list[str]:
    """The problem names the notice shows: the first PROBLEM_LIMIT, names truncated, then a count."""
    names = []
    for name in problems[:PROBLEM_LIMIT]:
        if len(name) > PROBLEM_NAME_LIMIT:
            name = name[:PROBLEM_NAME_LIMIT] + "\u2026"
        names.append(name)
    if len(problems) > PROBLEM_LIMIT:
        names.append(f"and {len(problems) - PROBLEM_LIMIT} more")
    return names


def clean_pass(doc) -> tuple[dict, list[str]]:
    """A review pass the round page can render, and the fields it had to repair.

    `major` and `minor` must be lists of objects and each `evidence` a list of
    objects; anything else is dropped and named in the returned problems. Scalar
    fields are shown as text (None as ""). The input is never changed: the result
    is a new dict, so a cached document stays as core returned it (R4 C3).

    The walk is exactly two levels deep (item, evidence entry) and never recursive
    (R5 N1). The problem names are capped (R5 NIT-1).
    """
    if not isinstance(doc, dict):
        return {}, ["pass file is not an object"]
    problems: list[str] = []
    clean = dict(doc)
    for field in _PASS_LISTS:
        if field not in doc:
            continue
        value = doc[field]
        if value is None:
            clean[field] = []
            continue
        if not isinstance(value, list):
            problems.append(field)
            clean[field] = []
            continue
        items = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                problems.append(f"{field}[{index}]")
                continue
            items.append(_item(item, f"{field}[{index}]", problems))
        clean[field] = items
    return clean, _capped(problems)

