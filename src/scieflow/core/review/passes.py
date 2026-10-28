"""The two fresh review passes (spec §5; plan T2.1/T2.2).

- Pass A (`<reviewer>-on-<author>.blind.json`): the adversarial persona, the draft
  fenced as data, nothing from the evidence bundle and no literature services.
- Freezing: before the round's bundle is built (`evidence.build`, or
  `scieflow review freeze`), the sha256 of every valid pass-A file is recorded in
  `assignment.json` (`pairs[].blind_sha256`). A pass-A file that changes after
  that is tampering (Review Focus #6): pass B is refused for that pair and the
  coordinator is told (`note.review_pass_a_tampered`).
- Pass B (`…evidence.json`): the frozen pass-A JSON verbatim, the draft, the bundle
  in a fenced data block (rule 8) and the rendered `instruction`s of the review
  prompts that apply to the draft's sections.
- Validation (`validate_pass`): schema, identity fields, the frozen hash, every
  pass-A comment accounted for, and every evidence quote verbatim from a bundle
  string that belongs to its DOI (rule 9).

Every review is a fresh dispatch (`--role research.draft-reviewers`); this module
only writes prompt text and checks files, it never dispatches or calls out.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from string import Template

from scieflow.core import events
from scieflow.core.drafts import DraftError, check_name
from scieflow.core.review import assign as assign_mod
from scieflow.core.review import claims as claims_mod
from scieflow.core.review import decisions as decisions_mod
from scieflow.core.review import prompts as prompts_mod

PASSES = ("blind", "evidence")
ROLE = "research.draft-reviewers"
TEMPLATES = Path(__file__).resolve().parents[2] / "research" / "templates"
EVIDENCE_DIR = "evidence"
BUNDLE = "bundle.json"
TAMPER_EVENT = "note.review_pass_a_tampered"
_QUOTABLE = ("quote", "title", "detail")          # bundle strings a reviewer may quote
_MIN_QUOTE = 20                                    # shorter only when it is the whole string
_CONTEXT_FILES = (("inputs/manifest.yml", "MANIFEST (the evidence the draft must trace to)"),
                  ("journal/profile.md", "JOURNAL PROFILE"))
_CITES_DOI = re.compile(r"\b10\.\d{4,9}/\S+")
_CITES_ID = re.compile(r"\b[FC][0-9]+\b")
_CITES_WORD = re.compile(
    r"\b(?:bundle|scite|crossref|europe ?pmc|citation statements?|retract\w*|withdr[ae]wn?|"
    r"contradict\w*|refut\w*|replicat\w*|et al\b|contrasting (?:citations?|statements?)|"
    r"(?:earlier|previous|prior|later|newer|recent|published|other) (?:work|stud(?:y|ies)|"
    r"papers?|reports?|trials?|literature))", re.I)
_CITES_YEAR = re.compile(r"\(\s*(?:19|20)\d{2}[a-z]?\s*\)|\b[A-Z][a-z]+,? (?:19|20)\d{2}\b")


class PassError(ValueError):
    """A refused pass prompt or an invalid review file."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or [message]


class TamperError(PassError):
    """A frozen pass-A file is missing or changed after its hash was recorded."""


# --- small helpers ------------------------------------------------------------------


def _round(round_) -> int:
    if isinstance(round_, bool) or not isinstance(round_, int) or round_ < 1:
        raise PassError(f"round {round_!r} is not an integer >= 1")
    return round_


def _name(value, what: str) -> str:
    if not isinstance(value, str):
        raise PassError(f"{what} {value!r} is not an agent name")
    try:
        check_name(value)
    except DraftError as exc:
        raise PassError(f"{what} {value!r}: {exc}") from exc
    if "\\" in value or any(c.isspace() for c in value):
        raise PassError(f"{what} {value!r}: no backslashes or spaces in agent names")
    return value


def _pass(value) -> str:
    if value not in PASSES:
        raise PassError(f"pass {value!r} is not one of {', '.join(PASSES)}")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def round_dir(ws: Path, round_: int) -> Path:
    return Path(ws) / "review" / f"round-{round_}"


