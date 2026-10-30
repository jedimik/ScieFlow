# Manuscript check and package

`scieflow manuscript` checks a run's manuscript against its journal profile,
judges the reporting-checklist items that need a reader, renders and builds the
LaTeX, and packages what a submission needs. It never uploads anything and
never submits. The journal side (research, the profile format, the rules for
profiles) is in [Target journals](journals.md).

Every command takes a run (`<run>`). Every read and every check accepts
`--json`. Exit codes: `0` ok, `1` refused or a check failed (a report is still
written where the command writes one), `2` usage. Everything here runs locally:
no network call, and a build never starts `tectonic` in an L2 or no-egress run.

## Files in a run

```text
workspace/<run>/manuscript/
  metadata.yml          title, authors, declarations, keywords, highlights, journal slug
  main.tex              rendered by `render` (first line is a marker); hand edits are refused
  preamble.tex          copied by `render` for the article class, when absent
  sections/abstract.tex and one file per section (required)
  references.bib        bibliography (required)
  letters/              cover-letter.tex, response-to-reviewers.tex (starters)
  build/                main.pdf, main.log, main.bbl and build.json after a build
workspace/<run>/report/
  manuscript-check.json, manuscript-check.md
  reporting-checklist-<id>.md, checklist/<id>.judgement.json
  submission/           the package (see Package)
```

## The metadata file

`manuscript/metadata.yml` is checked against `schemas/manuscript-metadata.yml`;
unknown keys are refused.

```yaml
version: 1
journal: journal-of-synthetic-neurology   # the profile slug
article_type: research-article
title: "A Synthetic Study of Things"
keywords: [synthetic, example]
highlights: []                            # optional
authors:
  - name: A. Example
    email: a.example@example.org          # optional; never printed in messages
    orcid: 0000-0002-1825-0097            # optional; checked offline (checksum)
    affiliations: [{name: Department of Examples, ror: "https://ror.org/..."}]
    credit: [conceptualization, writing-original-draft]   # CRediT roles, checked offline
    corresponding: true                   # at most one author
declarations: {coi: "...", funding: "...", ethics: "...", data_availability: "...",
               code_availability: "...", ai_use: "..."}
sections: [introduction, methods, results, discussion]   # optional order of file stems
letters: {editor_name: "..."}             # optional; fills the cover letter
```

ORCID, ROR and CRediT values are checked with the vendored validators (checksum
for ORCID, pattern for ROR, the CRediT role list). Messages name an author by
position, never by e-mail address. These identifiers are checked offline only:
ScieFlow does not look them up in a registry.

## Word count

The word count is ScieFlow's own rule (residual R4). It is deterministic and
documented here so that a borderline count can be checked by hand.

1. Remove math: `$…$`, `$$…$$`, `\(…\)`, `\[…\]` and the `equation`, `align`,
   `gather`, `multline`, `eqnarray`, `displaymath` and `math` environments, starred
   forms included.
2. Remove the `figure`, `table`, `tabular`, `verbatim`, `lstlisting`, `minted`,
   `thebibliography` and `comment` environments. Captions are counted separately.
3. Remove commands that carry no text: `\cite…`, `\ref`, `\eqref`, `\autoref`,
   `\cref`, `\Cref`, `\label`, `\url`, `\includegraphics`, `\bibliography`,
   `\bibliographystyle`, `\input`, `\include`, `\usepackage`, `\documentclass`,
   `\newcommand`, `\renewcommand`, `\vspace`, `\hspace`, and the names in
   `\begin` and `\end`.
4. `\href{u}{t}` keeps `t`; any other `\command[opt]{arg}` keeps `arg`; `~`,
   `\\` and braces become spaces.
5. Words are the matches of `[^\W_](?:[\w'’.\-]*[^\W_])?`.

