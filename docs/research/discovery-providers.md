# Discovery providers: PubTator3, ClinicalTrials.gov, OpenAlex

Gap discovery can look beyond the papers it has read, through three free
public services. Core makes every request; sub-agents only read the results.

| Provider | Module | What it finds |
| --- | --- | --- |
| PubTator3 | `research/lib/pubtator.py` | **Open links**: A is linked to B, B to C, but no relation A–C has been extracted from PubMed |
| ClinicalTrials.gov v2 | `research/lib/ctgov.py` | **Completed, unpublished trials**: finished at least two years ago, no linked publication |
| OpenAlex | `research/lib/openalex_trends.py` | **Trend breaks** in a topic's share of the literature per year; **co-citation bridges** between two topics |

## Setup (deny by default)

```bash
cp config/integrations.example.yml config/integrations.yml   # gitignored
```

Without `config/integrations.yml` every provider is **off**. A provider must be
listed with `enabled: true`. Each provider has a built-in host list
(`www.ncbi.nlm.nih.gov`, `clinicaltrials.gov`, `api.openalex.org`); `hosts:` in
the file may only narrow it. A host outside the list is refused when the file
loads, and a request to any host not listed for that provider (or not https)
is refused before it is built. Unknown keys are refused. A refusal is a
boundary (exit code 3, like scite), never retried around.

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
openalex`). Cache hits are ledgered as `cached` and cost nothing. Provider rows
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
