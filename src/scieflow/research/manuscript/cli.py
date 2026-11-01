"""`scieflow manuscript` (M-F plan 5.14, t7 subset): `check`, `checklist-prompt`, `render`, `build`. Exit
codes: 0 ok, 1 a check failed or the run is refused, 2 usage. Package and letter come with later tickets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import click

from scieflow.core.project import Project, ProjectError
from scieflow.research.journal import state
from scieflow.research.manuscript import build as build_mod
from scieflow.research.manuscript import check as check_mod
from scieflow.research.manuscript import checklist, judgement, letters as letters_mod
from scieflow.research.manuscript import diff as diff_mod, points as points_mod
from scieflow.research.revision import adopt as adopt_mod, coverage as coverage_mod, draft as draft_mod
from scieflow.research.revision import letter as letter_mod, render as revision_render, respond as respond_mod
from scieflow.research.manuscript import package as package_mod, render as render_mod, templates, tex
from scieflow.research.manuscript.claims import ledger as ledger_mod
from scieflow.research.manuscript.claims import compile as claims_compile
from scieflow.research.manuscript.claims import report as claims_report
from scieflow.research.manuscript.claims import waivers as claims_waivers
from scieflow.research.manuscript import crate as crate_mod

OK, REFUSED, USAGE = 0, 1, 2


def _fail(message: str, code: int = REFUSED) -> NoReturn:
    click.echo(message, err=True)
    raise SystemExit(code)


def _run_folder(project: Project, run: str) -> Path:
    try:
        folder = project.run_dir(run)
    except ProjectError as exc:
        _fail(f"error: {exc}")
    if not folder.is_dir():
        _fail(f"no run {run!r} (looked for {folder})")
    return folder


@click.group()
def manuscript():
    """Manuscripts: check against the journal, render, build and package."""


@manuscript.command()
@click.argument("run")
@click.option("--journal", default=None, help="The journal slug (default: metadata.yml journal).")
@click.option("--article-type", "article_type", default=None, help="The article type id.")
@click.option("--guideline", "guidelines", multiple=True,
              help="A reporting guideline id (recorded in the report; checked by a later step).")
@click.option("--main", "main_file", default=None, help="The main TeX file (default main.tex).")
@click.option("--json", "as_json", is_flag=True, help="Print the report document as JSON.")
@click.option("--no-write", "no_write", is_flag=True, help="Write nothing (no report, no note).")
def check(run, journal, article_type, guidelines, main_file, as_json, no_write):
    """Check the manuscript of RUN against its journal profile (M-F 5.9)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        report = check_mod.run_check(project.root, folder, journal=journal, article_type=article_type,
                                     main=main_file, guidelines=guidelines)
    except check_mod.CheckError as exc:
        _fail(str(exc))
    json_path = md_path = None
    if not no_write:
        json_path, md_path = check_mod.write_report(report, folder)
        check_mod.note_check(folder, report)
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        click.echo(f"manuscript check: {len(report.errors)} errors, {len(report.warnings)} warnings")
        for finding in report.findings:
            where = f" ({finding.where})" if finding.where else ""
            click.echo(f"  {finding.severity} {finding.rule}{where}: {finding.message}")
        if not no_write:
            click.echo(f"report: {json_path}")
            click.echo(f"report: {md_path}")
    raise SystemExit(report.exit_code)


@manuscript.command("checklist-prompt")
@click.argument("run")
@click.option("--guideline", "guideline", required=True, help="The reporting guideline id to judge.")
@click.option("--main", "main_file", default=None, help="The main TeX file (default main.tex).")
@click.option("--write", "write", is_flag=True, help="Write prompts/checklist-<id>.md (default: print it).")
def checklist_prompt(run, guideline, main_file, write):
    """Write the reviewer prompt for the checklist items that need judgement (M-F 5.10).

    Dispatch it with --role research.reviewer; the reviewer writes report/checklist/<id>.judgement.json.
    """
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        text = judgement.prompt_for(project.root, folder, guideline, main=main_file)
    except (tex.TexError, checklist.ChecklistError) as exc:
        _fail(str(exc))
    if write:
        try:
            path = judgement.write_prompt(folder, guideline, text)
        except state.StateError as exc:
            _fail(str(exc))
        click.echo(f"prompt: {path}")
    else:
        click.echo(text, nl=False)
    raise SystemExit(OK)


@manuscript.command()
@click.argument("run")
@click.option("--class", "cls", type=click.Choice(list(templates.CLASSES)), default=None,
              help="The paper class (default: the profile's latex_class, else article).")
