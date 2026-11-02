# M-G1 claim ledger and journal revision — design (Part A of docs/superpowers/plans/2026-10-31-m-g1-claim-ledger-revision.md)

## Part A — Spec

### 5.1 Layout and files

```
workspace/<slug>/
  manuscript/ …                      (M-F: main.tex, sections, references.bib, metadata.yml)
  report/claims/claims.jsonl         one ledger line per claim (5.3), sorted by (file order, line, id)
  report/claims/ledger.json          {version, parser_version, main, manuscript_tree_sha256, count, claims_sha256}
  report/claims/check.json|check.md  the last check (5.6)
  revision/points/<label>/           a provenance point (5.9): point.json + files/…
  revision/round-<N>/                a decision-letter round (5.10–5.12)
  report/diff/<from>..<to>/          changes.json, diff.txt, diff.tex (when latexdiff exists)
  report/ro-crate/ro-crate-metadata.json   (5.14)
```

All writes are atomic (`journal.state.atomic_write`, M-F) and refuse a symbolic link on the path; every
read is confined to the run (`resolve()` + `is_relative_to(run)`), regular files only. Outputs are
deterministic: sorted keys, no timestamps, `\n` line ends, UTF-8.

### 5.2 Claim units (`claims/units.py`)

Input: `tex.read_tree(manuscript/, main, keep_comments=True)` (M-G-D16), document body only (from
`\begin{document}` to `\end{document}` or the first `\bibliography`, `\printbibliography` or
`\begin{thebibliography}`; a file without `\begin{document}` is all body, as M-F treats it).

- **Environment units**: each `figure`, `figure*`, `table`, `table*` environment is one unit (`unit:
  figure|table`); its text is the whole environment (caption and cells).
- **Abstract unit(s)**: sentences of `\begin{abstract}…\end{abstract}` (`unit: abstract`).
- **Sentence units**: the rest, split into paragraphs at blank lines and sectioning commands
  (`\section`, `\subsection`, `\subsubsection`, `\paragraph`, starred or not), then into sentences with
  `nblm.claims.split_sentences` on comment-stripped text (`unit: sentence`). Display-math environments
  (`tex.DISPLAY_ENVS`) belong to their sentence but contribute no numbers.
- **Section**: the title of the enclosing `\section` and `checklist.section_kinds(title)` (abstract units
  get kind `abstract`; units before the first section get `front`).
- **Comments**: a comment on line L (`%` not escaped, `tex.strip_line` rule) belongs to every unit whose
  span includes L; inside an environment it belongs to the environment unit.
- A unit is a **claim** when it has at least one checkable number (5.5), a reference (5.4) or a `\cite`.
  Other units are not ledgered.
- **Id**: `c-` + the first 12 hex of sha256(`file` + `\n` + normalised text) where normalised = NFC,
  comments removed, whitespace collapsed; a repeat within the ledger gets `-2`, `-3` in document order.
- **Limits**: at most 5,000 claims (more → exit 1 `claims-limit`); text stored up to 2,000 characters
  (`text_truncated: true` beyond).

### 5.3 Ledger line (`claims/ledger.py`, `schemas/claims-ledger.yml`)

```json
{"version": 1, "id": "c-1a2b3c4d5e6f", "file": "sections/results.tex", "line": 12, "end_line": 13,
 "unit": "sentence", "section": "Results", "section_kinds": ["results"],
 "text": "<normalised text>", "text_sha256": "<64 hex>",
 "numbers": [{"raw": "0.91", "value": "0.91", "decimals": 2, "percent": false, "relation": "="}],
 "refs": [{"type": "data", "id": "tbl-metrics", "via": "comment"},
          {"type": "run", "id": "denoise/sigma-1.5", "via": "inline"},
          {"type": "doi", "id": "10.5555/x.1", "key": "smith2019", "via": "cite"}],
 "cites": ["smith2019"],
 "pins": {"data:tbl-metrics": {"files": {"inputs/results/metrics.csv": "<sha256>"}}}}
```

- `build` writes `claims.jsonl` (one canonical JSON object per line) and `ledger.json`. Pins of a claim
  are carried from the previous ledger when its `id` and `refs` are unchanged; otherwise (and with
  `--repin`, `--repin <id>`) they are taken from the current evidence (5.4). An unresolved reference
  has no pin. `--repin` emits `note.claims_repinned` (count). Re-running `build` on an unchanged
  manuscript and unchanged evidence gives byte-identical files.
