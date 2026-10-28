# Discovery providers: PubTator3, ClinicalTrials.gov, OpenAlex, Europe PMC

Gap discovery can look beyond the papers it has read, through four free
public services. Core makes every request; sub-agents only read the results.

| Provider | Module | What it finds |
| --- | --- | --- |
| PubTator3 | `research/lib/pubtator.py` | **Open links**: A is linked to B, B to C, but no relation A–C has been extracted from PubMed |
| ClinicalTrials.gov v2 | `research/lib/ctgov.py` | **Completed, unpublished trials**: finished at least two years ago, no linked publication |
| OpenAlex | `research/lib/openalex_trends.py` | **Trend breaks** in a topic's share of the literature per year; **co-citation bridges** between two topics |
| OpenAlex + Europe PMC | `research/discovery/abstraction.py` | **Novelty pre-check** of every abstracted hypothesis (hit counts and top hits) |

## Setup (deny by default)

```bash
cp config/integrations.example.yml config/integrations.yml   # gitignored
```

Without `config/integrations.yml` the discovery providers are **off**; a provider
must be listed with `enabled: true`. The four literature search sources
(`search-openalex`, `search-arxiv`, `search-europepmc`, `search-crossref`, see
"Literature search" below) are **on** unless listed with `enabled: false`. Each provider has a built-in host list
(`www.ncbi.nlm.nih.gov`, `clinicaltrials.gov`, `api.openalex.org`,
`www.ebi.ac.uk`, `export.arxiv.org`, `api.crossref.org`); `hosts:` in
the file may only narrow it. A host outside the list is refused when the file
loads, and a request to any host not listed for that provider (or not https)
is refused before it is built. Unknown keys are refused. A refusal is a
boundary (exit code 3, like scite), never retried around.

## Literature search (`scieflow research search`)

`scieflow research search openalex|arxiv|europepmc|crossref "query"` goes through
the same gateway: policy, leak check, cache (7 days), rate limit, no redirects,
ledger. The output records are unchanged.

- The query is checked before anything is built (M-A-D2). A word-final `*` and a
  standalone single digit between two words are allowed (`parkinson*`,
  `type 2 diabetes`); the query is sent as typed. Still refused: data-like tokens
  (`patient 4711 age 63`, `cohort: RBD043`, paths, e-mail addresses), a digit run
  written with spaces (`patient 4 7 1 1`, `RBD 0 4 3`, refused as a number
  whatever the spacing), and `5-HT2A receptor`. Rephrase the query; the refusal
  names the kind of leak, never the query.
- arXiv and Europe PMC syntax (`all:`, the publication-year clause) is added by
  code after the check; `--from-year` is an integer year.
- `--workspace SLUG` records the request in `workspace/SLUG/scite/ledger.jsonl`
  and `events.jsonl`. Without it the request is recorded in the per-day ledger only.
- Exit codes: 0 success; 3 refused (leak check, policy, source switched off); 1
  failure (`HTTP_<status>`, `RATE`, `TRANSIENT`, `MALFORMED`).
- **Network mode.** Every search command takes `--network live|record|replay|offline`,
  and a run's own mode (`scieflow research network <slug>`) applies when the flag is
  omitted. `replay` and `offline` send nothing and exit `6` on a request with no
  recorded answer. A flag can tighten the mode, never loosen it. The run's mode applies to a search made
  with `--workspace SLUG` or from a dispatch; without either the search is unscoped (`live`),
  and a `--network` value other than `live` there exits `1`. The full rules, the
  `search all` fan-out and the snapshot store are in
  [Literature kernel](literature-kernel.md).

## What leaves the machine

Every query parameter is classified, deny by default:

- **free text** (search terms, conditions) must pass the review sanitiser's
  leak check: no numbers that look like data, no paths, no `label:` prefixes,
  no wildcards;
- **ids**: a PubTator entity id you pass (`@DISEASE_REM_Sleep_Behavior_Disorder`)
  must have the PubTator shape and its name must pass the same leak check —
  so `@DISEASE_cohort_a_n_43` is refused — unless PubTator itself returned that
  id to the run (autocomplete or relations); page tokens are shape-checked;
- everything else is a constant in ScieFlow's code.

Anything else raises `LeakError` and nothing is sent. Redirects are never
followed: a 3xx is a failure (`HTTP_3xx`), so no request reaches a host the
policy did not check.

## Cache, rate limit, ledger

Requests share scite's on-disk cache (`~/.cache/scieflow/scite/cache/`, TTLs
from `cache_ttl_days`) and its ledgers: each request is a row in the run's
`scite/ledger.jsonl` and the per-day ledger, with the provider name as
`source`, and emits `integration.call` (`integration: pubtator | ctgov |
openalex | europepmc`). Cache hits are ledgered as `cached` and cost nothing. Provider rows
never count against scite's MCP budgets. Each provider has its own rate limit
(`rate_per_s`; NCBI asks for at most 3 per second). Failures are classified
(`RATE`, `TRANSIENT`, `AUTH`, `MALFORMED`, `HTTP_<status>`), ledgered and never
cached — including a 200 whose body has the wrong shape. scite's status
(`last_error`) and a bundle's `calls.json` only look at scite's own rows.