**Main text** is every section except those whose normalised title is
references, bibliography, acknowledgements or acknowledgments, funding,
declarations, competing interests, conflict(s) of interest, author
contributions, credit authorship contribution statement, data availability,
code availability, ethics, ethics statement, supplementary material or appendix.
Subsections belong to their section. **Abstract and title are counted on their own.** Title normalisation strips commands (keeping their arguments), turns `&`
into `and`, removes punctuation, lowercases and collapses whitespace.

## `scieflow manuscript check`

```bash
uv run scieflow manuscript check <run>
uv run scieflow manuscript check <run> --guideline consort-2010 --json
```

Options: `--journal SLUG` (default: `metadata.yml`), `--article-type ID`,
`--guideline ID` (repeatable; a reporting guideline to check), `--main FILE`
(default `main.tex`), `--json`, `--no-write`. Article type: `--article-type`,
else `metadata.article_type`, else the profile's only type. Without one, exit 1
with rule `article-type`.

The check writes `report/manuscript-check.json` and `report/manuscript-check.md`.
Both are deterministic and carry the profile's sha256 and the manuscript's
source tree hash. Exit `0` when there is no error finding, `1` otherwise.
Limits that are null in the profile are not checked. Every limit finding records
the limit and the value (counts only).

The rules:

| Rule | Severity | When |
| --- | --- | --- |
| `profile-missing`, `profile-legacy`, `profile-invalid` | error | No structured profile resolves, or it fails the profile rules. A legacy `.md`-only profile is refused. |
| `profile-stale` | warning | The profile is older than its `max_age_days`. |
| `article-type` | error | The article type is unknown or not given. |
| `abstract-missing`, `abstract-words` | error | No abstract, or more words than `words_abstract`. |
| `abstract-structure` | warning | The profile's abstract is structured and fewer than two of its headings (or Background, Objective(s), Methods, Results, Conclusion(s)) begin a paragraph or a bold `…:` label. |
| `title-words` | error | More words than `words_title`. |
| `main-words`, `section-words` | error | The main text is over `words_main`, or a section is over its `words_max`. |
| `keywords` | error | The count is outside `keywords_min` to `keywords_max`. Metadata keywords win over TeX keywords. |
| `highlights` | error | More than `highlights_max` highlights, or one over `highlight_chars_max` characters. |
| `figures`, `tables`, `display-items` | error | Over `figures_max`, `tables_max` or `display_items_max`. |
| `references-count` | error | The number of cited keys is outside `references_min` to `references_max`. |
| `citation-missing` | error | A `\cite` key has no bibliography entry. |
| `citation-uncited` | warning | A bibliography entry is never cited. |
| `reference-year` | warning | A cited entry has no year. The report also gives the median year and the share of the last five years, as information only. |
| `section-missing` | error | A required profile section matches no section title or alias. |
| `section-order` | warning | The profile says `order: strict` and the matched sections are out of order. |
| `declaration-missing` | error | A required declaration has neither a metadata text nor a matching section: `coi` (competing interests or conflict(s) of interest), `funding`, `ethics`, `data-availability`, `code-availability`, `ai-use` (use of AI or generative AI), `credit` (every author has at least one CRediT role), `orcid` (every author has a valid ORCID). |
| `orcid-invalid`, `ror-invalid`, `credit-invalid`, `corresponding` | error | A validator refuses a value, or more than one corresponding author is given. |
| `template-class`, `template-default` | warning | `\documentclass` differs from the profile's `latex_class`, or the profile names no class. |
| `bib-style` | warning | `\bibliographystyle` differs from the profile's `bibtex_style`. |
| `word-first` | warning | The profile says `word_first: true`. ScieFlow does not build DOCX (see below). |
| `guideline-*` | see checklists | Reporting checklist rules, below. |

## Reporting checklists and judgement

The guidelines checked are the union of: the `--guideline` ids; the design's
`reporting_guidelines` (`design/design.yml`, when present; a design that cannot be
read adds the `design-unreadable` warning and contributes nothing); and the
profile's `reporting_guidelines.by_study_type` for the design's study type. An
unknown id is `guideline-unknown` (error). A profile guideline that the design
does not list is `guideline-not-in-design` (warning; the design follows the
profile). A checklist file still marked unverified is `guideline-unverified`
(warning).