- `show <run> [--id ID] [--json]` prints the ledger (excerpts ≤ 60 characters in the table view).
- `ledger.json` `manuscript_tree_sha256` is `tex.source_tree_hash(manuscript/)`; `check` refuses a
  ledger whose hash differs from the current tree (`ledger-stale`, exit 1: "run `scieflow manuscript
  claims build`").

### 5.4 References and resolution (`claims/refs.py`, `claims/resolve.py`, `claims/evidence.py`)

Markers: `\[(data|run|doi):([^\]\s]{1,200})\]` in a comment of the unit (`via: comment`; the
paper-draft form is `% source: [data:<id>]`) or in its text (`via: inline`). Every `\cite`-family key
(`citations.CITE_RE`) is a `cite`; a key whose bib entry has a DOI (`nblm.claims.parse_bib`) adds a
`doi` ref (`via: cite`). DOIs are normalised (`https://doi.org/` prefix removed, lower case) and must pass
`core.review.claims.safe_doi`.

- **`data:<id>`** (id pattern `release.ID_PATTERN`): (1) an `inputs/manifest.yml` artifact with that
  `id` → `inputs/<file>`; (2) a `data.yml` `released` record → its `file` (sha256 re-checked with the
  `release._verify` rule; mismatch → C002 `artifact-altered`); (3) an `imports` record whose `release`
  equals the id → its `file` (same re-check). Two or more matches → `ref-ambiguous`; none →
  `ref-unresolved`; a manifest path that is absolute, contains `..`, leaves `inputs/` or is a symlink →
  `ref-unsafe` (all C001, error). **L2 rule (M-B 5.2 "only released artifacts … in a draft"):** when
  `levels.run_level(run)` is `L2`, a data ref resolved through the manifest (not a release or an import)
  is `data-unreleased` (error, not waivable).
- **`run:<ref>`**, inside `experiments/` of this run only (M-G-D15): (1) `<ref>` names a campaign folder
  with `results.json` → the **campaign**; (2) `<campaign>/<run_id>` with that campaign's
  `results.json` listing `run_id` → the **run**; (3) a bare `run_id` listed in exactly one campaign → the
  run; more than one → `ref-ambiguous`; none → `ref-unresolved`. Run ids pass `manifest._is_run_id`.
  Evidence files: a run's `metrics.json`, `manifest.json` and text-like files under `artifacts/`; a
  campaign's `results.json`, `results.csv`, `stats.json` and text-like files under `tables/`.
  Integrity: `manifest.verify_campaign(campaign)` problems naming that run (or any run, for a campaign
  ref) → C002 `artifact-altered`; a `legacy-run` note → warning `run-unverifiable`.
- **`doi:<doi>`**: the bib must contain the key (else `ref-unresolved`, C001); a bib entry without DOI →
  warning `doi-missing`; when `report/selected_dois.txt` exists and lacks the DOI → warning
  `doi-unselected` (the `citations.py:62-74` rule). Status: see 5.6 C005/C003. Every `\cite` key of
  a claim is also matched against the blockers, with or without a DOI (5.6 C005).
