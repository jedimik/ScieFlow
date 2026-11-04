# Journal revision

`scieflow manuscript revision` handles a journal's decision letter: it stores the letter
as a round, has a sub-agent structure it into reviewer comments, adopts those comments
after checking them against the letter, and then checks that every comment is answered.
`scieflow manuscript point` and `scieflow manuscript diff` pin the manuscript as it was
at each submission, and compare two pins. The claim check that a revision should pass
is on the [Claim ledger and check](claims.md) page.

Everything here is local. Letters are data, not instructions: a letter is fenced as
untrusted text in every prompt, and it is never executed. Exit codes: `0` ok, `1` refused
or failed, `2` usage.

## Who runs what

| Step | Command | Who |
| --- | --- | --- |
| Store the letter | `scieflow manuscript revision ingest` | the user |
| Pin the submitted version | `scieflow manuscript point add <run> submitted-r<N> --from-package` | the user |
| Write the structuring prompt | `scieflow manuscript revision prompt` (`--role research.submitter`) | coordinator |
| Structure the letter | sub-agent writes `revision/round-<N>/draft/<agent>.json` | sub-agent |
| Adopt the comments | `scieflow manuscript revision adopt` | coordinator |
| Write the response starter | `scieflow manuscript revision respond` | coordinator |
| Answer the comments | the authors edit `response.md` and the section files | the authors |
| Check coverage | `scieflow manuscript revision coverage` | coordinator |
| Write the response letter | `scieflow manuscript revision letter` | coordinator |

`revision ingest` and `revision adopt` are refused when `SCIEFLOW_DISPATCH` is set, so
a sub-agent can neither store a letter nor adopt its own draft. `point add --from-package`
is refused in a dispatch as well.

## Commands

```bash
uv run scieflow manuscript revision ingest <run> --letter FILE [--round N] [--decision accept|minor|major|reject]
uv run scieflow manuscript revision prompt <run> --round N --agent NAME [--write]
uv run scieflow manuscript revision adopt <run> --round N --draft PATH [--replace]
uv run scieflow manuscript revision respond <run> --round N [--force]
uv run scieflow manuscript revision coverage <run> --round N [--json]
uv run scieflow manuscript revision letter <run> --round N [--force]
uv run scieflow manuscript revision show <run> [--json]
uv run scieflow manuscript point add <run> <label> [--from-package]
uv run scieflow manuscript point list <run> [--json]
uv run scieflow manuscript diff <run> --from <label> [--to <label>|current] [--main main.tex] [--json]
```

`revision show` prints the rounds without any letter text.

## Ingest

`ingest --letter FILE` takes a regular file inside the run folder, at most 1 MB, with the
suffix `.txt`, `.md`, `.html` or `.htm`. HTML is converted to text. The file must decode as
UTF-8; line ends are normalised to `\n`, and trailing spaces are removed.

- The round is `--round`, or one past the highest existing round. An existing round folder
  is refused.
- It writes `revision/round-<N>/letter.txt` and `letter.json` (the letter hash, the decision,
  the blocks and their paragraphs). The decision is `--decision` when given. Otherwise the
  letter's own wording in the editor block is read: `accept`, `minor revision`,
  `major revision` or `reject`. Two different matches give `unknown`.
- Blocks are reviewer headers `R1`, `R2`, … and editor headers `E`, `E2`, … A header is a line
  of at most 120 characters that starts with `Reviewer n` or `Referee n`, or with `Editor`
  (also `Associate Editor`, `Handling Editor` and similar). Text before the first header is
  the editor block. A repeated `Rn` header is continuation text. Paragraphs are runs of
  non-blank lines with ids `R1.p1`, `R1.p2`, and so on. Paragraph ids are the unit that
  coverage checks.
- It then pins the point `submitted-r<N>`: from `report/submission/source.zip` when that package
  exists (a package that fails its integrity check refuses the ingest and writes nothing); from
  the current manuscript only when there is no package, with a printed note that the point was
  not taken from a submission package. An existing point of that name is kept.