Each manuscript section is classed by its title: introduction (introduction,
background); methods (methods, method, materials and methods, patients and
methods, participants and methods, methodology, study design, experimental
procedures, statistical analysis); results (results, findings); discussion
(discussion, conclusion, conclusions, limitations, discussion and conclusions).
Anything else is "other". The title, the abstract and the whole manuscript are
also parts.

Each checklist item is then decided by the manuscript itself:

- A figure or table item (`diagram`, `figure`, `table`) is `present` when a
  figure or table environment sits in a matching part, otherwise `missing`.
- A registration item is `present` when a registry identifier appears anywhere
  (ClinicalTrials.gov, ISRCTN, PROSPERO, ACTRN, DRKS, ChiCTR, EudraCT, OSF).
  Otherwise `missing`.
- A statement or number item is `needs-judgement`.

Severity follows the checklist's verification (M-F-D9). A mechanically `missing`
item is `guideline-item-missing`: an **error** when the checklist is verified,
a **warning** when it is unverified. A `needs-judgement` item is informational
until a reader judges it.

To judge the `needs-judgement` items, write the reader's prompt and dispatch it
with the reviewer role:

```bash
uv run scieflow manuscript checklist-prompt <run> --guideline consort-2010 --write
uv run scieflow agent run --role research.reviewer <primary> workspace/<run>/prompts/checklist-consort-2010.md workspace/<run>/logs/checklist-consort-2010.log
uv run scieflow manuscript check <run> --guideline consort-2010
```

The reader writes `report/checklist/<id>.judgement.json`. A judgement is merged
only when its guideline and manuscript hash match the current manuscript
(otherwise `judgement-stale`). `present` needs a quote of at least 20 characters
that is found in the manuscript text. `absent` and `not-applicable` need a reason
of up to 300 characters. Invalid entries are ignored with `judgement-invalid`,
naming their item ids. A judgement never overrides `present` or `missing`.

The filled checklist is written to `report/reporting-checklist-<id>.md`. An
unverified checklist starts with a banner saying so: ScieFlow's checklists are
skeletons until a human verifies each item against the guideline (residual R5).

## `scieflow manuscript render`

```bash
uv run scieflow manuscript render <run> --class article
```