- **Text-like evidence**: extensions `.csv .tsv .json .txt .md .tex .yml .yaml`, ≤ 20 MB each, at most
  200 files per ref; other files (PNG, PDF, binaries) are listed and hashed but give no numbers
  (`number-uncheckable` warning when a claim's numbers can only be checked against them).
- **Pins**: `{"<type>:<id>": {"files": {<run-relative path>: <sha256>}}}` over the evidence files of
  the ref (sorted). DOI refs have no pin.

### 5.5 Numbers (`claims/numbers.py`) — M-G-D5

- Token: `[-−+]?(?:\d{1,3}(?:[,\u202f\u00a0 ]\d{3})+|\d+)(?:\.\d+)?` optionally followed by `\s*\\?%`;
  `−` and `\u2212` read as minus; thousands separators removed. Scientific notation in evidence
  (`1.2e-05`) is parsed; in the manuscript a `\times 10^{…}` form is not (follow-up; such a claim gets
  `number-uncheckable`).
- Relation: `<`, `\lt`, `≤`, `\le`, `\leq`, `>`, `\gt`, `≥`, `\ge`, `\geq` directly before the token (only
  spaces, `$` or `=` between) set `relation`; otherwise `=`.
- Not claims: tokens inside `\cite…{}`, `\ref{}`, `\eqref{}`, `\autoref{}`, `\label{}`, `\url{}`,
  `\href{…}{}` (first argument), `\includegraphics[…]{…}`, `\begin{…}[…]` options, `\documentclass`,
  display math; tokens directly after `Figure`, `Fig.`, `Figs.`, `Table`, `Tab.`, `Section`, `Sec.`,
  `Eq.`, `Equation`, `Supplementary`, `Appendix`, `Chapter`, `Step`, `Phase`, `Version`, `v` (with optional
  `~`/space, case-insensitive); integers 1900–2099 without `%` (years); ordinals (`1st`, `2nd`, …);
  tokens glued to letters on either side (`H2O`, `SF-36` → not checked).
- Match (C002 when a checked number has no match in the union of the claim's data/run evidence):
  manuscript value `t` with `d` decimals matches evidence value `a` when `round_half_up(a, d) == t` or
  `round_half_even(a, d) == t`; for `d = 0` `a` must be integral and equal; with `percent` also
  `a × 100` under the same rule; with a relation `<`/`≤`/`>`/`≥`, `a` must satisfy it. Decimal
  arithmetic (`decimal.Decimal`), never floats.
- A claim with data/run refs **and** a DOI cite: an unmatched number is the warning
  `number-unmatched-cited` (it may come from the cited paper), not C002.
- A claim with only DOI refs: numbers are not checked (`numbers_checked: false` in the report).
- Messages name the manuscript number, the claim's file:line and the evidence path; **never a value read
  from an evidence file** (M-B-D9).

### 5.6 The check (`claims/compile.py`, `claims/report.py`) — C001–C006

| Code | Rule ids | When | Severity |
|---|---|---|---|
| C001 orphan claim | `orphan`, `ref-unresolved`, `ref-ambiguous`, `ref-unsafe` | a checkable number and no data/run/doi ref; a ref that does not resolve | error in units with kinds `abstract`, `results`, or unit `table`/`figure`; warning elsewhere (`orphan` only; the `ref-*` rules are always errors) |
| C002 altered number | `number-missing`, `artifact-altered` | 5.5 match fails; evidence differs from its own record (5.4) | error |
| C003 stale evidence | `evidence-changed`, `source-corrected` | current evidence hashes ≠ pins; a cited DOI with a `corrected` flag in the latest evidence bundle | error / warning |
| C004 qualifier mismatch | — | reserved, never emitted (M-G-D3) | — |
| C005 retraction | `blocked-reference` | a blocker of `review.service.blockers()` names a reference of the claim: its `doi` (normalised as in 5.4) equals a ledgered DOI, or its `key` equals a cite key of the claim or that key's safe token (`k-` + first 16 hex of sha256(key) when the key is not `[A-Za-z0-9_.:/+-]{1,200}`; the `identity_gates._token` rule, reimplemented in `compile.py` because `_token` is private, and pinned equal to it by a test) | error, not waivable |
| C006 artifact-code drift | `code-dirty`, `code-not-ancestor` | a cited run's manifest `code.scieflow.git` or `code.pipeline.git` has `dirty: true`; its `sha` is not an ancestor of the repository HEAD (`git -C <repo root> merge-base --is-ancestor <sha> HEAD`, fixed argv, 10 s timeout, `GIT_OPTIONAL_LOCKS=0`; exit 1 or 128 → drift) | error |
| — | `data-unreleased` | 5.4 L2 rule | error, not waivable |
| — | `code-state-unknown`, `run-unverifiable`, `doi-missing`, `doi-unselected`, `doi-status-unknown`, `number-uncheckable`, `number-unmatched-cited` | as named in 5.4/5.5; `code-state-unknown` when a manifest `git` is null or git is unavailable; `doi-status-unknown` once per check when the run has no evidence round and no identity check | warning |

- The C005 message names the claim, the reference (DOI or key token) and the blocker `reason`
  (`retracted`, `unresolvable`, `identity-mismatch`, …) and says how it clears (fix the reference, or
  the user overrules the `review-blocker` gate); never the blocker's `detail` text.
- A status lookup that fails (`blockers()` raises `ValueError`/`OSError`) is the warning
  `status-unreadable`, never C005 (researcher's rule, section 4) — but the **package** treats it as a
  refusal (fail closed, 5.8).
- Output: `report/claims/check.json` `{version, manuscript_tree_sha256, ledger_sha256, counts: {by_code,
  errors, warnings, waived}, findings: [{code, rule, severity, claim, file, line, ref?, message}],
  waived: [{claim, code, rule}]}` sorted by (file order, line, code, rule); `check.md` the same as a
  table. `--json` prints the document; `--no-write` writes nothing. Exit 0 without errors, 1 with
  errors or bad input. One `note.claims_checked` event (counts) when written.

### 5.7 Waivers (`claims/waivers.py`) — M-G-D7

`scieflow manuscript claims waive <run> <claim-id> --code C00x --reason TEXT`: refused when
`SCIEFLOW_DISPATCH` is set ("the user waives; sub-agents never do"); the claim must exist in the
current ledger and currently have a finding with that code; C005 and `data-unreleased` are refused;
`--reason` 10–500 characters. Records `note.claims_waived` with actor `human`, `{claim, code,
text_sha256, reason}`. The check applies a waiver only when a `note.claims_waived` event with actor
`human` has the claim's current `text_sha256` (a changed sentence voids it); waived findings move to
`waived` and do not count as errors.

### 5.8 Package refusal (`package.py`) — M-G-D8

`_refuse_claims(project, run)` is called right after `_no_links(folder)` (`package.py:294`), so every
M-F refusal still comes first. It calls `claims.compile.check_run(project, run)` (public; M-G2's verify
gate will call the same function), which builds the ledger in memory (pins carried from an existing, readable
`claims.jsonl`; none otherwise), runs the check in memory and raises `PackageError("claims", "<n>
claim error(s): run scieflow manuscript claims check <run>")` on any error. Any exception while building
or checking → `PackageError("claims", "the claim ledger could not be built (<ExceptionType>); refused
(fail closed)")`. Nothing is written by this step. `package.json` is unchanged.

### 5.9 Provenance points and diffs (`points.py`, `diff.py`) — M-G-D12

- `scieflow manuscript point add <run> <label> [--from-package]`: label `^[a-z0-9][a-z0-9.-]{0,39}$`,
  refused if it exists. Without `--from-package` it copies the files `tex.source_tree_hash` counts
  (same exclusions) into `revision/points/<label>/files/` and writes `point.json` `{version, label,
  origin: "current", tree_sha256, files: {rel: sha256}}`. With `--from-package` (refused in a dispatch)
  it reads `report/submission/package.json` and `source.zip`, requires `sha256(source.zip) ==
  package.json files["source.zip"]`, and extracts entries whose names are relative, without `..`,
  backslash or NUL, at most 200 entries and 50 MB in total (`origin: "package"`, `package_sha256`). A
  symlink anywhere, more than 200 files or 50 MB → exit 1. `point list <run> [--json]`. Event
  `note.manuscript_point`.
- `scieflow manuscript diff <run> --from <label> [--to <label>|current] [--main main.tex] [--json]`:
  writes `report/diff/<from>..<to>/changes.json` (`added`, `removed`, `changed` with both sha256s),
  `diff.txt` (`difflib.unified_diff` over each side's flattened text, comments kept, `n=3`), and, when
  `shutil.which("latexdiff")` finds it, `diff.tex`: both sides are flattened by ScieFlow into
  `scratch/manuscript-diff/{old,new}.tex`, then `latexdiff --encoding=utf8 old.tex new.tex` runs with
  `cwd` that folder, `env={"PATH": "/usr/bin:/bin", "HOME": <that folder>}`, no shell, timeout 120 s,
  stdout capped at 20 MB, stderr tail kept for errors. Absent → `notes: ["latexdiff-not-installed"]`,
  exit 0. latexdiff failure → exit 1 with the last 20 stderr lines. Event `note.manuscript_diff`
  (counts).

### 5.10 Decision-letter ingest (`revision/letter.py`) — M-G-D9

`scieflow manuscript revision ingest <run> --letter FILE [--round N] [--decision accept|minor|major|reject]`:

- Refused in a dispatch. FILE: a regular file (no symlink), ≤ 1 MB, suffix `.txt`, `.md`, `.html` or
  `.htm` (HTML via `journal.html.to_text`); decoded UTF-8 (`errors="strict"`; invalid → exit 1), NFC,
  CRLF/CR → LF, trailing spaces removed. Round: `--round` or 1 + the highest existing; an existing
  round folder → exit 1.
- Writes `revision/round-<N>/letter.txt` and `letter.json` `{version, round, letter_sha256,
  source_suffix, decision, decision_source: "flag"|"letter"|"unknown", blocks: [{id, label_line,
  start, end, paragraphs: [{id, start, end}]}]}`.
- **Blocks**: a header is a line ≤ 120 characters matching (case-insensitive)
  `^\s*(?:reviewer|referee)\s*#?\s*(\d{1,2})\b` → `R<n>`, or
  `^\s*(?:(?:associate|academic|handling|section)\s+)?editor(?:-in-chief)?\b` → `E` (a later editor
  header → `E2`, `E3`). Text before the first header belongs to `E`. A repeated `R<n>` header is
  treated as text (continuation). Paragraphs: maximal runs of non-blank lines; ids `<block>.p<k>`.
  An empty block is dropped.
- **Decision**: `--decision` wins (`flag`); otherwise the first match of `accept(ed)? (for
  publication|as is)`, `minor revision`, `major revision`, `reject(ed)?` in the `E` block; two different
  matches → `unknown`.
- Then point `submitted-r<N>`: from the package when `report/submission/package.json` exists and its
  `source.zip` checks (5.9), else from the current manuscript with the printed note `point taken from
  the current manuscript, not from a submission package`; an existing point of that name is kept.
- Event `note.revision_ingested` `{round, blocks, paragraphs, decision, letter_sha256}` — never letter
  text.

### 5.11 Structuring prompt, draft and adopt (`revision/draft.py`, `revision/adopt.py`)

- `revision prompt <run> --round N --agent NAME [--write]`: a prompt for `--role research.submitter`
  that contains the output path `revision/round-<N>/draft/<NAME>.json`, the draft format below, the
  rules, and the letter **as an untrusted fenced block with line numbers** (`passes._fenced(…,
  untrusted=True)`), plus the block/paragraph table. `--write` stores it in `prompts/` and prints the
  `scieflow agent run --role research.submitter …` line (the review-prompt pattern,
  `core/review/cli.py:384-401`).
- Draft format:
  ```json
  {"version": 1, "round": 1, "letter_sha256": "<64 hex>",
   "reviews": [{"block": "R1", "review": {<manuscript-review document>}}],
   "not_comments": [{"paragraph": "R1.p1", "reason": "greeting"}]}
  ```
  Each `review` must validate with `decisions.schema_error("manuscript-review", …)` and in addition:
  `agent` = `journal-<block in lower case>`, `reviewed` = `manuscript`, no `pass`, `evidence`,
  `followup_requests`, `scite_status`, `changed_from_blind`; ids unique within the review;
  `location` = `letter:<a>-<b>` (1-based lines of `letter.txt`) inside that block; `problem` (major) /
  `comment` (minor) is a **verbatim** substring of letter lines a..b after NFC and whitespace collapse,
  at least 20 characters or the whole range text; `why_it_matters` / `resolution` are free text (shown
  as "agent's reading" in the response starter).
- `revision adopt <run> --round N --draft PATH`: refused in a dispatch; PATH inside the run, regular, ≤
  1 MB. Checks in order, nothing written on any failure: `letter.txt` sha = `letter.json` = draft
  `letter_sha256` (`letter-changed`); block set equal (`blocks-differ`); schema and the extra rules
  (`review-invalid` with the JSON path); verbatim (`quote-not-in-letter`, naming comment id and line
  range, never quoting the letter beyond 60 characters); **every paragraph of every block is overlapped
  by a comment range or listed once in `not_comments`** with a reason of 3–200 characters
  (`letter-uncovered`, `not-comment-conflict` when listed and commented). Writes
  `revision/round-<N>/reviews/<block>.json` (canonical JSON, one manuscript-review document each) and
  `adopted.json` `{version, round, letter_sha256, draft_sha256, comments: ["R1.M1", "R1.m1", …],
  not_comments}`; an existing `adopted.json` → `--replace` required. Event `note.revision_adopted`
  (counts).

### 5.12 Response, coverage and the response letter (`revision/respond.py`, `coverage.py`, `render.py`) — M-G-D10

- `revision respond <run> --round N [--force]` writes `revision/round-<N>/response.md`: a header with the
  decision and the `not_comments` list, then per comment id in block order `## R1.M1 (major) —
  letter:12-15`, the comment as a `> ` quote, the agent's reading (major only), and the lines
  `Response: [write your response]` and `Change: [file — what changed]` / `No change: [why]`.
  Refuses to overwrite without `--force`.
- `revision coverage <run> --round N [--json]`: parses `response.md` headings (`^#{1,6}\s*([ERE0-9.Mm]+)`
  for ids of `adopted.json`); per comment id: `missing` (no section), `no-response` (no `Response:` line
  with text other than a `[…]` placeholder), `no-change-line` (neither line), `both-change-lines`,
  `no-change-reason` (`No change:` text < 10 characters), `change-path` (the `Change:` text must begin
  with a manuscript-relative path that `tex`'s rules accept), `change-not-found` (that file is equal in
  point `submitted-r<N>` and the current manuscript, or absent from both). Unknown ids → warning
  `unknown-comment`. Writes `coverage.json` `{ok, comments, problems}`; exit 0 when ok, else 1. Event
  `note.revision_coverage`.
- `revision letter <run> --round N [--force]`: refused unless coverage is ok. Writes
  `manuscript/letters/response-to-reviewers-r<N>.tex`: the vendored template up to and including its
  `\newpage` after `\tableofcontents`, filled by `letters.response_text` (title), then per block
  `\section{Reviewer n}` (`Editor` for E blocks) and per comment `\subsection{Comment R1.M1}`,
  `\reviewercomment{…}`, `\response{…}`, and the `Change:` text, all through `render.escape`, then
  `\end{document}`. `letters/` is outside the source-tree hash (`tex.py:56`).
- Acceptance loop (GAP "letter → JSON → coverage loop green on a fixture"): ingest → draft → adopt →
  respond → answer and change a section file → coverage exit 0 → letter written.

### 5.13 Lifecycle wiring — moved to M-G2 (M-G-D17, M-G-D19)

Not part of M-G1. The round-1 design (verify-phase claim gate, `paper-revision` workflow) is section
10.2; the M-G2 plan re-designs it against the landed paper-lifecycle code. M-G1 provides what M-G2 will
call: the in-memory build-and-check used by the package refusal (5.8) is a public function
`claims.compile.check_run(project, run) -> Report` so that M-G2 needs no change in the claims package.

### 5.14 RO-Crate (`crate.py`) — M-G-D13

`scieflow manuscript crate <run> [--json] [--no-write]` → `report/ro-crate/ro-crate-metadata.json`:

- `@context` `https://w3id.org/ro/crate/1.1/context`; entity 1 the metadata descriptor `{"@id":
  "ro-crate-metadata.json", "@type": "CreativeWork", "conformsTo": {"@id":
  "https://w3id.org/ro/crate/1.1"}, "about": {"@id": "./"}}`; entity 2 the root `{"@id": "./",
  "@type": "Dataset", "name": <metadata.yml title or the run slug>, "hasPart": [...], "author": [...]}`.
- Paths in `@id` are relative to the run folder (the crate root). Manuscript source files (the
  source-tree selection) and every evidence file of 5.4 are `File` entities `{name, identifier:
  "sha256:<hex>", encodingFormat}`; authors from `metadata.yml` are `Person` `#person-<k>` `{name,
  identifier: <ORCID URL when valid>}` — no e-mail, no affiliation address.
- Claims are `CreativeWork` `#claim-<id>` `{name: "claim <id>", text, isPartOf: {"@id": <file>},
  additionalType: <unit>, citation: [DOI entities], isBasedOn: [data/run evidence entities]}`.
  DOIs are `ScholarlyArticle` `https://doi.org/<doi>` `{identifier: <doi>}`. Runs are `CreateAction`
  `#run-<campaign>-<run_id with / → ->` `{name, endTime: manifest finished, instrument: {"@id":
  "#stage-<name>"}, result: [artifact files]}` with `SoftwareApplication` `#stage-<name>` `{name,
  identifier: "sha256:<script sha256>"}`.
- L2 run: a data ref resolved through the manifest (not released) becomes `#data-<id>` `{"@type":
  "CreativeWork", name: <id>, conditionsOfAccess: "L2 data, not released"}` with no path or hash.
- Order: descriptor, root, then entities sorted by `@id`; every `{"@id": …}` reference points to an
  entity in `@graph`; no absolute path, no `..`. Deterministic bytes. Event `note.manuscript_crate`.

### 5.15 CLI surface and exit codes

```
scieflow manuscript claims build <run> [--main F] [--repin] [--repin-claim ID ...] [--json]
scieflow manuscript claims show  <run> [--id ID] [--json]
scieflow manuscript claims check <run> [--json] [--no-write]
scieflow manuscript claims waive <run> <claim-id> --code C00x --reason TEXT
scieflow manuscript point add  <run> <label> [--from-package]
scieflow manuscript point list <run> [--json]
scieflow manuscript diff <run> --from LABEL [--to LABEL|current] [--main F] [--json]
scieflow manuscript revision ingest   <run> --letter FILE [--round N] [--decision D]
scieflow manuscript revision prompt   <run> --round N --agent NAME [--write]
scieflow manuscript revision adopt    <run> --round N --draft PATH [--replace]
scieflow manuscript revision respond  <run> --round N [--force]
scieflow manuscript revision coverage <run> --round N [--json]
scieflow manuscript revision letter   <run> --round N [--force]
scieflow manuscript revision show     <run> [--round N] [--json]
scieflow manuscript crate <run> [--json] [--no-write]
```

Exit codes: the module's `0` ok, `1` refused or failed check, `2` usage (`cli.py:20`). Refusals in a
dispatch name the command and say it is the user's or the coordinator's.

### 5.16 Data protection, security and integrity (reviewed in every ticket that touches them)

| Property | How |
|---|---|
| No egress | M-G makes no network call; `doi` status comes from local review state only; no new dependency |
| No data values in messages or events | messages name manuscript numbers, files and rule ids; evidence values never printed (M-B-D9); excerpts ≤ 60 chars; events carry counts, ids, hashes |
| Confinement | every path resolved inside the run; symlinks refused; run ids per `_is_run_id`; zip names validated; ≤ limits of 5.2, 5.4, 5.9, 5.10 |
| Untrusted letters | letter text is data (root rule 8): fenced as untrusted in the prompt; HTML through the existing text extractor; never executed |
| Quotes, not trust | every adopted comment re-checked verbatim against the stored `letter.txt`, not the prompt copy; every paragraph accounted for |
| Human-only actions | `revision ingest`, `revision adopt`, `point add --from-package`, `claims waive` refused when `SCIEFLOW_DISPATCH` is set; waivers count only from `actor: human` events |
| L2 | `data-unreleased` error; RO-Crate lists unreleased L2 data without path or hash; nothing leaves the run |
| Fail closed | package refuses when the ledger or blocker state cannot be read (the verify gate is M-G2) |
| Subprocesses | `git merge-base` and `latexdiff` only: fixed argv, no shell, timeouts, minimal env, capped output |
| Determinism | sorted JSON, no timestamps, byte-identical on re-run |

### 5.17 What M-G does not do (stated)

No lifecycle wiring (verify gate, `paper-revision` workflow: M-G2, section 10); no C004 (M-G-D3); no PROV-JSON-LD; no PDF of the diff; no PDF letters; no web page or menu item (D7: the
CLI is complete); no cross-run `[run:]` (M-G-D15); no change to internal review rounds; numbers written as
words ("forty-three") are not recognised (same limit as `core/review/claims.py:21-22`); a number that
also happens to appear elsewhere in the evidence file is accepted ("appears in" is weaker than "is the
value of row X"); retraction status is only as fresh as the last evidence round or identity check.

