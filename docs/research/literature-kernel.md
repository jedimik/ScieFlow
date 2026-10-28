# Literature kernel

The literature kernel is the part of the research module that turns one query into
one merged, ranked list of papers, decides whether the network may be used for it,
and keeps every answer inside its run so the same search can be replayed later.
It is shipped as M-C1. Discovery providers that are not literature search engines
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
still available as `scieflow research search openalex|arxiv|europepmc|crossref`.

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
uv run scieflow research snapshots verify <slug> [--json]    # shape, key, request and body hash of every file
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

## Exit codes

| Code | Meaning | Where |
|---|---|---|
| `0` | a source answered (`search all`: at least one source), or the network or snapshot command succeeded | `research search`, `search all`, `research network`, `research snapshots` |
| `1` | a failure: a source failed, a refused write, an invalid input (such as `--network replay` with no run), or a corrupt snapshot read by a `snapshots` command | `research search`, `search all`, `research network`, `research snapshots` |
| `3` | a refusal: a leak-check refusal, a switched-off source, a corrupt or forged snapshot read by a `replay` or `offline` run, or a data-protection refusal (L2 run, unlisted service, copy from L2) | `research search`, `search all`, `research snapshots copy` |
| `6` | a no-egress run has no recorded answer for the request; nothing was sent | `research search`, `search all` (`replay`, `offline`) |

## Limits

- No new connector: PubMed, Semantic Scholar, OpenCitations, Unpaywall and DataCite
  arrive in M-C2. No `verify-bib` and no PDF download are part of this kernel.
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