Writes `manuscript/main.tex` from `metadata.yml`, the profile and the section
files. Options: `--class [article|elsarticle|IEEEtran|svjour3]` (default: the
profile's `latex_class`, else `article`), `--article-type ID`, `--force`, `--json`.

- Classes and bibliography styles come from `templates/paper/registry.yml`.
  Class files are never vendored; the build finds them on the host.
- The sections are `metadata.sections` when given, else the profile's sections
  of the article type in order (`sections/<slugified title>.tex`; a required
  file that is missing is `section-file-missing`), else the IMRaD files that
  exist.
- Line 1 of the rendered `main.tex` is a marker with the class and a hash of the
  rest. `render` refuses to overwrite a `main.tex` without the marker, or one
  edited by hand. `--force` first keeps the old file as `main.tex.bak-<sha12>`.
- The same inputs give byte-identical output.

## `scieflow manuscript build`

```bash
uv run scieflow manuscript build <run> --engine latexmk --timeout 600
```

Options: `--engine [latexmk|pdflatex|xelatex|lualatex|tectonic]` (default: the
first of latexmk, pdflatex, xelatex, lualatex found on the host; `tectonic` only
when named), `--timeout SECONDS` (30 to 1800, default 600), `--json`.

The build:

1. Computes the source tree hash (`source_tree_sha256`) over the regular files of
   `manuscript/`, excluding `build/`, `drafts/`, `letters/`, backup files and LaTeX
   by-products. A symlink in the tree stops the build.
2. Checks that the document class and the bibliography style can be found, by
   file in `manuscript/` or with `kpsewhich`.
3. Copies the tree into `workspace/<run>/scratch/manuscript-build/`, then compiles
   there. Shell escape is off (`-no-shell-escape`; `shell_escape=f`), output
   writes are restricted to the scratch folder (`openout_any=p`), the command
   line is fixed (no shell), the environment is minimal, and each engine call
   has the timeout. A raw engine runs the usual passes, with `biber` or `bibtex`
   when needed.
4. On success copies `main.pdf`, `main.log` and `main.bbl` to `manuscript/build/`
   and writes `manuscript/build/build.json` with the engine, the return codes,
   `pdf_sha256` and `source_tree_sha256`. On failure it prints the last 40 lines
   of the log and exits 1.

`tectonic` downloads packages, so it is refused in an L2 run and in a run
without egress (and when the network is replaying). Residual R7: TeX can read
files that the user can read; the build compiles the run's own manuscript only,
and a hostile manuscript is outside the threat model.

## `scieflow manuscript package`

```bash
uv run scieflow manuscript package <run>
```

Writes `report/submission/` for the run. It is refused (exit 1, nothing written)
unless, in this order:

1. No review blocker is open (`scieflow review merge-check` names them), and no
   open `review-blocker` gate exists. Unreadable review state also refuses.
2. A structured, valid journal profile resolves.
3. `manuscript check` passes with no error, run in the same process.
4. `build/build.json` is ok, its source tree hash equals the current tree, and
   `main.pdf` matches its recorded hash. Otherwise run `manuscript build` first.
5. `manuscript/` contains no symlinks.

The package holds `main.pdf`; `source.zip` (the sources, with `main.bbl` when
present; fixed timestamps, so the same tree gives the same bytes); a
`cover-letter.tex` (from `letters/`, or the vendored template with the journal,
title, corresponding author and e-mail filled); `declarations.md` (in the
profile's order, with the CRediT statement); `highlights.txt` when there are
highlights; one `reporting-checklist-<id>.md` per checked guideline;
`manuscript-check.md`; `submission-checklist.md`; and `package.json` with the
file hashes, the check and build hashes, the guidelines and their verification,
and the data level. There are no timestamps. The checklist lists what is still
to do: each `submission.required_files` item, the placeholders left in the cover
letter, the staleness of the profile, unverified checklists, and the notes that
ORCID and ROR were checked offline only.

ScieFlow uploads nothing. You submit the package yourself. For a run at data
level L2 the package says that the results may be shared only through
`scieflow data release`.

## `scieflow manuscript letter`

```bash
uv run scieflow manuscript letter <run> --kind cover
uv run scieflow manuscript letter <run> --kind response --force
```

Writes a starter cover letter or a response to reviewers into
`manuscript/letters/`. Existing letters are kept unless `--force` is given. The
content is the authors'; the starter fills only fields ScieFlow knows, and the
package lists every placeholder left (residual R9).

## DOCX

ScieFlow does not build DOCX or run pandoc (decisions M-F-D1 and D20). A profile
marked `word_first: true` gets the `word-first` warning and the submission
checklist notes it. A pandoc or DOCX path is a follow-up for a journal that
requires Word.

## Known limits

- **R1. Live response shapes are untested** for journal research (see the
  journal page).
- **R2. Files are the integrity boundary** for journal sources and profiles (see
  the journal page).
- **R3. Publisher pages often refuse scripts** (see the journal page).
- **R4. Word counts are ScieFlow's rule**, not a journal's. The rule is above.
  Borderline counts deserve a human look.
- **R5. Checklists are unverified skeletons** until a human verifies them. Until
  then their findings are warnings, and every output says so.
- **R6. DNS-based SSRF** applies to journal research only (see the journal page).
- **R7. TeX can read files the user can read.** The build runs the run's own
  manuscript in a scratch copy with shell escape off.
- **R8. Classes not installed here.** `elsarticle`, `IEEEtran` and `svjour3` are
  not installed on the development host; only `article` is compiled for real in
  tests, and the other templates are checked textually.
- **R9. Cover letters and declarations are starters**; see `letter` above.
