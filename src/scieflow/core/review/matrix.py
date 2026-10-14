"""The review matrix (spec §5; plan T2.3): review/round-N/matrix.json + matrix.md.

Core builds the skeleton mechanically (`skeleton`, written to
`matrix.skeleton.json`): every comment of every valid pass of the round, plus every
flag of the evidence bundle. With no bundle (scite off) only blind passes are
expected and the evidence columns say `off`. Passes that are missing, tampered
with or invalid are listed, never silently clustered.

The coordinator clusters the skeleton into `matrix.json` (review-matrix schema).
`check` validates it against the schema and the skeleton — each comment and each
flag in exactly one cluster, blockers only and always from blocker flags,
evidence quotes verbatim from the bundle for their DOI, scite_status as in the
bundle, agreement consistent with the number of reviewers — and reports every
problem at once. `render_md` turns a checked matrix into a deterministic table.
"""

from __future__ import annotations

import json
from pathlib import Path

from scieflow.core import store
from scieflow.core.review import decisions as decisions_mod
from scieflow.core.review import passes

SKELETON = "matrix.skeleton.json"
MATRIX = "matrix.json"
MARKDOWN = "matrix.md"
_NONE = "—"


class MatrixError(ValueError):
    """A matrix that cannot be built or does not check; `problems` lists every reason."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def ref(member: dict) -> str:
    return f"{member['reviewer']}-on-{member['author']}:{member['pass']}:{member['id']}"


def _items(review: dict, rv: str, au: str, pass_: str) -> list[dict]:
    out = []
    for severity, key in (("major", "problem"), ("minor", "comment")):
        for c in review[severity]:
            member = {"reviewer": rv, "author": au, "pass": pass_, "id": c["id"]}
            item = {"ref": ref(member), "member": member, "severity": severity,
                    "location": c["location"], "text": c[key],
                    "recommendation": review["recommendation"]}
            for extra in ("why_it_matters", "resolution", "changed_from_blind", "evidence"):
                if extra in c:
                    item[extra] = c[extra]
            out.append(item)
    return out


def _bundle(ws: Path, n: int):
    try:
        return passes.load_bundle(ws, n)
    except passes.PassError as exc:
        raise MatrixError([str(exc)]) from exc


def skeleton(ws: Path, round_: int) -> dict:
    """Every comment of every valid pass plus every bundle flag (deterministic)."""
    ws = Path(ws)
    try:
        n = passes._round(round_)
        data = passes.assignment(ws, n)
    except passes.PassError as exc:
        raise MatrixError([str(exc)]) from exc
    loaded = _bundle(ws, n)
    expected = passes.PASSES if loaded else ("blind",)
    items: list[dict] = []
    missing: list[dict] = []
    refused: list[dict] = []
    for pair in data["pairs"]:
        rv, au = pair["reviewer"], pair["author"]
        for pass_ in expected:
            where = {"reviewer": rv, "author": au, "pass": pass_}
            if not passes.pass_path(ws, n, rv, au, pass_).is_file():
                missing.append(where)
                continue
            try:
                review = passes.validate_pass(ws, n, rv, au, pass_)
            except passes.PassError as exc:
                refused.append({**where, "reason": str(exc)})
                continue
            items.extend(_items(review, rv, au, pass_))
    out = {"version": 1, "round": n,
           "scite_status": loaded[1]["scite_status"] if loaded else "off",
           "flags": loaded[1]["flags"] if loaded else [],
           "items": items, "missing": missing, "refused": refused}
    if loaded:
        out["bundle_hash"] = passes.sha256(loaded[0])
    return out


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_skeleton(ws: Path, round_: int) -> Path:
    """Write review/round-N/matrix.skeleton.json; returns its path."""
    sk = skeleton(ws, round_)
    path = passes.round_dir(ws, sk["round"]) / SKELETON
    store._atomic_write(path, dumps(sk))
    return path


# --- check ----------------------------------------------------------------------------------


def _load_matrix(ws: Path, n: int) -> dict:
    try:
        raw = passes._read(ws, passes.round_dir(ws, n) / MATRIX)
    except passes.PassError as exc:
        raise MatrixError([str(exc)]) from exc
    if raw is None:
        raise MatrixError([f"no matrix for round {n}: cluster {SKELETON} into "
                           f"review/round-{n}/{MATRIX} first"])
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise MatrixError([f"round {n} {MATRIX} is not valid JSON: {exc}"]) from exc
    problem = decisions_mod.schema_error("review-matrix", data)
    if problem:
        raise MatrixError([f"round {n} {MATRIX} does not match the review-matrix schema: "
                           f"{problem}"])
    return data


def _check_cluster(c: dict, flags: dict, index, sk: dict, problems: list[str]) -> None:
    cid = c["cluster"]
    reviewers = {m["reviewer"] for m in c["members"]}
    if c["members"]:
        if c["agreement"] == "single" and len(reviewers) > 1:
            problems.append(f"{cid}: agreement single but {len(reviewers)} reviewers raised it")
        elif c["agreement"] != "single" and len(reviewers) == 1:
            problems.append(f"{cid}: agreement {c['agreement']} but only 1 reviewer raised it")
    if c["scite_status"] != sk["scite_status"]:
        problems.append(f"{cid}: scite_status {c['scite_status']} does not match the round's "
                        f"{sk['scite_status']}")
    blocking = [flags[f] for f in c.get("flags") or [] if f in flags and flags[f]["blocker"]]
    if c["blocker"]:
        reason = c["blocker_reason"]
        if not any(f["kind"] == reason for f in blocking):
            problems.append(f"{cid} is marked blocker ({reason}) but has no {reason} flag of "
                            "the evidence bundle")
    for f in blocking:
        if not c["blocker"] or c.get("blocker_reason") != f["kind"]:
            problems.append(f"{f['id']} ({f['kind']}) is a blocker: {cid} must be blocker: true "
                            f"with blocker_reason {f['kind']}")
    for side in ("evidence_for", "evidence_against"):
        for e in c[side]:
            if index is None:
                problems.append(f"{cid}: {side} cites {e['doi']} but the round has no evidence "
                                "bundle (scite off)")
                continue
            why = passes.quote_problem(index, e["doi"], e["quote"])
            if why:
                problems.append(f"{cid}: {why}")
            flag = e.get("flag")
            if flag is not None:
                if flag not in flags:
                    problems.append(f"{cid}: {side} names {flag}, which is not a flag of the "
                                    "evidence bundle")
                elif str(flags[flag].get("doi", e["doi"])).lower() != e["doi"].lower():
                    problems.append(f"{cid}: {side} names {flag} for {e['doi']}, but {flag} is "
                                    f"about {flags[flag]['doi']}")


def check(ws: Path, round_: int) -> dict:
    """The coordinator's matrix.json, checked against the schema and the skeleton."""
    ws = Path(ws)
    try:
        n = passes._round(round_)
    except passes.PassError as exc:
        raise MatrixError([str(exc)]) from exc
    data = _load_matrix(ws, n)
    sk = skeleton(ws, n)
    loaded = _bundle(ws, n)
    index = passes.bundle_index(loaded[1]) if loaded else None
    problems: list[str] = []
    if data["round"] != n:
        problems.append(f"round {data['round']} in {MATRIX}, the file is in round {n}")
    if "bundle_hash" in data and data["bundle_hash"] != sk.get("bundle_hash"):
        problems.append(f"bundle_hash {data['bundle_hash'][:12]}… does not match the round's "
                        "evidence bundle" + ("" if loaded else " (there is none)"))
    for m in sk["missing"]:
        problems.append(f"{m['reviewer']} on {m['author']}: the {m['pass']} pass is missing; "
                        "dispatch it (or re-run it) before the matrix is checked")
    for r in sk["refused"]:
        problems.append(f"{r['reviewer']} on {r['author']}: the {r['pass']} pass is refused: "
                        f"{r['reason']}")
    refs = {i["ref"] for i in sk["items"]}
    flags = {f["id"]: f for f in sk["flags"]}
    seen: set[str] = set()
    member_in: dict[str, list[str]] = {}
    flag_in: dict[str, list[str]] = {}
    for c in data["clusters"]:
        cid = c["cluster"]
        if cid in seen:
            problems.append(f"cluster {cid} appears twice")
        seen.add(cid)
        for m in c["members"]:
            r = ref(m)
            if r not in refs:
                problems.append(f"{cid}: {r} is not a comment of a valid pass in round {n}")
            else:
                member_in.setdefault(r, []).append(cid)
        for f in c.get("flags") or []:
            if f not in flags:
                problems.append(f"{cid}: {f} is not a flag of the round's evidence bundle")
            else:
                flag_in.setdefault(f, []).append(cid)
        _check_cluster(c, flags, index, sk, problems)
    for item in sk["items"]:
        where = member_in.get(item["ref"], [])
        if not where:
            problems.append(f"{item['ref']} is not in any cluster")
        elif len(where) > 1:
            problems.append(f"{item['ref']} is in more than one cluster ({', '.join(where)})")
    for fid, f in flags.items():
        where = flag_in.get(fid, [])
        if not where:
            problems.append(f"{fid} ({f['kind']}) is not in any cluster")
        elif len(where) > 1:
            problems.append(f"{fid} is in more than one cluster ({', '.join(where)})")
    if problems:
        raise MatrixError(problems)
    return data


