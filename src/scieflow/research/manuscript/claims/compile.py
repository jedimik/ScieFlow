"""The claim check (M-G 5.6-5.8): codes C001-C006 over the claim ledger.

`assemble` builds the ledger in memory with the same steps as `ledger.build` (it writes nothing); `check_run`
(the package refusal, M-G 5.8) checks that assembly, and `check_ledger` (`claims check`) first refuses a
ledger built from another manuscript (`ledger-stale`). Every finding names a claim, a run-relative path or a
rule id, never a value read from an evidence file (M-G 5.16). Nothing here writes a file, runs a subprocess
other than the fixed `git merge-base` of C006, or reaches the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from scieflow.core import events
from scieflow.core.review import service as review_service
from scieflow.core.review.claims import parser as claim_parser
from scieflow.core.review.claims import safe_doi
from scieflow.research.manuscript import tex
from scieflow.research.manuscript.claims import evidence as ev
from scieflow.research.manuscript.claims import ledger as ledger_mod
from scieflow.research.manuscript.claims import numbers as numbers_mod
from scieflow.research.manuscript.claims import refs as refs_mod
from scieflow.research.manuscript.claims import resolve as resolve_mod
from scieflow.research.manuscript.claims import units as units_mod

VERSION = 1
RELATIONS = frozenset({"=", "<", "<=", ">", ">="})
TEXT_EXT = (".csv", ".tsv", ".json", ".txt", ".md", ".tex", ".yml", ".yaml")
MAX_TEXT_BYTES = 20 * 1024 * 1024
SAFE_KEY = re.compile(r"[A-Za-z0-9_.:/+-]{1,200}")
SHA_RE = re.compile(r"[0-9a-fA-F]{7,40}")
GIT_TIMEOUT_S = 10

# Rule id -> check code (M-G 5.6). A rule not listed is uncoded (its findings carry `code: null`).
CODE_OF = {
    "orphan": "C001", "ref-unresolved": "C001", "ref-ambiguous": "C001", "ref-unsafe": "C001",
    "ref-too-long": "C001", "ref-unsafe-doi": "C001",
    "number-missing": "C002", "artifact-altered": "C002",
    "evidence-changed": "C003", "source-corrected": "C003",
    "blocked-reference": "C005",
    "code-dirty": "C006", "code-not-ancestor": "C006",
}
WAIVABLE = frozenset({"C001", "C002", "C003", "C006"})


def key_token(key) -> str:
    """The safe token of a bibliography key (M-G 5.6, the identity_gates._token rule)."""
    key = str(key)
    if SAFE_KEY.fullmatch(key):
        return key
    return "k-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# ---- numbers (M-G 5.5) ------------------------------------------------------------------------------------

NUMBER_IN_TEXT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?:[eE]([-+]?\d+))?(?![\w]|\.\d)")


def evidence_values(text: str) -> list[Decimal]:
    """The numeric tokens of an evidence file as Decimals. A hyphen glued to a word is a separator."""
    values = []
    for match in NUMBER_IN_TEXT.finditer(text):
        start = match.start()
        sign = ""
        if start >= 1 and text[start - 1] == "-" and not (start >= 2 and (text[start - 2].isalnum()
                                                                          or text[start - 2] in "_.")):
            sign = "-"
        raw = match.group(1) + (f"e{match.group(2)}" if match.group(2) else "")
        try:
            values.append(Decimal(sign + raw))
        except InvalidOperation:
            continue
    return values


def _satisfies(x: Decimal, relation: str, t: Decimal) -> bool:
    return {"<": x < t, "<=": x <= t, ">": x > t, ">=": x >= t}[relation]


def _rounded(x: Decimal, decimals: int, mode) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-decimals), rounding=mode)


def value_matches(t: Decimal, decimals: int, percent: bool, relation: str, a: Decimal) -> bool:
    """Whether the manuscript value `t` (with `decimals`, `percent`, `relation`) matches evidence value `a`.

    `=`: half-up or half-even rounding of `a` to `decimals` equals `t`; with no decimals `a` must be integral
    and equal. With `percent`, `a * 100` is tried too. A relation (`<`, `<=`, `>`, `>=`) is satisfied by `a`.
    Decimal arithmetic only (M-G 5.5).
    """
    if relation not in RELATIONS:
        return False
    candidates = [a] + ([a * 100] if percent else [])
    for x in candidates:
        try:
            if relation != "=":
                if _satisfies(x, relation, t):
                    return True
            elif decimals == 0:
                if x == x.to_integral_value() and x == t:
                    return True
            elif _rounded(x, decimals, ROUND_HALF_UP) == t or _rounded(x, decimals, ROUND_HALF_EVEN) == t:
                return True
        except InvalidOperation:
            continue
    return False


# ---- the assembled ledger ----------------------------------------------------------------------------------

@dataclass
class _Claim:
    record: dict
    outcomes: list           # resolve_mod.Outcome of each reference, in record order


@dataclass
class Assembly:
    run: Path
    tree_sha256: str
    claims: list             # of _Claim, in ledger order
    problems: list           # finding dicts from the reference and cite resolution (no code yet)
    records: list            # the ledger records, as ledger.build writes them
    claims_sha256: str
    order: dict = field(default_factory=dict)


def _problem(unit, item: dict, ref: dict | None = None) -> dict:
    out = {"claim": unit.id, "file": unit.file, "line": unit.line, "rule": item["rule"],
           "severity": item["severity"], "message": item["message"]}
    if ref is not None:
        out["ref"] = f"{ref['type']}:{ref['id']}"
    return out


def assemble(run_folder, main: str = "main.tex") -> Assembly:
    """The ledger of the run's manuscript, built in memory (M-G 5.3). Writes nothing."""
    run = Path(run_folder)
    manuscript = run / "manuscript"
    try:
        tree = tex.read_tree(manuscript, main, keep_comments=True)
    except tex.TexError as exc:
        raise ledger_mod.ClaimsError(exc.rule, exc.message) from exc
    text = ledger_mod._bib_text(run)
    parser = claim_parser()
    bib = parser.parse_bib(text)
    bib_keys = {match.group(2) for match in parser.ENTRY_RE.finditer(text)}
    order = {name: index for index, name in enumerate(tree.files)}
    problems: list = []
    claims = []
    for unit in units_mod.extract_units(tree):
        found, parse_problems = refs_mod.parse_refs("\n".join(unit.comments), unit.text, bib)
        problems += [_problem(unit, resolve_mod.finding(p["rule"], p["message"])) for p in parse_problems]
        keys = refs_mod.cite_keys(unit.text)
        nums = numbers_mod.extract(unit.text)
        if not (found or keys or nums):
            continue
        claims.append((unit, found, keys, nums))
    if len(claims) > ledger_mod.MAX_CLAIMS:
        raise ledger_mod.ClaimsError("claims-limit",
                                     f"{len(claims)} claims: the ledger holds at most {ledger_mod.MAX_CLAIMS}")
    numbered = units_mod.number([unit for unit, _found, _keys, _nums in claims])
    try:
        ctx = resolve_mod.open_context(run, bib_keys, bib)
    except ev.EvidenceError as exc:
        raise ledger_mod.ClaimsError(exc.rule, exc.message) from exc
    previous = ledger_mod._previous(run)
    entries = []
    for unit, (_u, found, keys, nums) in zip(numbered, claims):
        outcomes = [resolve_mod.resolve_ref(ctx, ref) for ref in found]
        for outcome in outcomes:
            problems += [_problem(unit, item, outcome.ref) for item in outcome.findings]
        for key in keys:
            problems += [_problem(unit, item) for item in resolve_mod.resolve_cite(ctx, key)]
        record = ledger_mod._record(unit, found, keys, nums)
        old = previous.get(unit.id)
        carried = dict(old.get("pins", {})) if old is not None and old.get("refs") == found else {}
        record["pins"] = ledger_mod._fill_pins(run, ctx, outcomes, carried)
        entries.append(_Claim(record, outcomes))
    entries.sort(key=lambda c: (order.get(c.record["file"], len(order)), c.record["line"], c.record["id"]))
    records = [c.record for c in entries]
    claim_lines = b"".join((json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
                           for r in records)
    return Assembly(run=run, tree_sha256=tex.source_tree_hash(manuscript), claims=entries,
                    problems=problems, records=records, claims_sha256=hashlib.sha256(claim_lines).hexdigest(),
                    order=order)


# ---- the check ---------------------------------------------------------------------------------------------

def _finding(rule: str, message: str, *, severity: str, claim=None, file=None, line=None, ref=None,
             code=None) -> dict:
    out = {"code": code if code is not None else CODE_OF.get(rule), "rule": rule, "severity": severity,
           "claim": claim, "file": file, "line": line}
    if ref is not None:
        out["ref"] = ref
    out["message"] = message
    return out


def _regular(run: Path, rel: str):
    try:
        return ev.regular_file(run, rel)
    except (ev.EvidenceError, OSError):
        return None


def _is_text(rel: str) -> bool:
    return rel.lower().endswith(TEXT_EXT)


def _values_of(run: Path, rel: str, cache: dict) -> list:
    if rel not in cache:
        cache[rel] = []
        path = _regular(run, rel)
        if path is not None and _is_text(rel):
            try:
                if path.stat().st_size <= MAX_TEXT_BYTES:
                    cache[rel] = evidence_values(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                cache[rel] = []
    return cache[rel]


def _number_findings(run: Path, claim: _Claim, cache: dict) -> list:
    rec = claim.record
    if not rec["numbers"]:
        return []
    data_run = [o for o in claim.outcomes if o.ref["type"] in ("data", "run")]
    if not data_run:
        return []                                  # DOI-only claims: numbers are not checked (M-G 5.5)
    usable = [o for o in data_run if not any(f["severity"] == "error" for f in o.findings)]
    if not usable:
        return []                                  # the reference errors already say why
    files = sorted({rel for o in usable for rel in o.files})
    texts = [rel for rel in files if _is_text(rel)]
    where = dict(claim=rec["id"], file=rec["file"], line=rec["line"])
    if not texts:
        return [_finding("number-uncheckable",
                         "the numbers of this claim have no text evidence to be checked against",
                         severity="warning", **where)]
    values = []
    for rel in texts:
        values += _values_of(run, rel, cache)
    doi_cited = any(o.ref["type"] == "doi" for o in claim.outcomes)
    out, seen = [], set()
    for num in rec["numbers"]:
        raw = num["raw"]
        if raw in seen:
            continue
        seen.add(raw)
        try:
            target = Decimal(num["value"])
        except InvalidOperation:
            continue
        if any(value_matches(target, num["decimals"], num["percent"], num["relation"], a) for a in values):
            continue
        if doi_cited:
            out.append(_finding("number-unmatched-cited",
                                f"the number {raw} matches no data or run evidence (its DOI may hold it)",
                                severity="warning", **where))
        else:
            out.append(_finding("number-missing",
                                f"the number {raw} is not in the evidence it cites ({', '.join(texts[:3])})",
                                severity="error", **where))
    return out


def _orphan(claim: _Claim) -> list:
    rec = claim.record
    if not rec["numbers"] or rec["refs"]:
        return []
    strict = rec["unit"] in ("abstract", "table", "figure") or "results" in rec["section_kinds"] \
        or "abstract" in rec["section_kinds"]
    return [_finding("orphan", f"{len(rec['numbers'])} checkable number(s) have no data, run or DOI reference",
                     severity="error" if strict else "warning",
                     claim=rec["id"], file=rec["file"], line=rec["line"])]


def _pin_findings(run: Path, claim: _Claim) -> list:
    rec = claim.record
    out = []
    for key, pin in sorted((rec.get("pins") or {}).items()):
        files = (pin or {}).get("files", {}) if isinstance(pin, dict) else {}
        changed = False
        for rel, digest in sorted(files.items()):
            path = _regular(run, rel)
            if path is None or ev.sha256_file(path) != digest:
                changed = True
                break
        if changed:
            out.append(_finding("evidence-changed",
                                f"the evidence of {key} differs from the pins of this claim; rebuild the "
                                "ledger (claims build) to repin after a checked change",
                                severity="error", claim=rec["id"], file=rec["file"], line=rec["line"], ref=key))
    return out


def _blocker_entries(project, slug: str):
    """(open blockers of the run, status unreadable?). A read that fails is unreadable, never clear."""
    try:
        return list(review_service.blockers(project, slug)), False
    except (ValueError, OSError, RecursionError, ImportError):
        return [], True


def _claim_blocks(rec: dict, entries: list) -> list:
    """(label, reason) of each blocker reference this claim cites: by DOI, or by cite key or its token."""
    doi_ids = {r["id"] for r in rec["refs"] if r["type"] == "doi"}
    cites = list(rec.get("cites") or [])
    hits = []
    for entry in entries:
        reason = str(entry.get("reason", "blocked"))
        for ref in entry.get("references") or []:
            if not isinstance(ref, dict):
                continue
            label = None
            bdoi = safe_doi(ref.get("doi")) if ref.get("doi") else None
            if bdoi is not None and bdoi in doi_ids:
                label = bdoi
            bkey = ref.get("key")
            if label is None and isinstance(bkey, str):
                for key in cites:
                    if bkey == key or bkey == key_token(key):
                        label = key_token(key)
                        break
            if label is not None and (label, reason) not in hits:
                hits.append((label, reason))
    return hits


def _corrected_dois(run: Path):
    """(safe DOIs flagged 'corrected' in the latest evidence bundle, status unreadable?)."""
    base = run / "review"
    rounds = []
    if base.is_dir() and not base.is_symlink():
        for entry in base.iterdir():
            if entry.name.startswith("round-") and entry.name[6:].isdigit():
                rounds.append(int(entry.name[6:]))
    for n in sorted(rounds, reverse=True):
        path = _regular(run, f"review/round-{n}/evidence/bundle.json")
        if path is None:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            return set(), True
        flags = doc.get("flags") if isinstance(doc, dict) else None
        if not isinstance(flags, list):
            return set(), True
        return {safe_doi(f.get("doi")) for f in flags
                if isinstance(f, dict) and f.get("kind") == "corrected" and safe_doi(f.get("doi"))}, False
    return set(), False


def _has_evidence_round(run: Path) -> bool:
    base = run / "review"
    if not base.is_dir():
        return False
    for entry in base.iterdir():
        if entry.name.startswith("round-") and (_regular(run, f"review/{entry.name}/matrix.json")
                                               or _regular(run, f"review/{entry.name}/evidence/bundle.json")):
            return True
    return False


def _ancestor(repo: Path, sha, cache: dict) -> str:
    """'ok' when sha is an ancestor of HEAD, 'drift' when not (or the repository is not git), 'unknown'."""
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        return "unknown"
    key = (str(repo), sha)
    if key not in cache:
        git = shutil.which("git")
        verdict = "unknown"
        if git is not None:
            env = {"PATH": os.environ.get("PATH", ""), "LANG": "C", "LC_ALL": "C",
                   "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
            try:
                proc = subprocess.run([git, "-C", str(repo), "merge-base", "--is-ancestor", sha, "HEAD"],
                                      env=env, capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
                verdict = "ok" if proc.returncode == 0 else ("drift" if proc.returncode in (1, 128) else "unknown")
            except (OSError, subprocess.SubprocessError):
                verdict = "unknown"
        cache[key] = verdict
    return cache[key]


def _drift_findings(run: Path, repo: Path, claim: _Claim, cache: dict) -> list:
    rec = claim.record
    where = dict(claim=rec["id"], file=rec["file"], line=rec["line"])
    out = []
    for outcome in claim.outcomes:
        if outcome.ref["type"] != "run":
            continue
        for rel in outcome.files:
            if not (rel.endswith("/manifest.json") and "/runs/" in rel):
                continue
            label = rel[:-len("/manifest.json")]
            path = _regular(run, rel)
            try:
                doc = json.loads(path.read_text(encoding="utf-8")) if path is not None else None
            except (OSError, UnicodeDecodeError, ValueError, RecursionError):
                doc = None
            code = doc.get("code") if isinstance(doc, dict) else None
            if not isinstance(code, dict):
                out.append(_finding("code-state-unknown", f"the manifest of {label} has no code state",
                                    severity="warning", **where))
                continue
            for scope in ("scieflow", "pipeline"):
                block = code.get(scope) if isinstance(code.get(scope), dict) else {}
                state = block.get("git")
                if not isinstance(state, dict):
                    out.append(_finding("code-state-unknown", f"{label}: the {scope} git state is not recorded",
                                        severity="warning", **where))
                    continue
                if state.get("dirty") is True:
                    out.append(_finding("code-dirty", f"{label}: the {scope} code was dirty when the run was made",
                                        severity="error", **where))
                    continue
                verdict = _ancestor(repo, state.get("sha"), cache)
                if verdict == "drift":
                    out.append(_finding("code-not-ancestor",
                                        f"{label}: the {scope} commit it was made from is not in this history",
                                        severity="error", **where))
                elif verdict == "unknown":
                    out.append(_finding("code-state-unknown",
                                        f"{label}: the {scope} commit cannot be checked (git unavailable or "
                                        "no valid sha)", severity="warning", **where))
    return out


def _problem_findings(asm: Assembly) -> list:
    out = []
    for p in asm.problems:
        out.append(_finding(p["rule"], p["message"], severity=p["severity"], claim=p["claim"],
                            file=p["file"], line=p["line"], ref=p.get("ref")))
    return out


def _apply_waivers(run: Path, findings: list, records: dict) -> tuple[list, list]:
    """Move findings a human waived (note.claims_waived, actor human, same text hash) to `waived`."""
    valid = set()
    for event in events.read(run, types=("note.claims_waived",)):
        if event.get("actor") != "human":
            continue
        data = event.get("data") or {}
        valid.add((data.get("claim"), data.get("code"), data.get("text_sha256")))
    kept, waived = [], []
    for item in findings:
        claim = item["claim"]
        rec = records.get(claim) if claim is not None else None
        code = item["code"]
        if code in WAIVABLE and rec is not None and (claim, code, rec["text_sha256"]) in valid:
            waived.append({"claim": claim, "code": code, "rule": item["rule"]})
        else:
            kept.append(item)
    return kept, waived


def _sort_key(order: dict, item: dict):
    return (order.get(item["file"], len(order)) if item["file"] else len(order), item["line"] or 0,
            item["code"] or "", item["rule"])


@dataclass
class CheckResult:
    findings: list
    waived: list
    claims: list
    manuscript_sha256: str
    ledger_sha256: str
    status_unreadable: bool = False

    @property
    def errors(self) -> list:
        return [f for f in self.findings if f["severity"] == "error"]

    @property
    def warnings(self) -> list:
        return [f for f in self.findings if f["severity"] == "warning"]

    def counts(self) -> dict:
        by_code: dict = {}
        for item in self.findings:
            key = item["code"] or "uncoded"
            by_code[key] = by_code.get(key, 0) + 1
        return {"by_code": dict(sorted(by_code.items())), "errors": len(self.errors),
                "warnings": len(self.warnings), "waived": len(self.waived)}

    def document(self) -> dict:
        return {"version": VERSION, "manuscript_tree_sha256": self.manuscript_sha256,
                "ledger_sha256": self.ledger_sha256, "counts": self.counts(),
                "findings": self.findings, "waived": self.waived}


def _evaluate(project, asm: Assembly) -> CheckResult:
    run = asm.run
    repo = Path(project.root)
    findings = _problem_findings(asm)
    entries, blockers_unreadable = _blocker_entries(project, run.name)
    corrected, bundle_unreadable = _corrected_dois(run)
    status_unreadable = blockers_unreadable or bundle_unreadable
    cache_values: dict = {}
    cache_git: dict = {}
    doi_claims = False
    for claim in asm.claims:
        rec = claim.record
        where = dict(claim=rec["id"], file=rec["file"], line=rec["line"])
        findings += _orphan(claim)
        findings += _pin_findings(run, claim)
        findings += _number_findings(run, claim, cache_values)
        if not blockers_unreadable:
            for label, reason in _claim_blocks(rec, entries):
                findings.append(_finding("blocked-reference",
                                         f"the claim cites a blocked reference ({reason}): {label}; fix the "
                                         "reference, or the user overrules the review-blocker gate",
                                         severity="error", ref=label, **where))
        findings += _drift_findings(run, repo, claim, cache_git)
        for ref in rec["refs"]:
            if ref["type"] != "doi":
                continue
            doi_claims = True
            if ref["id"] in corrected:
                findings.append(_finding("source-corrected",
                                         "a cited DOI carries a correction or editorial notice in the latest "
                                         "evidence", severity="warning", ref=f"doi:{ref['id']}", **where))
    if doi_claims and not _has_evidence_round(run):
        findings.append(_finding("doi-status-unknown",
                                 "no evidence round holds the status of the cited DOIs",
                                 severity="warning"))
    if status_unreadable:
        findings.append(_finding("status-unreadable", "the review status could not be read",
                                 severity="warning"))
    findings.sort(key=lambda item: _sort_key(asm.order, item))
    records = {c.record["id"]: c.record for c in asm.claims}
    kept, waived = _apply_waivers(run, findings, records)
    return CheckResult(findings=kept, waived=waived, claims=asm.records,
                       manuscript_sha256=asm.tree_sha256, ledger_sha256=asm.claims_sha256,
                       status_unreadable=status_unreadable)


def check_run(project, run_folder, main: str = "main.tex") -> CheckResult:
    """The check of the run's manuscript as it is now, the ledger built in memory (M-G 5.8). Writes nothing."""
    asm = assemble(run_folder, main=main)
    return _evaluate(project, asm)


def check_ledger(project, run_folder, main: str = "main.tex") -> CheckResult:
    """`claims check`: refuses a missing ledger (`ledger-missing`) or one built from another manuscript
    (`ledger-stale`); otherwise the check of `check_run`."""
    run = Path(run_folder)
    ledger_mod.read(run)                            # ledger-missing, and the symbolic link refusals
    path = ledger_mod.ledger_folder(run) / "ledger.json"
    if path.is_symlink():
        raise ledger_mod.ClaimsError("claims-path", "report/claims/ledger.json is a symbolic link")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise ledger_mod.ClaimsError("ledger-unreadable", "ledger.json cannot be read") from None
    try:
        current = tex.source_tree_hash(run / "manuscript")
    except tex.TexError as exc:
        raise ledger_mod.ClaimsError(exc.rule, exc.message) from exc
    if not isinstance(doc, dict) or doc.get("manuscript_tree_sha256") != current:
        raise ledger_mod.ClaimsError("ledger-stale", "the manuscript changed after the last build: run "
                                                     "`scieflow manuscript claims build <run>` first")
    return check_run(project, run, main=main)