def file_name(reviewer: str, author: str, pass_: str) -> str:
    return f"{reviewer}-on-{author}.{pass_}.json"


def pass_path(ws: Path, round_: int, reviewer: str, author: str, pass_: str) -> Path:
    return round_dir(ws, round_) / file_name(reviewer, author, pass_)


def _confined(ws: Path, path: Path) -> Path:
    """`path`, refusing one that resolves outside the run (a symlink out)."""
    try:
        real = path.resolve()
        inside = real.is_relative_to(Path(ws).resolve())
    except (OSError, RuntimeError) as exc:
        raise PassError(f"cannot resolve {path.name}: {exc}") from exc
    if not inside:
        raise PassError(f"{path.name} resolves outside the run")
    return path


def _read(ws: Path, path: Path) -> bytes | None:
    """Bytes of a regular file inside the run, or None when it does not exist."""
    _confined(ws, path)
    try:
        if not path.is_file():
            return None
        return path.read_bytes()
    except OSError as exc:
        raise PassError(f"{path.name}: {exc}") from exc


def _label(text: str) -> str:
    return re.sub(r"[\[\]\r\n]+", " ", text).strip()


def _fenced(label: str, content: str, untrusted: bool = False) -> str:
    return prompts_mod._fenced(_label(label), content, untrusted=untrusted)


# --- assignment ---------------------------------------------------------------------------


def assignment(ws: Path, round_: int) -> dict:
    """review/round-N/assignment.json, schema-checked; PassError when absent."""
    n = _round(round_)
    raw = _read(ws, assign_mod.path(ws, n))
    if raw is None:
        raise PassError(f"no assignment for round {n} (run `scieflow review assign`)")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise PassError(f"round {n} assignment.json is not valid JSON: {exc}") from exc
    problem = decisions_mod.schema_error("review-assignment", data)
    if problem:
        raise PassError(f"round {n} assignment.json does not match the review-assignment "
                        f"schema: {problem}")
    return data


def _pair(data: dict, n: int, reviewer: str, author: str) -> dict:
    for pair in data["pairs"]:
        if pair["reviewer"] == reviewer and pair["author"] == author:
            return pair
    raise PassError(f"{reviewer} on {author} is not assigned in round {n}")


def _frozen_blind(ws: Path, n: int, pair: dict) -> bytes:
    """The pass-A bytes whose hash was frozen; PassError when never frozen,
    TamperError when missing or changed."""
    rv, au = pair["reviewer"], pair["author"]
    digest = pair.get("blind_sha256")
    if digest is None:
        raise PassError(f"pass A of {rv} on {au} is not frozen in round {n} "
                        "(it is frozen when the evidence bundle is built, or by "
                        "`scieflow review freeze`)")
    raw = _read(ws, pass_path(ws, n, rv, au, "blind"))
    if raw is None:
        raise TamperError(f"pass A of {rv} on {au} is missing after it was frozen; "
                          "pass B is refused for this pair")
    actual = sha256(raw)
    if actual != digest:
        raise TamperError(f"pass A of {rv} on {au} changed after it was frozen "
                          f"(sha256 {digest[:12]}… recorded, {actual[:12]}… now); "
                          "pass B is refused for this pair")
    return raw


# --- the evidence bundle ----------------------------------------------------------------


def load_bundle(ws: Path, round_: int) -> tuple[bytes, dict] | None:
    """(bytes, parsed) of review/round-N/evidence/bundle.json, or None when absent."""
    n = _round(round_)
    raw = _read(ws, round_dir(ws, n) / EVIDENCE_DIR / BUNDLE)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise PassError(f"round {n} evidence bundle is not valid JSON: {exc}") from exc
    problem = decisions_mod.schema_error("evidence-bundle", data)
    if problem:
        raise PassError(f"round {n} evidence bundle does not match the evidence-bundle "
                        f"schema: {problem}")
    return raw, data


