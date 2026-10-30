# Target journals

ScieFlow can research a target journal's author guidelines, turn them into a
checked **journal profile**, and then check a manuscript against that profile.
This page covers the journal side: the flow, the profile format, the rules
and the limits. The manuscript side (the check, the reporting checklists, the
build and the package) is in [Manuscript check and package](manuscript.md).

Commands take a run (`<run>`) or a profile slug (`<slug>`). Every read accepts
`--json`. Exit codes: `0` ok (including "waiting for the gate" and "denied"),
`1` refused, a validation failure or an ambiguous journal, `2` usage, `3` a
Gateway policy or data-protection refusal (nothing was sent), `6` a no-egress
miss (nothing was sent).

## The flow

1. **Research the journal.** `scieflow journal research` gathers facts about
   the journal (OpenAlex and Crossref) and records them in the run:

    ```bash
    uv run scieflow journal research <run> --journal "Journal of Synthetic Neurology" --issn 1234-5679
    ```

    Without `--issn` the name must match exactly one OpenAlex source. If it
    matches several, the command lists up to ten candidates and asks you to
    re-run with `--issn`. ScieFlow never guesses between them.

2. **Approve the guideline pages (human only).** Author-guideline pages are
    downloaded only when `journal-guidelines` is switched on (see below). Then
    the command opens a `journal-sources` gate listing the exact URLs and their
    hosts, and prints the answer line:

    ```bash
    uv run scieflow gate answer <run> <gate-id> approve
    ```

    Only a human answers this gate. An agent never answers it, never opens it on
    the human's behalf, and never adds a URL to it. While it is open the command
    exits 0 and says it is waiting; a denied gate also exits 0 and fetches nothing.

3. **Fetch.** Run `journal research` again. For each approved URL ScieFlow
    makes one HTTPS GET, stores the body as received and a plain-text copy, and
    records a row in `journal/sources.json`. `--retry-failed` re-requests only
    the failed rows of the same approved list.

4. **Or save the pages yourself.** When `journal-guidelines` is off, or a
    publisher refuses scripted downloads (R3 below), save the guideline page in
    your browser and add it. Adding a source is the user's act:

    ```bash
    uv run scieflow journal add-source <run> --file guidelines.html --url https://publisher.example/authors --note "saved from the publisher's site"
    ```

    `--note TEXT` is optional and is kept with the source row.

    The file must be a regular file of at most 5 MB with the suffix `.html`,
    `.htm`, `.txt` or `.md`. A PDF guide is saved as text first (no PDF
    extraction in this version).

5. **Draft the profile.** Write the agent prompt, dispatch the drafting agents,
    then have a primary agent check their drafts:

    ```bash
    uv run scieflow journal prompt <run> --for agy --write
    uv run scieflow agent run --role research.journal-profile agy workspace/<run>/prompts/journal-profile-agy.md workspace/<run>/logs/journal-profile-agy.log
    uv run scieflow journal prompt <run> --cross-check --write
    uv run scieflow agent run --role research.journal-profile <primary> workspace/<run>/prompts/journal-profile.md workspace/<run>/logs/journal-profile-check.log
    ```

    A support agent writes `journal/draft-<agent>.yml`. The primary agent's
    cross-check verifies every value and quote against the stored sources and
    writes `journal/profile.draft.yml`. Support agents never produce a profile
    alone (tier rule: a support agent is always paired with a primary one). The
    prompt carries the stored pages as fenced, untrusted data and caps each page
    at 80,000 characters (240,000 in total).

6. **Adopt.** The coordinator adopts the checked draft:

    ```bash
    uv run scieflow journal adopt <run>
    ```

    `adopt` replaces `identity`, `oa` and `norms` with the values from
    `journal/facts.json`, runs every rule below, and writes nothing when one
    fails. On success it writes `config/journals/<slug>.yml`, the rendered
    `config/journals/<slug>.md` (only when no hand-written `.md` of that slug
    exists), and the run's copy under `journal/`. An existing adopted profile of
    the same slug with different content needs `--replace`; `journal diff` shows
    the difference first.

`adopt` and `add-source` refuse inside an agent dispatch. A sub-agent never
adopts a profile and never adds a page.

## Other commands

