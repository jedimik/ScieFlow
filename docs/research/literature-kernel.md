# Literature kernel

The literature kernel is the part of the research module that turns one query into
one merged, ranked list of papers, decides whether the network may be used for it,
and keeps every answer inside its run so the same search can be replayed later.
It is shipped as M-C1; M-C2 adds the identity check (`verify-bib`), two more search sources,
snowballing, saved searches and the open-access lookup, described in [M-C2 additions](#m-c2-additions).
Discovery providers that are not literature search engines
(PubTator3, ClinicalTrials.gov) are described in [Discovery providers](discovery-providers.md).

Everything in this page runs through the provider Gateway: the same policy, leak
check, cache, rate limit and ledger as every other literature request.

## Searching every source at once

```bash
uv run scieflow research search all "parkinson rapid eye movement sleep" --limit 25
```

`search all` asks every enabled source (OpenAlex, Crossref, Europe PMC and arXiv, in
that order) and prints one JSON object:

- the answers are merged with the vendored dedupe and ranked with the vendored rank
  (see [Credits](#credits));
- the output names each source's status and never repeats the query. The status is
  one of `ok` (answered), `refused`, `off` (switched off), `offline-miss` (a snapshot
  miss in a no-egress run) or `failed`;
- each source is isolated: a refusal or a failure ends that source only.

Options: `--sources LIST` (a comma-separated subset), `--limit N` (records asked of
each source), `--from-year YEAR`, `--top N`, `--workspace SLUG`, `--network MODE`. One source at a time is
still available as `scieflow research search openalex|arxiv|europepmc|crossref|pubmed|semantic-scholar|datacite`.
PubMed and Semantic Scholar are off until you switch them on (D16); `search all` asks them only
when they are on, and asks DataCite only when it is named in `--sources`.

`search all` exits `0` when any source answered; it exits `6` only when no source answered and every source asked is a snapshot miss, `3` when no source answered and some source was refused or switched off, and `1` otherwise.

Before any source is asked, the query is repaired and then checked once. Repair
removes control characters and backslashes, drops unbalanced brackets and an unpaired
quote, and collapses whitespace; a query that needs no repair is sent byte for byte as
before. The leak check then runs on the repaired text (see
[Discovery providers](discovery-providers.md#literature-search-scieflow-research-search)).

## Network modes

Every literature request of a run has one mode, decided before any transport is
chosen:

| Mode | Requests sent | Answers kept | On a request with no recorded answer |
|---|---|---|---|
| `live` | yes, after the data-protection check | no snapshot | the request is made |
| `record` | yes, after the data-protection check | yes, as snapshots in the run | the request is made, and its answer is recorded |
| `replay` | no | read only | exit `6`: nothing was sent |
| `offline` | no | read only, then the Gateway cache | exit `6`: nothing was sent |

`live` and `record` are the egress modes; `replay` and `offline` never open a
connection. The mode applies to a request made for a run: with `--workspace SLUG`, or from inside a dispatch, where the run is the dispatched one. Without either, the search is unscoped: it is `live`, and `--network replay`, `--network offline` or `--network record` there exits `1`.

Where the mode comes from, in order:

1. `workspace/<run>/network.yml`, when it is present and valid. An invalid file (an
   unknown key, a wrong type, unreadable YAML, a symbolic link) fails closed to
   `offline`.
2. Otherwise the run's data level decides (see [Data protection](../data-protection.md)).
   An L2 run reaches only the services listed in its `l2_providers`; a listed service
   is `live`, and an unlisted one is `replay`, so its miss is the data-protection
   refusal (exit `3`). Its origin is `l2-default`. A run below L2 with no file is `live`.

A command flag `--network MODE` may tighten a mode, or move between `live` and
`record`. It can never loosen a no-egress mode: switching a `replay` or `offline` run
to an egress mode is refused.

```bash
uv run scieflow research network <slug>                       # show mode, origin, set_by, snapshot count
uv run scieflow research network <slug> replay --reason "reproduce round 1"
uv run scieflow research network <slug> record --reason "first pass, online"
uv run scieflow research network <slug> --clear --reason "back to the data-level default"
```

A human may set any mode. Moving from a no-egress mode to an egress mode, or clearing
the file, needs `--reason`. An agent (`--as-agent`) may only set `replay` or `offline`,
and a write refuses inside a dispatch. Every write is atomic and emits a `network.set`
event (`mode`, `previous`, `actor`, `reason`).

## Snapshots

In `record` mode every answer is written to:

```text
workspace/<run>/snapshots/literature/<provider>/<key>.json
```

`<key>` is the Gateway's cache key (64 lowercase hex). A snapshot holds the endpoint,
the request parameters, the response body, the body's hash, the result count and the
retrieval time. Only the Gateway writes snapshots.

```bash
uv run scieflow research snapshots list <slug> [--json]
uv run scieflow research snapshots verify <slug> [--json]    # shape, file name, provider and body hash of every file
uv run scieflow research snapshots copy <source> <target> [--json]
```

`verify` reports a corrupt snapshot by its relative path, never its body. A corrupt or forged snapshot read by a `replay` or `offline` run is a refusal (exit `3`): nothing is sent, and `snapshots verify` finds it beforehand. `copy`
moves recorded answers between runs and is refused from an L2 run (exit `3`), because
the snapshots of an L2 run hold its queries. In a dispatch the run's `network.yml`, when it exists, is read-only to the agent. In `replay`
and `offline` runs the `snapshots/` folder is read-only too, once it exists (see [Limits](#limits)); a dispatch in an egress mode may
still write to it.

## Inbound sanitiser

Search output and the evidence bundle's quoted passages are untrusted text. The
vendored sanitiser (`scieflow.core.sanitize`) is applied only where a value leaves the
process as output: search printouts (`papers.emit`) and the fence around quoted
passages in review prompts, which also removes ANSI sequences, bidirectional and
zero-width characters and other controls. Dedupe, rank, snapshots, caches and the
ledger always see the raw values, so the sanitiser never changes a verdict.

## No-export invariant and the D17 contract

Literature caches and snapshots stay inside the run or the user cache, and full text
never becomes a snapshot. The archive builder never packs `scratch/`, `.uv-cache/` or
`.cache/` into a run archive, and `fulltext.evidence` writes its cache only under the
run's agent home. Tests pin these rules (no-export tests, `tests/test_noexport.py`).

For M-C2 the contract (D17) is: a PDF or full-text download is refused for an L2 run,
and is cached only under the run's `scratch/` or the user cache, never elsewhere in the
run.

## M-C2 additions

M-C2 adds the identity check, two search sources, snowballing, saved searches, a news
hook and the open-access lookup. Every request still goes through the Gateway, the run's
network mode and the data-protection check.

### Identity check (verify-bib)

```bash
uv run scieflow research verify-bib <slug> [--bib PATH] [--sources openalex,crossref,datacite] [--round N] [--network MODE] [--json]
```

`verify-bib` reads `manuscript/references.bib` (or `--bib PATH`, which must stay inside the
run) and checks each entry against the identity sources: OpenAlex, Crossref and DataCite by
default. Each entry gets one of four verdicts:

- `verified`: at least two sources agree on the work;
- `mismatch`: the DOI belongs to a different paper, or the entry is mangled;
- `unresolvable`: no source knows the reference; it looks invented;
- `inconclusive`: the evidence is too thin or too dirty to decide (a rate limit, a source
  error, or a title the leak check refused).

Only `mismatch` and `unresolvable` are refusal-grade. A title is sent only if it passes the
leak check, and a source that could not answer never produces a negative. The check needs at
least two usable sources (otherwise it sends nothing and exits `3`) and at most 500 entries
(otherwise it exits `1`).

With `--round N` (coordinator only; refused inside a dispatch) the report is written to
`review/round-N/identity.json`, and one `review.identity_checked` event lists the
refusal-grade references. Each one opens a human-only `review-blocker` gate. Until the
reference is fixed and checked again, or a human overrules its gate with a reason, merge-check
exits `1`:

```bash
uv run scieflow gate answer <slug> <gate-id> overrule --note "why the risk is accepted"
uv run scieflow gate answer <slug> <gate-id> fixed
```

Without `--round` nothing is written and no gate opens, so an agent can check its own draft.

### New search sources and the D16 defaults

- `scieflow research search pubmed "<query>"`: PubMed field tags (`[tiab]`, `[ti]`, `[ab]`,
  `[mh]`, `[mesh]`, `[majr]`, `[tw]`, `[pt]`, `[la]`, `[sb]`, `[au]`) are allowed. The leak
  check ignores them and the query is sent as typed; any other bracket is refused. A year
  limit goes through `--from-year`, which the code composes.
- `scieflow research search semantic-scholar "<query>"`: keyless. No API key is read or sent.
- `scieflow research search datacite "<query>"`: datasets and software.
- PubMed and Semantic Scholar are off by default (D16). They run only when
  `config/integrations.yml` sets `enabled: true` for `search-pubmed` or
  `search-semantic-scholar`; until then a request exits `3` with "is off" and sends nothing.
  `search all` asks them only when they are on, and asks DataCite only when it is named in
  `--sources`. A default `search all` gives the same four statuses and exit codes as in M-C1.

### Snowballing

```bash
uv run scieflow research snowball --seed <DOI> [--seed <DOI> ...] [--direction forward|backward] [--depth 1|2] [--limit N] [--sources LIST] [--workspace SLUG] [--network MODE]
```

`forward` lists the papers that cite the seeds; `backward` lists the papers they cite. It
takes one to ten seeds, each a plain DOI (a malformed one exits `1` before any request), and
one or two hops. `--limit` (1 to 100, default 25) caps the neighbours asked of each source per
expanded node. Each node is expanded once, before it can re-enter the frontier, so citation
loops end. Sources are OpenAlex, Semantic Scholar and OpenCitations in that order; Semantic
Scholar takes part only when it is switched on (D16). Seeds are never reported as discoveries.
Source failures are isolated, and the exit codes follow the `search all` rule.

### Saved searches (monitor) and the news hook

```bash
uv run scieflow research monitor add <slug> <name> --query "<query>" [--query "<query>" ...] [--source all|<one source>]
uv run scieflow research monitor run <slug> <name> [--ts RFC3339] [--network MODE]
uv run scieflow research monitor list <slug> [--json]
uv run scieflow research monitor show <slug> <name> [--json]
```

`monitor run` re-runs each saved query and reports only the papers the monitor has not seen
before. Its state is kept in `workspace/<slug>/monitors/<name>.json`, and the new records of
the last run in `<name>.last.json`. The state moves forward only after a complete answer: a
snapshot miss or a failure leaves it unchanged. A live re-run asks the sources again; an
offline or replay run reads the cache or the snapshots. `add` refuses a name that already
exists. `--ts` makes a run deterministic, because the vendored monitor never reads the clock.

A news interest with a `literature:` block re-runs a monitor instead of calling an agent
(see [News](../news/index.md#literature-interests-m-c2)).

### Open-access lookup and the contact address

`scieflow research oa DOI [DOI ...]` (one to fifty DOIs) asks Unpaywall whether a free full
text exists, and where. Unpaywall requires an e-mail address with every request, so the
`unpaywall` provider stays off until you put your own address in `config/integrations.yml`
as `contact_email` and set `enabled: true`. Without both, `oa` exits `3` and names the reason.

ScieFlow never ships an address. The address goes on the wire only, and it is never written
to a ledger, cache entry, snapshot, event or printed output. `oa` reports a verdict
(`full-text`, `abstract-only` or `unavailable`) and the open-access link. It never requests
that link.

### Gold set: what is measured, and how to record your own

The identity check is measured on researcher's recorded gold set of 150 references: 75 real
papers, 25 real datasets, 25 references with a wrong DOI and 25 invented ones. M-C2 replays
researcher's recorded answers offline and reproduces its verdict on every item. On the 100
real references, 0 of 100 are refused (the false-positive target). Of the 100 wrong or invented
references, 50 of 50 are caught. Of the real ones, 71 are `verified` and 29 are `inconclusive`:
the check could not confirm them, but it refused none. The set is mostly cardiology and AI
references and datasets. It is not a neurology set. A neurology set needs a live recording,
which is a separate step that needs human approval. To record your own answers, run
`scieflow research verify-bib <slug> --network record`; `--network replay` then checks them offline.

### What M-C2 does not do

M-C2 never downloads a PDF or full text (a link found by `oa` is reported, never fetched). It adds
no PubMed or Semantic Scholar identity source and no API key for any source. It has no neurology
gold set yet. The new commands are CLI commands only, with no web page. Identity verdicts are
not shown inside the evidence bundle. The review page's single matrix link goes to the first
blocker's round, so when only identity blockers are open it opens a page without a matrix; a
per-blocker link is a follow-up.

## Exit codes

| Code | Meaning | Where |
|---|---|---|
| `0` | a source answered (`search all`: at least one source), or the network or snapshot command succeeded | `research search`, `search all`, `research network`, `research snapshots` |
| `1` | a failure: a source failed, a refused write, an invalid input (such as `--network replay` with no run), or a corrupt snapshot read by a `snapshots` command | `research search`, `search all`, `research network`, `research snapshots` |
| `3` | a refusal: a leak-check refusal, a switched-off source, a corrupt or forged snapshot read by a `replay` or `offline` run, or a data-protection refusal (L2 run, unlisted service, copy from L2) | `research search`, `search all`, `research snapshots copy` |
| `6` | a no-egress run has no recorded answer for the request; nothing was sent | `research search`, `search all` (`replay`, `offline`) |
| `4` | `verify-bib` checked the bibliography and at least one reference is refusal-grade (`mismatch` or `unresolvable`); the report is still written, and `inconclusive` never gives `4` | `research verify-bib` (see [Identity check](#identity-check-verify-bib)) |

## Limits

- The M-C1 kernel has no connector for PubMed, Semantic Scholar, OpenCitations, Unpaywall or
  DataCite. M-C2 added them (see [M-C2 additions](#m-c2-additions)). No PDF download exists in
  either milestone: a link found by `oa` is reported, never fetched.
- No shared cross-process rate limiter (M-C1-D4). Each process rate-limits itself.
- Every test is offline: all transports are faked, and no live search against a real
  index has been run as part of this work.
- A forged snapshot in a `record` run is not caught by the mode check; `verify`
  checks the file's shape and hashes, not its origin.
- A dispatch can create a `snapshots/` folder that does not exist yet. That happens in a
  run that replays without one (an L2 run with no `network.yml`, or a hand-written
  `network.yml`); a snapshot planted there would then be served by replay. Running
  `scieflow research network <slug> replay` or `offline` creates the folder first, so a
  run set that way is protected.

## Credits

The dedupe, rank, inbound sanitiser and query-repair modules are vendored from
**researcher** (MIT License), version 1.0.0. Their licence and provenance are in the
repository's `CREDITS.md` and `VENDORED.md`; the pinned hashes and patches are in
`src/scieflow/_vendor/researcher_core/UPSTREAM.json`.