@click.option("--article-type", "article_type", default=None, help="The article type id (default: metadata.yml).")
@click.option("--force", is_flag=True, help="Replace a hand-edited or unmarked main.tex (kept as main.tex.bak-<sha12>).")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def render(run, cls, article_type, force, as_json):
    """Write manuscript/main.tex from metadata, the profile and the section files (M-F 5.11)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = render_mod.render_run(project.root, folder, cls=cls, article_type=article_type, force=force)
    except render_mod.RenderError as exc:
        _fail(f"render refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        state_text = "written" if result.written else "unchanged"
        click.echo(f"render: {state_text} {result.path} (class {result.cls}, sha256 {result.sha256[:12]})")
        if result.backup is not None:
            click.echo(f"render: kept the previous file as {result.backup}")
    raise SystemExit(OK)


@manuscript.command()
@click.argument("run")
@click.option("--engine", default=None, type=click.Choice(["latexmk", "pdflatex", "xelatex", "lualatex", "tectonic"]),
              help="The engine (default: the first of latexmk, pdflatex, xelatex, lualatex found; tectonic only when named).")
@click.option("--timeout", type=click.IntRange(30, 1800), default=600, show_default=True,
              help="Seconds per engine call.")
@click.option("--json", "as_json", is_flag=True, help="Print the build record as JSON.")
def build(run, engine, timeout, as_json):
    """Compile manuscript/main.tex in a scratch copy with shell escape off (M-F 5.12)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = build_mod.build_run(project.root, folder, engine=engine, timeout=timeout)
    except build_mod.BuildError as exc:
        _fail(f"build refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        for note in result.notes:
            click.echo(f"note: {note}")
        if result.ok:
            click.echo(f"build: ok ({result.engine}), pdf sha256 {result.pdf_sha256[:12]}")
            click.echo(f"build: {folder / 'manuscript' / build_mod.BUILD_DIR / 'main.pdf'}")
    if not result.ok:
        click.echo("build failed; last lines of the log:", err=True)
        click.echo(result.log_tail, err=True)
        _fail(f"build failed with engine {result.engine}")
    raise SystemExit(OK)


@manuscript.command()
@click.argument("run")
@click.option("--json", "as_json", is_flag=True, help="Print the package record as JSON.")
def package(run, as_json):
    """Write report/submission/ for RUN once every gate holds (M-F 5.13). Uploads nothing."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = package_mod.package_run(project, folder)
    except package_mod.PackageError as exc:
        _fail(f"package refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        click.echo(f"package: {result.folder}")
        for name in sorted(result.files):
            click.echo(f"  {name}")
    raise SystemExit(OK)


@manuscript.command()
@click.argument("run")
@click.option("--kind", type=click.Choice(["cover", "response"]), required=True,
              help="The starter letter: cover or response.")
@click.option("--force", is_flag=True, help="Replace an existing letter.")
def letter(run, kind, force):
    """Write a starter cover letter or response to reviewers into manuscript/letters/ (M-F 5.13)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        path = letters_mod.write_letter(project.root, folder, kind, force=force)
    except letters_mod.LetterError as exc:
        _fail(f"letter refused [{exc.rule}]: {exc.message}")
    click.echo(f"letter: {path}")
    raise SystemExit(OK)


@manuscript.group()
def claims():
    """The claim ledger: one record per checkable sentence, figure or table (M-G 5.2-5.3)."""


@claims.command("build")
@click.argument("run")
@click.option("--main", "main_file", default=None, help="The main TeX file (default main.tex).")
@click.option("--json", "as_json", is_flag=True, help="Print the ledger document as JSON.")
@click.option("--repin", is_flag=True, help="Take every claim's pins from the current evidence.")
@click.option("--repin-claim", "repin_claims", multiple=True, metavar="ID",
              help="Take this claim's pins from the current evidence (repeatable).")
def claims_build(run, main_file, as_json, repin, repin_claims):
    """Write report/claims/claims.jsonl and ledger.json for RUN, pinning the evidence it cites."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = ledger_mod.build(folder, main=main_file or "main.tex", repin=repin,
                                  repin_claims=tuple(repin_claims))
    except ledger_mod.ClaimsError as exc:
        _fail(f"claims refused [{exc.rule}]: {exc.message}")
    if as_json:
        document = json.loads(result.ledger_path.read_text(encoding="utf-8"))
        click.echo(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        click.echo(f"claims: {result.count} claims written to {result.claims_path}")
        if result.problems:
            rules: dict = {}
            for item in result.problems:
                rules[item["rule"]] = rules.get(item["rule"], 0) + 1
            summary = ", ".join(f"{rule} {count}" for rule, count in sorted(rules.items()))
            click.echo(f"reference problems: {summary}")
    raise SystemExit(OK)


@claims.command("show")
@click.argument("run")
@click.option("--id", "claim_id", default=None, help="Show only the claim with this id.")
@click.option("--json", "as_json", is_flag=True, help="Print the records as a JSON list.")
def claims_show(run, claim_id, as_json):
    """Print the claim ledger of RUN (excerpts of at most 60 characters in the table view)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        records = ledger_mod.read(folder, claim_id=claim_id)
    except ledger_mod.ClaimsError as exc:
        _fail(f"claims refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        for record in records:
            click.echo(f"{record['id']}  {record['file']}:{record['line']}  {record['unit']}  "
                       f"{tex.excerpt(record['text'])}")
    raise SystemExit(OK)


@claims.command("check")
@click.argument("run")
@click.option("--main", "main_file", default=None, help="The main TeX file (default main.tex).")
@click.option("--json", "as_json", is_flag=True, help="Print the check document as JSON.")
@click.option("--no-write", "no_write", is_flag=True, help="Write nothing (no report, no event).")
def claims_check(run, main_file, as_json, no_write):
    """Check the claim ledger of RUN against its evidence and blockers (M-G 5.6); exit 1 with any error."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = claims_compile.check_ledger(project, folder, main=main_file or "main.tex")
    except ledger_mod.ClaimsError as exc:
        _fail(f"claims refused [{exc.rule}]: {exc.message}")
    document = result.document()
    json_path = md_path = None
    if not no_write:
        try:
            json_path, md_path = claims_report.write(folder, document)
        except ledger_mod.ClaimsError as exc:
            _fail(f"claims refused [{exc.rule}]: {exc.message}")
        claims_report.note_checked(folder, document)
    if as_json:
        click.echo(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        counts = document["counts"]
        click.echo(f"claims check: {counts['errors']} errors, {counts['warnings']} warnings, "
                   f"{counts['waived']} waived")
        for item in result.findings:
            code = item["code"] or "-"
            where = f" ({item['file']}:{item['line']})" if item["file"] else ""
            click.echo(f"  {item['severity']} {code} {item['rule']}{where}: {item['message']}")
        if not no_write:
            click.echo(f"report: {json_path}")
            click.echo(f"report: {md_path}")
    raise SystemExit(REFUSED if result.errors else OK)


@claims.command("waive")
@click.argument("run")
@click.argument("claim_id")
@click.option("--code", "code", required=True, help="The finding code to waive: C001, C002, C003 or C006.")
@click.option("--reason", "reason", required=True, help="Why the finding is accepted (10-500 characters).")
def claims_waive(run, claim_id, code, reason):
    """Waive one finding of a claim: a human decision, never inside a dispatch (M-G 5.7)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        claims_waivers.waive(folder, project, claim_id, code, reason)
    except (claims_waivers.WaiverError, ledger_mod.ClaimsError) as exc:
        _fail(f"waive refused [{exc.rule}]: {exc.message}")
    click.echo(f"waived {code} for claim {claim_id} (recorded as a human action)")
    raise SystemExit(OK)


@manuscript.command("crate")
@click.argument("run")
@click.option("--json", "as_json", is_flag=True, help="Print the RO-Crate document as JSON.")
@click.option("--no-write", "no_write", is_flag=True, help="Write nothing (no crate file, no note).")
def crate_command(run, as_json, no_write):
    """Write report/ro-crate/ro-crate-metadata.json (RO-Crate 1.1 metadata only) for RUN (M-G 5.14)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        document = crate_mod.build(project, folder)
        path = None
        if not no_write:
            path = crate_mod.write(folder, document)
            crate_mod.note_written(folder, document)
    except (crate_mod.CrateError, ledger_mod.ClaimsError) as exc:
        _fail(f"crate refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))
    elif path is not None:
        click.echo(f"crate: {len(document['@graph'])} entities written to {path}")
    else:
        click.echo(f"crate: {len(document['@graph'])} entities (nothing written: --no-write)")
    raise SystemExit(OK)


@manuscript.group()
def point():
    """Provenance points: a pinned copy of the source tree (M-G 5.9)."""


@point.command("add")
@click.argument("run")
@click.argument("label")
@click.option("--from-package", "from_package", is_flag=True,
              help="Take the source.zip of report/submission/ (refused inside a dispatch).")
def point_add(run, label, from_package):
    """Pin the manuscript of RUN as provenance point LABEL."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        record = points_mod.add(folder, label, from_package=from_package)
    except points_mod.PointError as exc:
        _fail(f"point refused [{exc.rule}]: {exc.message}")
    click.echo(f"point {label}: {record['origin']}, {len(record['files'])} files")
    raise SystemExit(OK)


@point.command("list")
@click.argument("run")
@click.option("--json", "as_json", is_flag=True, help="Print the point records as JSON.")
def point_list(run, as_json):
    """List the provenance points of RUN."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        records = points_mod.list_points(folder)
    except points_mod.PointError as exc:
        _fail(f"point refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        for record in records:
            click.echo(f"{record['label']}  {record['origin']}  {len(record['files'])} files")
    raise SystemExit(OK)


@manuscript.command("diff")
@click.argument("run")
@click.option("--from", "from_label", required=True, help="The provenance point to compare from.")
@click.option("--to", "to_label", default="current", show_default=True,
              help="A provenance point, or current (the manuscript as it is now).")
@click.option("--main", "main_file", default="main.tex", show_default=True, help="The main TeX file.")
@click.option("--json", "as_json", is_flag=True, help="Print the diff summary as JSON.")
def diff_command(run, from_label, to_label, main_file, as_json):
    """Write the changes between two provenance points into report/diff/ (M-G 5.9)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = diff_mod.diff(folder, from_label, to_label, main=main_file)
    except (diff_mod.DiffError, points_mod.PointError) as exc:
        _fail(f"diff refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        click.echo(f"diff: {result.folder}")
        click.echo(f"  added {len(result.added)}, removed {len(result.removed)}, changed {len(result.changed)}")
        for note in result.notes:
            click.echo(f"note: {note}")
    raise SystemExit(OK)


@manuscript.group()
def revision():
    """Decision letters: ingest a letter, prompt a sub-agent, adopt its comments (M-G 5.10-5.11)."""


@revision.command("ingest")
@click.argument("run")
@click.option("--letter", "letter_path", required=True, type=click.Path(dir_okay=False),
              help="The decision letter (.txt, .md, .html or .htm), a file inside the run folder.")
@click.option("--round", "round_", type=click.IntRange(min=1), default=None,
              help="The round number (default: one past the highest existing round).")
@click.option("--decision", type=click.Choice(letter_mod.DECISIONS), default=None,
              help="The decision; wins over the letter's own wording.")
def revision_ingest(run, letter_path, round_, decision):
    """Store the decision letter of RUN as a round, with its blocks and the point submitted-rN."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = letter_mod.ingest(folder, Path(letter_path), round_=round_, decision=decision)
    except (letter_mod.LetterError, points_mod.PointError) as exc:
        _fail(f"ingest refused [{exc.rule}]: {exc.message}")
    record = result["record"]
    point = result["point"]
    if point["note"]:
        click.echo(f"note: {point['note']}")
    click.echo(f"round {record['round']}: {len(record['blocks'])} blocks, {result['paragraphs']} paragraphs, "
               f"decision {record['decision']} ({record['decision_source']})")
    click.echo(f"point {point['label']}: {point['origin']}")
    raise SystemExit(OK)


@revision.command("prompt")
@click.argument("run")
@click.option("--round", "round_", type=click.IntRange(min=1), required=True, help="The round to structure.")
@click.option("--agent", required=True,
              help="The sub-agent that writes revision/round-N/draft/<agent>.json.")
@click.option("--write", "write", is_flag=True,
              help="Store the prompt in prompts/ and print the dispatch line instead of the prompt.")
def revision_prompt(run, round_, agent, write):
    """Build the structuring prompt for round N (letter fenced as untrusted data, M-G 5.11)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        text = draft_mod.build_prompt(folder, round_, agent)
        if not write:
            click.echo(text, nl=False)
            raise SystemExit(OK)
        path = draft_mod.store_prompt(folder, round_, agent, text)
    except letter_mod.LetterError as exc:
        _fail(f"prompt refused [{exc.rule}]: {exc.message}")
    except state.StateError as exc:
        _fail(f"prompt refused: {exc}")
    click.echo(f"prompt written: {path.relative_to(folder).as_posix()}")
    click.echo(draft_mod.dispatch_line(folder, round_, agent, path))
    raise SystemExit(OK)


@revision.command("adopt")
@click.argument("run")
@click.option("--round", "round_", type=click.IntRange(min=1), required=True, help="The round to adopt.")
@click.option("--draft", "draft_path", required=True, type=click.Path(dir_okay=False),
              help="The agent's draft (a file inside the run folder).")
@click.option("--replace", "replace", is_flag=True, help="Overwrite an existing adopted.json.")
def revision_adopt(run, round_, draft_path, replace):
    """Check the draft against the stored letter, then write one review per block (M-G 5.11)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        adopted = adopt_mod.adopt(folder, round_, Path(draft_path), replace=replace)
    except (letter_mod.LetterError, state.StateError) as exc:
        rule = getattr(exc, "rule", "write")
        _fail(f"adopt refused [{rule}]: {exc}")
    click.echo(f"adopted round {round_}: {len(adopted['comments'])} comments, "
               f"{len(adopted['not_comments'])} paragraphs without a comment")
    raise SystemExit(OK)


@revision.command("show")
@click.argument("run")
@click.option("--json", "as_json", is_flag=True, help="Print the round summaries as JSON.")
def revision_show(run, as_json):
    """Summarise the decision-letter rounds of RUN (no letter text)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        rounds = letter_mod.existing_rounds(folder)
    except letter_mod.LetterError as exc:
        _fail(f"show refused [{exc.rule}]: {exc.message}")
    rows = []
    for number in rounds:
        try:
            record = letter_mod.load(folder, number)
        except letter_mod.LetterError as exc:
            _fail(f"show refused [{exc.rule}]: {exc.message}")
        adopted = letter_mod.round_dir(folder, number) / "adopted.json"
        rows.append({
            "round": number,
            "decision": record["decision"],
            "decision_source": record["decision_source"],
            "blocks": [block["id"] for block in record["blocks"]],
            "letter_sha256": record["letter_sha256"],
            "adopted": adopted.is_file(),
        })
    if as_json:
        click.echo(json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        for row in rows:
            state_text = "adopted" if row["adopted"] else "not adopted"
            click.echo(f"round {row['round']}: decision {row['decision']} ({row['decision_source']}), "
                       f"blocks {' '.join(row['blocks'])}, {state_text}")
    raise SystemExit(OK)


@revision.command("respond")
@click.argument("run")
@click.option("--round", "round_", type=click.IntRange(min=1), required=True, help="The round to answer.")
@click.option("--force", "force", is_flag=True, help="Replace an existing response.md (typed text is lost).")
def revision_respond(run, round_, force):
    """Write the response starter of round N: one section per adopted comment (M-G 5.12)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        path = respond_mod.write(folder, round_, force=force)
    except (letter_mod.LetterError, state.StateError) as exc:
        rule = getattr(exc, "rule", "write")
        _fail(f"respond refused [{rule}]: {exc}")
    click.echo(f"response starter: {path.relative_to(folder).as_posix()}")
    raise SystemExit(OK)


@revision.command("coverage")
@click.argument("run")
@click.option("--round", "round_", type=click.IntRange(min=1), required=True, help="The round to check.")
@click.option("--json", "as_json", is_flag=True, help="Print the coverage document as JSON.")
def revision_coverage(run, round_, as_json):
    """Check that every adopted comment is answered with a change or a reason; write coverage.json (M-G 5.12)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        document = coverage_mod.write(folder, round_)
    except (letter_mod.LetterError, state.StateError) as exc:
        rule = getattr(exc, "rule", "write")
        _fail(f"coverage refused [{rule}]: {exc}")
    if as_json:
        click.echo(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        verdict = "ok" if document["ok"] else "not ok"
        click.echo(f"round {round_}: {len(document['comments'])} comments, coverage {verdict}")
        for row in document["problems"]:
            click.echo(f"  {row['comment']}: {row['level']} {row['rule']}")
    raise SystemExit(OK if document["ok"] else REFUSED)


@revision.command("letter")
@click.argument("run")
@click.option("--round", "round_", type=click.IntRange(min=1), required=True, help="The round to write.")
@click.option("--force", "force", is_flag=True, help="Replace an existing response letter.")
def revision_letter(run, round_, force):
    """Write the response to reviewers of round N into manuscript/letters/ (M-G 5.12)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        path = revision_render.write(project.root, folder, round_, force=force)
    except (letter_mod.LetterError, state.StateError) as exc:
        rule = getattr(exc, "rule", "write")
        _fail(f"letter refused [{rule}]: {exc}")
    click.echo(f"letter: {path}")
    raise SystemExit(OK)