| Command | Purpose |
| --- | --- |
| `scieflow journal show <slug>` | Show a profile. Refuses one that does not validate. `--article-type ID` shows one type; `--prompt` prints it as one fenced data block for a review prompt. |
| `scieflow journal list` | Every profile, structured (`.yml`) or legacy (`.md` only), with its adoption date and whether it is stale. |
| `scieflow journal validate <slug or path>` | The rules below. Quotes are re-checked when the source run still exists. |
| `scieflow journal diff <slug> <run>` | Field-level difference between the adopted profile and the run's draft. Lines read `~ path: old -> new`, `+ path: value`, `- path`. |
| `scieflow journal style <run>` | Writes the profile's `figures:` block to `journal/figure-style.yml`, the style file of `scieflow experiment figures` (see [Experiments CLI](../experiments/cli.md)). |
| `scieflow journal research <run> --refresh-of <slug>` | Re-research a journal with its name, ISSN-L and gateway URLs pre-filled. A new gate is still required. |

`journal research` also accepts `--url URL` (repeatable, at most ten; https only),
`--slug`, `--as-of YYYY-MM-DD` (the reference date; default today, UTC),
`--restart` (remove the working files first; never touches an adopted profile)
and `--json`. `journal prompt` accepts `--for AGENT`, `--cross-check` and
`--write` (without `--write` it prints the prompt). `journal adopt` accepts
`--draft PATH`, `--replace`, `--as-agent` and `--json`.

## Switching on guideline downloads

`journal-guidelines` is **off by default**. Turn it on in the project's
`config/integrations.yml` (the example file lists it as a commented entry):

```yaml
providers:
  journal-guidelines:
    enabled: true
```

It takes no `hosts:` key: each run's hosts come from that run's human-approved
`journal-sources` gate. A request to any other host is refused before anything
is sent. Redirects are failures (the message tells you to re-run with the final
URL), and ScieFlow does not crawl. Pages are fetched only over https on port
443, with no user name or password in the address and no IP literal or local
host name.

## Facts and norms

`journal/facts.json` records what OpenAlex and Crossref say about the journal:
the source identifiers, the publisher, open-access status, the DOAJ flag,
article processing charges, the works count, and the norms from a sample of up
to 200 recent articles (median and quartiles of references, abstract words and
title words). Where OpenAlex and Crossref disagree about the publisher or title,
both values are recorded as a conflict; code never picks one.

The norms are **information only**. They are shown in the profile and in the
check report, and no finding is ever raised from them (M-F-D13 and the plan's
out-of-scope list). A journal's limits come from its guidelines, not from the
sample.

## The profile format

A profile is `config/journals/<slug>.yml` with `version: 1`. The schema file
`schemas/journal-profile.yml` is the single source of the key lists. Abbreviated:

```yaml
identity:   {name, slug, issn_l, issns, publisher, homepage_url, openalex_id, crossref_title}   # facts
oa:         {is_oa, is_in_doaj, apc_usd}                                                     # facts
norms:      {sample, references, abstract_words, title_words}                                # facts
scope:      {text}
article_types:                    # at least one
  - id: research-article
    label: Research article
    limits: {words_main, words_abstract, words_title, keywords_min, keywords_max, figures_max,
             tables_max, display_items_max, references_min, references_max, highlights_max,
             highlight_chars_max, pages_max}              # each optional, integer >= 0
    abstract:  {structured, headings}
    structure: {order: strict|any, sections: [{title, aliases, required, words_max}]}
references: {style_name, bibtex_style, natbib, numbering}
templates:  {latex_class, latex_class_name, latex_options, template_url, word_first}
submission: {system, required_files, line_numbers, blinding}
declarations: {required: [coi, funding, ethics, data-availability, code-availability, ai-use, credit, orcid]}
reporting_guidelines: {by_study_type, other}
figures:    {single_column_mm, double_column_mm, font_pt, dpi, formats}      # optional
review:     {type, criteria}
evidence:   [{field, source, quote}]
conflicts:  [{field, chosen, reason, statements: [{source, quote}]}]
provenance: {run, facts_sha256, sources, max_age_days, adopted_at, adopted_by, tool, format}
```

Field paths in messages are dotted keys with list indexes, for example
`article_types[0].limits.words_abstract`.

## Validation rules

Each problem is reported as a rule id, a path and a message. Errors stop
adoption; warnings do not.