- The event `note.revision_ingested` carries counts and the letter hash. It never carries the
  letter text.

## Prompt and draft

`prompt --round N --agent NAME` builds the structuring prompt for `--role research.submitter`.
The prompt names the draft path `revision/round-<N>/draft/<NAME>.json`, the draft format and
the rules. It contains the letter as an untrusted fenced block with line numbers, and the
table of blocks and paragraphs. `--write` stores the prompt under `prompts/` and prints the
dispatch line (`scieflow agent run --role research.submitter …`) instead of the prompt.

The draft is a JSON document:

```json
{"version": 1, "round": 1, "letter_sha256": "<64 hex>",
 "reviews": [{"block": "R1", "review": {"...": "manuscript-review document"}}],
 "not_comments": [{"paragraph": "R1.p1", "reason": "greeting"}]}
```

Every `review` validates against the manuscript-review schema and, in addition: `agent` is
`journal-<block in lower case>`, `reviewed` is `manuscript`, there is no `pass`, `evidence`,
`followup_requests`, `scite_status` or `changed_from_blind`, ids are unique within the review,
and `location` is `letter:<a>-<b>` (1-based lines of `letter.txt`) inside that block. A
`problem` (major) or `comment` (minor) must be a verbatim substring of those lines after
Unicode normalisation and whitespace collapse, and at least 20 characters long or the whole
range. Free text in `why_it_matters` and `resolution` is shown as the agent's reading.

## Adopt

`adopt --round N --draft PATH` checks the draft against the stored letter. Nothing is
written if any check fails. The checks run in this order:

1. The letter hash agrees between `letter.txt`, `letter.json` and the draft (`letter-changed`).
2. The draft covers the same blocks as the letter (`blocks-differ`).
3. Schema and the extra rules above (`review-invalid`, with the JSON path).
4. Every quote is verbatim in the letter (`quote-not-in-letter`, naming the comment and its
   line range; the letter is never quoted beyond 60 characters).
5. Every paragraph is covered by a comment range or listed once in `not_comments` with a reason
   of 3 to 200 characters (`letter-uncovered`). A paragraph that is both commented and listed is
   `not-comment-conflict`.

On success it writes one canonical review per block to `revision/round-<N>/reviews/<block>.json`
and `adopted.json` (the letter and draft hashes, the comment ids such as `R1.M1` and `R1.m1`,
and the `not_comments`). An existing `adopted.json` needs `--replace`. The event
`note.revision_adopted` carries counts.

## Response starter, coverage and the response letter

`respond --round N` writes `revision/round-<N>/response.md`: a header with the decision and
the `not_comments` list, then one section per adopted comment in block order, with the comment
quoted and, for major comments, the agent's reading. Each section ends with three placeholder
lines:

```text
Response: [write your response]
Change: [file — what changed]
No change: [why]
```

Keep one `Change:` line with a manuscript-relative file name, or one `No change:` line with
a reason. `--force` replaces an existing `response.md`, and the typed text is lost.

`coverage --round N` reads `response.md`. It matches each heading's first token exactly
against the adopted comment ids. Per comment it reports:

- `missing`: no section for the comment.
- `no-response`: no `Response:` line with text other than a `[…]` placeholder.
- `no-change-line`: neither a `Change:` nor a `No change:` line.
- `both-change-lines`: both lines.
- `no-change-reason`: a `No change:` text under 10 characters.
- `change-path`: a `Change:` text that does not begin with a manuscript-relative path.
- `change-not-found`: the named file is equal in the point `submitted-r<N>` and the current
  manuscript, or absent from both.

A heading whose id is not adopted is the warning `unknown-comment`. Coverage writes
`coverage.json` (`ok`, `comments`, `problems`) and exits 0 only when every comment passes.

`letter --round N` is refused unless coverage is ok. It writes
`manuscript/letters/response-to-reviewers-r<N>.tex` from the vendored template: the title,
then one section per reviewer (or editor) and one subsection per comment with the comment, the
response and the change text. Every value is escaped. `--force` replaces an existing letter.
The letter is outside the source-tree hash.

## Points and diffs