def bundle_built(ws: Path, round_: int) -> bool:
    """True once round N's bundle exists or was ever built (a deleted bundle does not
    reopen freezing)."""
    if load_bundle(ws, round_) is not None:
        return True
    return any(isinstance(e.get("data"), dict) and e["data"].get("round") == round_
               for e in events.read(Path(ws), types=("review.evidence_built",)))


def tampered(ws: Path, round_: int, reviewer: str, author: str) -> None:
    """Tell the coordinator (run timeline) that a frozen pass A changed."""
    events.emit(Path(ws), TAMPER_EVENT, round=round_, reviewer=reviewer, author=author)


def _norm(text: str) -> str:
    return " ".join(str(text).split())


def bundle_index(bundle: dict) -> dict[str, list[str]]:
    """DOI (lower-case) -> the quotable strings (quote, title, detail) that belong to it.

    A string belongs to the DOI of its own object, or — for an object without a
    `doi` (a contested statement) — to its parent's DOI and its `citing_doi`.
    Every DOI named anywhere in the bundle is a key, even without strings.
    """
    index: dict[str, list[str]] = {}

    def add(doi, text=None):
        if isinstance(doi, str):
            bucket = index.setdefault(doi.lower(), [])
            if text is not None:
                bucket.append(_norm(text))

    def walk(node, inherited: tuple[str, ...]):
        if isinstance(node, list):
            for item in node:
                walk(item, inherited)
            return
        if not isinstance(node, dict):
            return
        own = (node["doi"],) if isinstance(node.get("doi"), str) else inherited
        if isinstance(node.get("citing_doi"), str):
            own = (*own, node["citing_doi"])
        for doi in own:
            add(doi)
        for doi in node.get("dois") or ():
            add(doi)
        for key, value in node.items():
            if key in _QUOTABLE and isinstance(value, str):
                for doi in own:
                    add(doi, value)
            elif isinstance(value, (dict, list)):
                walk(value, own)

    walk(bundle, ())
    return index


def quote_problem(index: dict[str, list[str]], doi: str, quote: str) -> str | None:
    """Why `quote` is not verbatim bundle evidence for `doi` (None when it is)."""
    strings = index.get(str(doi).lower())
    if strings is None:
        return f"{doi} is not in the evidence bundle"
    q = _norm(quote)
    if q and any(q in s and (len(q) >= _MIN_QUOTE or q == s) for s in strings):
        return None
    shown = q if len(q) <= 60 else q[:57] + "..."
    return f"quote {shown!r} is not a verbatim quote from the evidence bundle for {doi}"


# --- drafts and prompt text ----------------------------------------------------------------


def draft_dir(ws: Path, author: str) -> str:
    """The run-relative directory of the text `author` is reviewed on (with a
    trailing slash): `manuscript/drafts/<author>/`, or `manuscript/` in a
    paper-review run, where the reviewed author is the submitter."""
    if claims_mod.reviews_manuscript(ws):
        return claims_mod.MANUSCRIPT_DIR.as_posix() + "/"
    return (claims_mod.DRAFTS_DIR / author).as_posix() + "/"


def role(ws: Path) -> str:
    """The `--role` a review of this run is dispatched with."""
    return "research.reviewer" if claims_mod.reviews_manuscript(ws) else ROLE


def draft_files(ws: Path, author: str) -> tuple[Path, list[Path]]:
    """(draft root, its .tex files: main.tex and its confined inputs, else every top-level .tex)."""
    root = Path(ws) / draft_dir(ws, author)
    if root.is_symlink() or not root.is_dir():
        if claims_mod.reviews_manuscript(ws):
            raise PassError("no manuscript under manuscript/ (paper-review run)")
        raise PassError(f"no draft by {author} under manuscript/drafts/")
    _confined(ws, root)
    main = root / "main.tex"
    entries = [main] if main.is_file() else sorted(root.glob("*.tex"))
    files: list[Path] = []
    for entry in entries:
        for path in claims_mod._confined_tex(entry, root):
            if path not in files:
                files.append(path)
    if not files:
        raise PassError(f"the draft by {author} ({draft_dir(ws, author)}) has no .tex files")
    return root, files