# --- markdown -------------------------------------------------------------------------------


def _cell(value) -> str:
    text = " ".join(str(value).split())
    return text.replace("|", "\\|") if text else _NONE


def _evidence_cell(c: dict, side: str) -> str:
    if c["scite_status"] == "off" and not c[side]:
        return "off"
    if not c[side]:
        return _NONE
    parts = []
    for e in c[side]:
        tags = [t for t in (e.get("stance"), e.get("flag")) if t]
        parts.append(e["doi"] + (f" ({', '.join(tags)})" if tags else ""))
    return _cell("; ".join(parts))


def render_md(data: dict) -> str:
    """Deterministic markdown of a checked matrix (cluster order as in the file)."""
    lines = [f"# Review matrix — round {data['round']}", ""]
    if data.get("bundle_hash"):
        lines += [f"Evidence bundle sha256 {data['bundle_hash']}.", ""]
    lines += ["| Cluster | Title | Severity | Agreement | Evidence for | Reviewers | "
              "Evidence against | Blocker | scite | Recommendation |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for c in data["clusters"]:
        reviewers = len({m["reviewer"] for m in c["members"]})
        blocker = c.get("blocker_reason", "yes") if c["blocker"] else "no"
        row = [c["cluster"], c.get("title", ""), c.get("severity", ""), c["agreement"],
               _evidence_cell(c, "evidence_for"), str(reviewers),
               _evidence_cell(c, "evidence_against"), blocker, c["scite_status"],
               c["recommendation"]]
        lines.append("| " + " | ".join(r if r in ("off", _NONE) else _cell(r) for r in row)
                     + " |")
    lines += ["", "## Clusters"]
    for c in data["clusters"]:
        lines += ["", f"### {c['cluster']} — {_cell(c.get('title', ''))}", ""]
        members = ", ".join(ref(m) for m in c["members"]) or _NONE
        lines.append(f"- Members: {_cell(members)}")
        lines.append(f"- Flags: {_cell(', '.join(c.get('flags') or []))}")
        for side, label in (("evidence_for", "Evidence for"),
                            ("evidence_against", "Evidence against")):
            for e in c[side]:
                tags = ", ".join(t for t in (e["source"], e.get("stance"), e.get("flag")) if t)
                lines.append(f"- {label}: {_cell(e['doi'])} ({_cell(tags)}): "
                             f"“{_cell(e['quote'])}”")
        lines.append(f"- Rationale: {_cell(c['rationale'])}")
    return "\n".join(lines) + "\n"


def write_md(ws: Path, round_: int, data: dict) -> Path:
    path = passes.round_dir(ws, round_) / MARKDOWN
    store._atomic_write(path, render_md(data))
    return path