`point add <run> <label>` pins the manuscript as provenance point `<label>`. The label is
lower-case letters, digits, `.` and `-`, at most 40 characters, and an existing label is
refused. Without `--from-package` it copies the manuscript source files into
`revision/points/<label>/files/` and writes `point.json`. With `--from-package` (refused in a
dispatch) it extracts `report/submission/source.zip`, after checking its hash against
`package.json`. The extraction refuses symbolic links, names with `..` or backslashes,
more than 200 files, and more than 50 MB. `point list` lists the points.

`diff <run> --from <label> [--to <label>|current]` writes `report/diff/<from>..<to>/`:

- `changes.json`: the files added, removed and changed, with both hashes.
- `diff.txt`: a unified diff of each side's flattened text, comments kept.
- `diff.tex`: a latexdiff markup, only when `latexdiff` is installed. Without it the
  summary carries the note `latexdiff-not-installed` and the command still exits 0.

`latexdiff` runs with a fixed argument list, no shell, a minimal environment, a 120-second
timeout and capped output. A latexdiff failure exits 1 and shows the last 20 stderr lines.

## The paper-revision workflow

A decision letter for a submitted manuscript can run as a `paper-revision` run. Its six phases
are `intake`, `structure`, `revise`, `respond`, `verify` and `resubmit`, and the gates enforce
two of them. Start one with `uv run scieflow run init SLUG --workflow paper-revision --goal FILE
--approval per-campaign`, or with `run adopt` for a prepared folder; see
[the paper-revision gates](../runs.md#paper-revision-runs-and-phase-gates). Otherwise revise in place in the draft run, with Phases A to D of the journal-revision skill. That route has no phase
tracking.

| Phase | What happens | Command | Gate on `done` |
|---|---|---|---|
| `intake` | The user brings the manuscript as submitted into `manuscript/`, and its evidence as a data package or a `scieflow data import` of a release. Never another run's `experiments/` or `inputs/`. The user stores the letter with `ingest`. | `scieflow manuscript revision ingest` | none |
| `structure` | A sub-agent drafts the structure; the coordinator adopts it. | `scieflow manuscript revision prompt` (`--role research.submitter`), then `scieflow manuscript revision adopt` | none |
| `revise` | The starter is written; the authors change the manuscript. | `scieflow manuscript revision respond` | none |
| `respond` | Every comment is answered in `response.md`. | `scieflow manuscript revision coverage` | coverage passes for the highest round |
| `verify` | The claim check runs. | `scieflow manuscript build`, `scieflow manuscript claims build`, `scieflow manuscript claims check` | no claim errors |
| `resubmit` | The response letter and the package are written. | `scieflow manuscript revision letter`, `scieflow manuscript package` | none; the package refuses on claim errors itself |

`run mark` checks the gates, and a refusal names the command to run and leaves the phase as it
was. The evidence rule (M-G-D15) applies: a run reference stays inside its own run, so evidence
from another run comes in only as a data package or a `scieflow data import` of a release. The
user reads the response letter before anything is submitted.

## Limits and residuals

- A letter is read by block headers and paragraphs. A heading the heuristic does not recognise
  falls into the previous block, and the paragraph coverage rule is the safeguard.
- The structuring step is a sub-agent's work. Adoption checks the quotes and the coverage, not
  the judgement in `why_it_matters` or `resolution`.
- The letter is written only after coverage passes. Coverage checks the presence of a change
  file, not that the change answers the comment.
- No PDF of the diff and no PDF letters. The letter is LaTeX only.
- The paper-revision gates have residuals of their own, R1 to R3, listed under
  [claims](claims.md#limits-and-residuals).
- File-based integrity (R2). The revision events (`note.revision_ingested`, `note.revision_adopted`,
  `note.revision_coverage`) are plain lines in the run's events file, which a run's own agents can
  write; a forged event is not caught. See [claims](claims.md#limits-and-residuals).
- `latexdiff` is not installed on the test host. Only the mocked command line and the plain diff
  are tested there; the real-binary test runs only where `latexdiff` is installed (R4).