def _draft_blocks(ws: Path, author: str, root: Path, files: list[Path]) -> str:
    out = []
    for path in files:
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        text = path.read_text(encoding="utf-8", errors="replace")
        out.append(_fenced(f"THE DRAFT by {author}: {rel}", text))
    return "".join(out)


def _context_blocks(ws: Path) -> str:
    out = []
    for rel, label in _CONTEXT_FILES:
        try:
            raw = _read(ws, Path(ws) / rel)
        except PassError:
            continue
        if raw is not None:
            out.append(_fenced(f"{label}: {rel}", raw.decode("utf-8", errors="replace")))
    return "".join(out)


def _persona() -> str:
    text = (TEMPLATES / "adversarial-review.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _template(name: str, **values) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    return Template(text).substitute({k: str(v) for k, v in values.items()})


def _sections(files: list[Path]) -> dict[str, str]:
    """Section name -> text of the draft (local only, fenced when used)."""
    nb = claims_mod.parser()
    lines: dict[str, list[str]] = {}
    for path in files:
        body = nb.strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        default = claims_mod.section_name(path.stem) if path.stem != "main" else "body"
        for name, line in zip(claims_mod._sections_by_line(body, default),
                              body.split("\n"), strict=False):
            lines.setdefault(name, []).append(line)
    return {name: "\n".join(text).strip() for name, text in lines.items()}


def _instructions(ws: Path, root: Path, files: list[Path], bundle: dict) -> str:
    from scieflow.core.review import evidence as evidence_mod

    ps = prompts_mod.load_for_run(ws)
    man = Path(ws) / claims_mod.MANUSCRIPT_DIR
    ex = claims_mod.extract([(p, root) for p in files], man / claims_mod.BIB_NAME, man)
    sections = _sections(files)
    years = [r["year"] for r in bundle.get("references") or [] if isinstance(r.get("year"), int)]
    ctx = prompts_mod.Context.from_extraction(
        ex, run_dir=Path(ws), topic=evidence_mod.topic(ws),
        seed_dois=[c["doi"] for c in bundle.get("contested") or []],
        latest_cited_year=max(years) if years else None, sections=sections)
    chosen = ps.select(stage="review", sections=sorted(sections))
    if not chosen:
        return "(no review prompt applies to this draft)"
    return "\n\n".join(f"### {p.id}\n{prompts_mod.render_instruction(p, ctx, ps).strip()}"
                       for p in chosen)


def blind_prompt(ws: Path, round_: int, reviewer: str, author: str, *,
                 slug: str | None = None) -> str:
    """Pass-A prompt for an assigned pair: persona + draft, nothing from the bundle."""
    ws = Path(ws)
    n = _round(round_)
    _name(reviewer, "reviewer")
    _name(author, "author")
    _pair(assignment(ws, n), n, reviewer, author)
    root, files = draft_files(ws, author)
    return _template("review-pass-blind.md", slug=slug or ws.name, round=n, reviewer=reviewer,
                     author=author, draft_dir=draft_dir(ws, author), persona=_persona(),
                     context=_context_blocks(ws),
                     draft=_draft_blocks(ws, author, root, files))


def evidence_prompt(ws: Path, round_: int, reviewer: str, author: str, *,
                    slug: str | None = None) -> str:
    """Pass-B prompt: frozen pass A verbatim, the draft, the fenced bundle and the
    rendered review instructions. Refused (TamperError) when pass A changed after
    it was frozen; the coordinator is told through `note.review_pass_a_tampered`."""
    ws = Path(ws)
    n = _round(round_)
    _name(reviewer, "reviewer")
    _name(author, "author")
    pair = _pair(assignment(ws, n), n, reviewer, author)
    try:
        blind = _frozen_blind(ws, n, pair)
    except TamperError:
        tampered(ws, n, reviewer, author)
        raise
    loaded = load_bundle(ws, n)
    if loaded is None:
        raise PassError(f"no evidence bundle for round {n} (scite is off or "
                        "`scieflow review evidence` has not run): no pass B")
    bundle_raw, bundle = loaded
    root, files = draft_files(ws, author)
    digest = sha256(blind)
    return _template(
        "review-pass-evidence.md", slug=slug or ws.name, round=n, reviewer=reviewer,
        author=author, draft_dir=draft_dir(ws, author), blind_sha256=digest,
        persona=_persona(), context=_context_blocks(ws),
        draft=_draft_blocks(ws, author, root, files),
        pass_a=_fenced(f"YOUR FROZEN pass A REVIEW (sha256 {digest})",
                       blind.decode("utf-8", errors="replace")),
        bundle=_fenced(f"EVIDENCE BUNDLE round {n} (review/round-{n}/evidence/bundle.json)",
                       bundle_raw.decode("utf-8", errors="replace"), untrusted=True),
        instructions=_instructions(ws, root, files, bundle),
        scite_status=bundle["scite_status"])


# --- validation -----------------------------------------------------------------------------


def _comments(data: dict) -> list[dict]:
    return list(data.get("major") or []) + list(data.get("minor") or [])


def _text_of(comment: dict) -> str:
    return " ".join(str(comment.get(k, "")) for k in
                    ("location", "problem", "why_it_matters", "resolution", "comment"))


def _cites_evidence(comment: dict) -> bool:
    text = _text_of(comment)
    return bool(_CITES_DOI.search(text) or _CITES_ID.search(text) or _CITES_WORD.search(text)
                or _CITES_YEAR.search(text))


def _check_review(data, n: int, reviewer: str, author: str, pass_: str, name: str) -> dict:
    """Identity fields, schema and unique ids of one parsed review file."""
    if not isinstance(data, dict):
        raise PassError(f"{name} is not a JSON object")
    problems = []
    if data.get("agent") != reviewer:
        problems.append(f"agent is {data.get('agent')!r}, the file is by {reviewer!r}")
    if data.get("reviewed") != author:
        problems.append(f"reviewed is {data.get('reviewed')!r}, the file reviews {author!r}")
    if "round" in data and data["round"] != n:
        problems.append(f"round is {data['round']!r}, the file is in round {n}")
    elif "round" not in data and "pass" in data:
        problems.append(f"round is missing (a two-pass review names its round, {n})")
    if data.get("pass", "blind") != pass_:
        problems.append(f"pass is {data.get('pass', 'blind')!r}, the file is the {pass_} pass")
    if problems:
        raise PassError(f"{name}: " + "; ".join(problems), problems)
    problem = decisions_mod.schema_error("manuscript-review", data)
    if problem:
        raise PassError(f"{name} does not match the manuscript-review schema: {problem}")
    ids = [c["id"] for c in _comments(data)]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise PassError(f"{name}: comment id {', '.join(dupes)} is used more than once")
    return data


def _check_evidence_pass(data: dict, blind: dict, bundle: dict, name: str) -> None:
    problems: list[str] = []
    if data.get("scite_status") != bundle["scite_status"]:
        problems.append(f"scite_status is {data.get('scite_status')!r}, the evidence bundle "
                        f"says {bundle['scite_status']!r}")
    before = [c["id"] for c in _comments(blind)]
    after = {c["id"]: c for c in _comments(data)}
    for cid in before:
        if cid not in after:
            problems.append(f"{cid} of pass A is missing (keep it, marked unchanged, "
                            "weakened or withdrawn)")
    index = bundle_index(bundle)
    for cid, c in after.items():
        change = c["changed_from_blind"]
        if change == "new" and cid in before:
            problems.append(f"{cid} is marked new but pass A already has {cid}")
        elif change != "new" and cid not in before:
            problems.append(f"{cid} is marked {change} but pass A has no {cid}")
        evidence = c.get("evidence") or []
        for item in evidence:
            why = quote_problem(index, item["doi"], item["quote"])
            if why:
                problems.append(f"{cid}: {why}")
        if not evidence and change == "strengthened":
            problems.append(f"{cid} is strengthened, so it cites evidence, but has no verbatim "
                            "quote (evidence[] with doi and quote)")
        elif not evidence and change == "new" and _cites_evidence(c):
            problems.append(f"{cid} is new and cites evidence but has no verbatim quote "
                            "(evidence[] with doi and quote)")
    if problems:
        raise PassError(f"{name}: " + "; ".join(problems), problems)


def check_pass(ws: Path, round_: int, reviewer: str, author: str, pass_: str) -> tuple[dict, str]:
    """(review, sha256 of the validated bytes); PassError / TamperError when refused."""
    ws = Path(ws)
    n = _round(round_)
    _name(reviewer, "reviewer")
    _name(author, "author")
    _pass(pass_)
    pair = _pair(assignment(ws, n), n, reviewer, author)
    name = f"round {n} {file_name(reviewer, author, pass_)}"
    if pass_ == "blind":
        raw = _read(ws, pass_path(ws, n, reviewer, author, "blind"))
        if raw is None:
            raise PassError(f"{name} does not exist")
        data = _check_review(_parse(raw, name), n, reviewer, author, "blind", name)
        if "blind_sha256" in pair:
            if _frozen_blind(ws, n, pair) != raw:
                raise TamperError(f"pass A of {reviewer} on {author} changed while it was "
                                  "being checked")
        elif bundle_built(ws, n):
            raise PassError(f"{name} was not frozen before the evidence bundle was built; "
                            "it is not a blind review")
        return data, sha256(raw)
    blind_raw = _frozen_blind(ws, n, pair)
    raw = _read(ws, pass_path(ws, n, reviewer, author, "evidence"))
    if raw is None:
        raise PassError(f"{name} does not exist")
    loaded = load_bundle(ws, n)
    if loaded is None:
        raise PassError(f"{name}: no evidence bundle for round {n}, so no pass B")
    data = _check_review(_parse(raw, name), n, reviewer, author, "evidence", name)
    blind_name = f"round {n} {file_name(reviewer, author, 'blind')}"
    blind = _check_review(_parse(blind_raw, blind_name), n, reviewer, author, "blind", blind_name)
    _check_evidence_pass(data, blind, loaded[1], name)
    return data, sha256(raw)


def _parse(raw: bytes, name: str):
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise PassError(f"{name} is not valid JSON: {exc}") from exc


def validate_pass(ws: Path, round_: int, reviewer: str, author: str, pass_: str) -> dict:
    """The review file, validated (see module doc); PassError / TamperError otherwise."""
    return check_pass(ws, round_, reviewer, author, pass_)[0]


# --- freezing -------------------------------------------------------------------------------


def freeze(ws: Path, round_: int) -> dict:
    """Record the sha256 of every valid, not yet frozen pass-A file in assignment.json.

    Report keys (lists of (reviewer, author)): `frozen` now, `already` frozen and
    unchanged, `tampered` (frozen, then changed or deleted — the recorded hash is
    kept), `missing` (no pass-A file), `late` (written after the round's bundle:
    not a blind review, never frozen) and `invalid` ([(pair, reason)]).
    """
    ws = Path(ws)
    n = _round(round_)
    data = assignment(ws, n)
    has_bundle = bundle_built(ws, n)
    report: dict[str, list] = {k: [] for k in
                               ("frozen", "already", "tampered", "missing", "late", "invalid")}
    changed = False
    for pair in data["pairs"]:
        rv, au = pair["reviewer"], pair["author"]
        key = (rv, au)
        if "blind_sha256" in pair:
            try:
                _frozen_blind(ws, n, pair)
                report["already"].append(key)
            except TamperError:
                report["tampered"].append(key)
                tampered(ws, n, rv, au)
            continue
        raw = _read(ws, pass_path(ws, n, rv, au, "blind"))
        if raw is None:
            report["missing"].append(key)
            continue
        if has_bundle:
            report["late"].append(key)
            continue
        name = f"round {n} {file_name(rv, au, 'blind')}"
        try:
            _check_review(_parse(raw, name), n, rv, au, "blind", name)
        except PassError as exc:
            report["invalid"].append((key, str(exc)))
            continue
        pair["blind_sha256"] = sha256(raw)
        report["frozen"].append(key)
        changed = True
    if changed:
        assign_mod.write(ws, n, data)
    return report