## Detectors

- **Open links** (`pubtator.open_links`): A's complete relation list, the
  best-published neighbours as B (`max_b`), their relations, then every C that
  is neither A nor a known neighbour of A. Score = Σ over paths of
  min(A–B publications, B–C publications); ties by number of paths, then id.
  Relation types are merged (the best-published edge per pair). PubTator3
  extracts chemical/gene/variant–disease and gene/chemical relations, not
  disease–disease ones, so a disease C is never "known" to a disease A — read
  disease–disease open links with that in mind (or restrict C's type).
- **Unpublished trials** (`ctgov.unpublished`): completed trials whose registry
  record has no RESULT or DERIVED reference (BACKGROUND references cite older
  work and do not count), completed at least `grace_months` (24) whole months
  before the given date (a completion given as a month or year counts from its
  last day), oldest first. The search reports `complete: false` when
  `max_pages` stopped it before the registry's total. Results posted on the registry are reported, but
  they are not a paper. The registry's links are incomplete, so every
  candidate is a lead to verify.
- **Trend breaks** (`openalex_trends.trend`): the topic's yearly count divided
  by all of OpenAlex's, compared over a window (3 years) before and after each
  year; a ratio of 2 or more is a surge, 1/2 or less a drop. A run of
  consecutive flagged years is reported once. Pass the last *complete* year as
  `until_year`.
- **Bridges** (`openalex_trends.bridge_gap`): works referenced by at least two
  of the top-cited papers of both topics, next to the number of works matching
  both topics. Shared foundations with little direct overlap mark a gap. A
  paper top-cited in both topics (`shared_top`) is direct overlap, so its
  references are not counted as bridges.

All detectors are pure functions over fetched data and deterministic: the same
responses give the same candidates in the same order.

## From provider data to ranked hypotheses (`research/discovery/`)

- **Typed gap detectors** (`detectors.py`) turn fetched evidence into
  candidate gaps whose evidence items carry a verbatim `quote`, `source` and
  `detector` (additive fields of `gaps.schema.json`): `disputes` (scite
  `citation_graph` contrasting statements on seed DOIs), `open_questions`
  (sentences in scite `search_literature` hits such as "remains unclear",
  "further studies are needed"), `abc_links`, `unpublished_trials`, `bridges`,
  `trend_breaks` (the providers above) and `own_data` (the existing gap analysis
  of the user's data; its corpus stays local). Each detection also returns the
  corpus its quotes come from. Structured records (relations, counts) are
  quoted as a fixed rendering of the record core fetched.
- **Verification gate** (`detectors.verify`): an evidence item survives only if
  its quote (whitespace-normalised, case-folded, at least 12 characters) is a
  substring of a corpus text under the same ref; a gap with no surviving item
  is dropped. The gate is mechanical, so invented quotes — from a detector or
  from an agent fed one detector's evidence — fall out.
- **Tournament** (`tournament.py`, `hypothesis-tournament.schema.json`):
  seeded, capped, deterministic Elo (k 32, initial 1200). All pairs are
  shuffled by the seed and taken so every hypothesis plays as evenly as
  possible, up to `max_comparisons`; display order is randomised by the seed.
  Each match gets the least-loaded judge from the best available tier:
  `other-family` (a declared model family different from both authors'),
  `unknown-family`, `same-family`, and only as a last resort `author`. The
  tier is recorded per match. Missing or invalid verdicts change nothing and
  are counted. Elo ties are broken by debate status (consensus > unchallenged
  = undebated > disputed > challenged), then id; `top` holds the first
  `top_k` ids.
- **Abstraction** (`abstraction.py`): before any novelty search a hypothesis
  loses its framing ("We hypothesise that"), cohort descriptors ("in our
  cohort of 43 patients", "the Prague cohort", "cohort_a"), effect-size
  language ("large effect size", "odds ratio", "two-fold"; "doubles" becomes
  "increases"), number words, and everything the review sanitiser removes. A
  residual leak (first person, cohort, effect size, number words, data-shaped
  tokens) is refused, never sent. The abstracted text is what the gate shows
  and the only text that is sent.
- **Novelty**: every abstracted hypothesis gets a free pre-check (one OpenAlex
  and one Europe PMC search on its key terms; a provider that is off or
  failing is reported, the check goes on). Only the tournament's top-k go to
  scite: one OR-batched `search_literature` over each hypothesis'
  discriminative terms (hits matched back locally), then — only if a hit
  matched — one `citation_graph` over the matched hits with the highest
  contrasting share, for counter-evidence. At most 2 MCP calls, enforced in
  code on top of the `config/scite.yml` budget; a budget or quota stop marks
  the rows `quota`.