| Rule | Severity | When |
| --- | --- | --- |
| `unknown-key`, `missing-key`, `type`, `enum`, `range` | error | The schema's keys, types, enums and integer ranges. `max_age_days` is 1 to 3650; `figures` has exactly the keys and ranges of `schemas/figure-style.yml`. |
| `article-type` | error | No article type, a duplicate `id`, or an id that does not match `^[a-z0-9][a-z0-9-]{0,63}$`. |
| `evidence-missing` | error | A non-null value in a sourced block has no evidence entry. Numbers and booleans need one for their exact path; strings and lists need one for their path or their parent mapping's path. |
| `evidence-source` | error | An evidence or conflict source id is not an `ok` row of the run's `sources.json`, or the text's sha256 differs from its row. |
| `evidence-quote` | error | The quote, whitespace-collapsed, is not in the stored source text, or it is shorter than 20 characters and not a whole line of the source. |
| `evidence-number` | error | An integer value does not appear in its quote as a number (`3000`, `3,000`, `3 000`; the words zero to twenty count). |
| `evidence-name` | error | A reporting guideline's name (CONSORT, STROBE, STARD, TRIPOD, PRISMA, SPIRIT, ARRIVE, CHEERS) or a LaTeX class name is not in a quote for its path. |
| `guideline-unknown` | error | A guideline id is not in `config/reporting/index.yml`. |
| `study-type-unknown` | error | A `by_study_type` key is not a study type of `schemas/design.yml`. |
| `conflict` | error | A conflict whose `chosen` value differs from the field, or that has fewer than two statements. |
| `facts` | warning (adopt) | Adoption replaces the facts blocks from `facts.json`; a draft that differed gets `facts-overridden` for each block. |
| `stale` | warning | The oldest `retrieved_at` is older than `max_age_days`. |
| `sources-unavailable` | warning | `validate` of a project profile whose source run is gone. Quotes are not re-checked. |

A **verbatim quote** must be a part of a stored source after whitespace and
Unicode normalisation (at least 20 characters, or a whole line). A value
without a quote is not evidence, whatever the draft says. A conflict is never
resolved by code: the chosen value and the reason are recorded for a human to
read.

Profile **validity** is not **journal fit**. A valid profile records what the
guidelines say; `manuscript check` (see the manuscript page) then tests a
manuscript against it.

## Freshness, refresh and diff

A profile is stale when its oldest source is older than `max_age_days` (default
90). `scieflow journal list` shows staleness; `manuscript check` reports it as
`profile-stale`. Refresh with `journal research --refresh-of <slug>`, then
`journal diff` and `journal adopt --replace`.

## Data protection

- Only the journal's name or ISSN (and the OpenAlex source identifier) is sent to
  OpenAlex and Crossref, through the existing leak-checked search providers.
  Nothing from a manuscript, a design or run data leaves the machine.
- Guideline pages are downloaded only from the addresses a human approved, and
  only with `journal-guidelines` switched on.
- A run at data level L2 refuses both unless the provider is listed in its
  `l2_providers`. Do journal research in a separate L0 run; the resulting profile
  is project-wide and every later manuscript check reads it locally.
- Messages and events carry rules, paths, ids, hosts and counts. Page text
  appears only as a 60-character excerpt.

## Known limits

- **R1. Live response shapes are untested.** OpenAlex and Crossref fields are
  exercised on synthetic fixtures only. A changed field leaves a fact null with
  its status; it is never guessed.
- **R2. Files are the integrity boundary.** A run's gate, sources and profile are
  plain files that the run's agents can write. An agent that forges a coherent
  set is not caught here; `adopt` and `add-source` refuse inside a dispatch, but
  host-side signing is a follow-up.
- **R3. Publisher pages often refuse scripts** (HTTP 403 to direct retrieval).
  Save the page yourself and use `add-source`. ScieFlow does not try to get
  around a refusal.
- **R4. Word counts are ScieFlow's rule**, not a journal's (see the manuscript
  page). Borderline counts deserve a human look.
- **R5. Checklists are unverified skeletons** until a human verifies them (see the
  manuscript page).
- **R6. DNS-based SSRF.** An approved host name that resolves to a private
  address is not detected. The human approves the URL; only https on port 443 is
  allowed.
- **R7. TeX can read files the user can read.** This applies to the manuscript
  build (see the manuscript page), not to journal research.
- **R8. Classes not installed here.** `elsarticle`, `IEEEtran` and `svjour3` are
  not installed on the development host; only `article` is compiled in tests.
- **R9. Cover letters and declarations are starters.** The content is the
  authors'. Every placeholder left is listed in the package.

Also not done (stated, not hidden): no DOCX or pandoc path (see the manuscript
page, M-F-D1 and D20); no DOAJ API and no Europe PMC structure sample (M-F-D4);
no PDF text extraction; no check against the journal's norms; no web page or
menu item (M-F-D14; the CLI is complete).
