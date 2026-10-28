# M-C1 literature kernel I — design (Part A of docs/superpowers/plans/2026-10-27-m-c1-literature-kernel.md)

## Part A — Spec

### 5.1 Vendored code

Location `src/scieflow/_vendor/researcher_core/` (package `scieflow._vendor.researcher_core`).

| File | Content | Upstream |
|---|---|---|
| `__init__.py` | ScieFlow-authored, a docstring only ("vendored from researcher v1.0.0, MIT; see VENDORED.md"); no imports | (upstream `__init__` is not vendored: it imports the httpx stack) |
| `model.py` | verbatim | `core/researcher_core/model.py` |
| `dedupe.py` | verbatim + one patch: `from rapidfuzz import fuzz` → `from ._fuzz import fuzz` | `core/researcher_core/dedupe.py` |
| `rank.py` | verbatim | `core/researcher_core/rank.py` |
| `sanitize.py` | verbatim | `core/researcher_core/sanitize.py` |
| `query.py` | ScieFlow header (a docstring naming the excerpt, `import re`, two blank lines) + verbatim excerpt of `connectors/base.py` lines 86–203 | excerpt |
| `_fuzz.py` | ScieFlow-authored `fuzz.token_sort_ratio(s1, s2)`: sort whitespace-split tokens, join with one space; score = `(1 - (len_a + len_b - 2·LCS)/(len_a + len_b)) · 100`, `100.0` for two empty strings; LCS by the bit-parallel algorithm below. Any keyword argument (`processor`, `score_cutoff`) → `TypeError` | — |
| `LICENSE` | verbatim | `LICENSE` |
| `UPSTREAM.json` | `{project, version: "1.0.0", snapshot: "e4ddbf4 (gap-eval snapshot)", url: "https://github.com/sokolmarek/researcher", license: "MIT", files: {name: {upstream, upstream_sha256, sha256, patches: [{old, new}], lines?: [86, 203], header?}}}` | — |

LCS (bit-parallel, Hyyrö; planning probe: bit-identical to rapidfuzz on 20,300 pairs):

```
masks[ch] |= 1 << i for each position i of ch in b;  full = (1 << len(b)) - 1;  v = full
for ch in a:  u = v & masks.get(ch, 0);  v = ((v + u) | (v - u)) & full
LCS = len(b) - popcount(v)            (0 when a or b is empty)
```

`CREDITS.md` (repo root): researcher by its authors, MIT, what was copied and why, link to
`VENDORED.md`. `VENDORED.md`: one table row per vendored file (upstream path, upstream sha256, local
sha256, patch), the pin (v1.0.0, snapshot e4ddbf4), the update procedure (copy, re-pin, re-run
`tests/vendor`), and the rule "never edit a vendored file except through a recorded patch".

### 5.2 Record adapter and fan-out (`scieflow research search all`)

```
scieflow research search all "<query>" [--sources openalex,crossref,europepmc,arxiv]
        [--limit N] [--from-year Y] [--top N] [--workspace SLUG] [--network MODE]
```

- The query is repaired (5.3) and checked once (`registry.check_query`); a refusal exits 3 before
  any source is asked.
- Each source module exposes `search(gw, checked, *, limit, from_year) -> list[dict]` (the body of
  today's `_search` minus the query check); `_search(gw, args)` calls it, so single-source
  behaviour is unchanged.
- Sources run sequentially in the canonical order `openalex, crossref, europepmc, arxiv` (whatever
  order `--sources` lists), each through the same Gateway. Per-source isolation: `ProviderRefused`
  / `PolicyError` → status `refused`; a catalogue search source that is off (disabled in
  `config/integrations.yml`, or off by default and not listed `enabled: true`) → `off`;
  `HTTPFailure` → `failed` with its classification; `NetworkMiss` (5.6) → `offline-miss`; any other
  `Exception` → `failed` (`ERROR`). `KeyboardInterrupt`/`SystemExit` are not caught.
- Adapter (`research/lib/kernel.py`): `to_csl(record, source, rank)` → `CSLRecord(id=f"{source}:{rank}",
  DOI, title, issued year, container_title=venue, abstract, URL, citation_count=cited_by,
  source, source_id=url or doi, extra={"source_ranks": {source: rank}})`; `from_csl(merged,
  originals)` → a `papers.record` with the merged core fields and `signals` = the union of the
  originals' namespaced signals plus `signals.kernel = {sources, ids, score, rank}`.
- `dedupe` (vendored) over all records in canonical order, then `rank_records` (vendored), then
  `--top` if given. Both run on the raw records.
- Output (one JSON object, sanitised at print, 5.9):
  `{"kernel": 1, "sources": [{"source", "status", "count", "error"?}], "records": [...],
  "dedup": [DedupDecision.to_json_dict()]}`. No field repeats the query.
- Exit: 0 when at least one source answered `ok`; otherwise 6 when every non-ok source is
  `offline-miss`; otherwise 3 when any is `refused`/`off`; otherwise 1.
- Determinism: same snapshots → byte-identical stdout (vendored `rank` never reads the clock; ties
  break on id).

### 5.3 Query repair

`registry.repair_query(q)`: C0/C1 controls and DEL → space, backslash → space, then the vendored
`_drop_unbalanced_brackets` and `_drop_unpaired_quote`, whitespace collapsed; uppercase
`AND/OR/NOT` kept (M-C1-D3); a result with no letter or digit → empty → `LeakError("refused:
nothing left that can be sent")` (exit 3). `check_query` = repair → existing leak check on the
repaired text → `Checked(repaired)`. A clean query is unchanged byte for byte.

### 5.4 Network modes

`core/network.py`. Modes `live`, `record`, `replay`, `offline`; `EGRESS = {live, record}`.

`workspace/<run>/network.yml`:

```yaml
version: 1
mode: replay          # live | record | replay | offline
set_by: human         # human | agent
set_at: "2026-10-27T15:00:00Z"
reason: "reproduce the round-1 searches"
```

Unknown keys, a wrong type, an unknown mode, unreadable YAML or a symbolic link → invalid →
effective mode `offline` with reason `network-invalid` (fail closed).

`resolve(ws, service, *, flag=None) -> Resolved(mode, origin, on_miss)`:

| `network.yml` | run level | service in `l2_providers` | mode | origin | on a miss |
|---|---|---|---|---|---|
| present, valid | any | any | its mode | `file` | `NetworkMiss` (exit 6) |
| present, invalid | any | any | `offline` | `invalid` | `NetworkMiss` (exit 6) |
| absent | below L2 | — | `live` | `default` | — |
| absent | L2 | yes | `live` | `l2-default` | — |
| absent | L2 | no (or policy invalid) | `replay` | `l2-default` | M-B refusal (`ProviderRefused`, exit 3, M-B message) |

In `live`/`record` the M-B data check still runs before every live request (an L2 run reaches only
`l2_providers`). `flag` (the `--network` option of a search command): may switch an egress mode to
any mode and `live` ↔ `record`; switching a no-egress mode (`replay`, `offline`, or `l2-default`
replay) to `live`/`record` → `ProviderRefused("network: run <slug> is <mode>; a command option
cannot loosen it")`. `record`, `replay` and `offline` need a run (scoped, or the dispatch's run):
an unscoped search outside a dispatch with such a flag → exit 1 ("needs --workspace").

CLI (`scieflow research network`):

```
scieflow research network <slug> [--json]                         # show mode, origin, set_by, set_at, reason, snapshot count
scieflow research network <slug> MODE [--reason TEXT] [--as-agent] [--json]
scieflow research network <slug> --clear --reason TEXT            # human only: delete network.yml
```

Human: any mode; moving from a no-egress mode to an egress mode, or `--clear`, needs `--reason`.
`--as-agent`: only `replay` or `offline`, else exit 1, file unchanged. Every write refuses inside a
dispatch (`refuse_in_dispatch`, exit 1), writes atomically and emits `network.set` (`mode`,
`previous`, `actor`, `reason`).

### 5.5 Snapshot store (`research/lib/snapshots.py`)

Path: `workspace/<run>/snapshots/literature/<provider>/<key>.json`, `key` = the Gateway's cache key
(`scite.cache.request_key(provider, op, {"url": url, **normalised params})`, 64 hex).

Record (researcher's D15 fields plus bookkeeping):

```json
{"schema_version": 1, "source": "search-openalex", "endpoint": "search",
 "url": "https://api.openalex.org/works", "request_params": {"search": "…", "per-page": 25},
 "request_key": "<64 hex>", "body_kind": "json", "response_body": {…},
 "response_hash": "<sha256 of canonical_json(response_body)>", "results": 25,
 "retrieved_at": "2026-10-27T15:00:00Z"}
```

`canonical_json`/`sha256_hex` from the vendored `model.py`; `body_kind: "text"` stores the arXiv
Atom string. Writes: temp file + `os.replace`, never through a symbolic link. Reads (`load`): the
file, its parent folders up to the run and `snapshots/` itself must not be symbolic links; JSON with
exactly these keys; `request_key` equals the requested key and the file name; `source`, `endpoint`,
`url`, `request_params` equal the request; `response_hash` matches → else `SnapshotCorrupt`
(a `ProviderRefused`, exit 3, message names the relative path, never the body). A missing file →
`None`.

CLI (`scieflow research snapshots`): `list <slug> [--json]` (count and bytes per provider);
`verify <slug> [--json]` (exit 1 naming each bad file); `copy <from> <to> [--json]` — refuses inside
a dispatch (exit 1) and from an L2 run (exit 3, "data protection: run <from> is L2; its snapshots
hold its queries"), verifies every file first and copies nothing if any is corrupt or an existing
target differs (exit 1); identical existing targets are skipped; emits one `literature.snapshot`
(`action: copied`, `from`, `count`) in the target run.

### 5.6 Gateway request path per mode

`Gateway(ws, policy, …, network=None)` and `Gateway.unscoped(policy, …, network=None)`; `network` is
the command's `--network` flag. `check()` keeps its signature and behaviour (M-B tests call it);
internally it becomes `_data_check` + `_policy_check`.

```
mode = network.resolve(run, provider, flag)          # run from _data_scope(); no run → live
_policy_check(provider, url)                          # enabled, https, host — every mode
params = _params(...)                                 # leak check and classification — every mode
key = request_key(...)
replay : s = snapshots.load(run, …) ; hit → ledger(cached=True), validate, return body
         miss → on_miss (NetworkMiss, or the M-B ProviderRefused for l2-default)
offline: snapshot, else cache hit, else NetworkMiss
live   : _data_check(provider) ; today's path (cache → transport → ledger → cache put)
record : _data_check(provider) ; transport (no cache read) → ledger → cache put
         → snapshots.write(run, …) → event literature.snapshot (action: recorded, provider, op, key, response_hash)
```

`NetworkMiss(PolicyError)` carries `provider`, `op`, `key[:12]`, the mode and the run; message
"offline-miss: <provider> <op> (key <12 hex>): run <slug> is <mode>; nothing was sent". No ledger
row and no `integration.call` for a miss. A replay hit is ledgered like a cache hit and emits
nothing (as today's cache hits). Single-source commands: `NetworkMiss` → exit 6.

### 5.7 Services without snapshots

One helper `network.require_live(ws, service)`: raises `NetworkRefused(PolicyError)` ("network: run
<slug> is <mode>; <service> cannot be replayed and is refused") when `resolve(ws, service).origin
in ("file", "invalid")` and the mode is `replay` or `offline`. Called first in
`scite/gateway.request` (before cache and transport), first in `evidence.execute` (before any
source; covers build and backfill), and in `nblm.py` next to the M-B check (`:302-307`). The
`l2-default` case leaves these to M-B's rules, unchanged.

### 5.8 Sandbox protection

`core/data/dispatch.py` `decide()` adds to `protected` (same existence and no-symlink rules as
`data.yml`): the run's `network.yml` when it exists, and the run's `snapshots/` directory when the
resolved mode of the run (service-independent: file mode, or `l2-default`) is `replay` or
`offline`. The sandbox binds them read-only; `verify()` proves it (M-B mechanism, unchanged). In
`live`/`record` `snapshots/` stays writable so a dispatch can record.

### 5.9 Inbound sanitiser

`core/sanitize.py` re-exports `sanitize_string`, `sanitize_json_strings`, `fence_untrusted`,
`strip_ansi`, `strip_dangerous_controls`, `contains_injection` from the vendored module, plus
`fence_text(content)` = `strip_dangerous_controls(strip_ansi(content), keep_newlines=True)`.

- `papers.emit(records, *, sanitize=True)` prints `sanitize_json_strings(records)`; `registry.run`
  and the fan-out print through it. Records are sanitised only on output; dedupe, rank, snapshots,
  cache and ledger see raw values.
- `prompts._fenced(label, content, untrusted=False)`: with `untrusted=True` the content passes
  `fence_text` first (words kept, ANSI/bidi/zero-width/controls removed; tabs become spaces).
  `passes._fenced` passes it through; only the evidence bundle (`passes.py:427`) sets it.

### 5.10 No-export invariant (A-15) and the D17 contract

Tests pin: (1) `archive.build_zip` of a run never packs files under `scratch/` (incl.
`scratch/agent-home/**/.cache/scieflow/scite/cache`), `.uv-cache/`, `.cache/`; (2) with
`skip_rebuildable=False`, `scratch/agent-home` is still not packed; (3) `fulltext.evidence` run with
`HOME` = the run's agent home writes its cache only under that home; (4) the Gateway is the only
writer of `snapshots/` (AST) and `research/lib/fulltext.py` does not import the Gateway or the
snapshot store, so full text never becomes a snapshot. Documented contract for M-C2: a PDF or
full-text download is refused for an L2 run and cached only under the run's `scratch/` (or the
user cache), never elsewhere in the run.

### 5.11 Events and exit codes

New event types: `network.set` (`mode`, `previous`, `actor`, `reason`) and `literature.snapshot`
(`action: recorded|copied`, `provider`, `op`, `key`, `response_hash` / `from`, `count`). Exit codes
of the search commands: 0 ok, 1 failed, 3 refused, 6 offline-miss. `research network|snapshots`:
0, 1 (refused write, invalid input, corrupt snapshot), 3 (data-protection refusal).

### 5.12 CLI and docs (D7)

New: `scieflow research search all`, `--network MODE` on all five search commands,
`scieflow research network`, `scieflow research snapshots list|verify|copy`. Docs: new
`docs/research/literature-kernel.md` (fan-out, dedupe/rank, query repair, network modes, snapshots,
L2 behaviour, sanitiser, no-export invariant, D17 contract, credits), a network-mode paragraph in
`docs/research/discovery-providers.md` and `docs/data-protection.md` (L2 default), rows `3` and `6`
for `scieflow research search` in `docs/cli.md`, one nav line, the lit-review skill update and
research rule 6 (section 3).

### 5.13 Threats and how M-C1 answers them

| Threat | Answer |
|---|---|
| Replay silently falls through to a live request | Mode decided before any transport; replay/offline have no transport branch; tests count zero session calls |
| Agent loosens the mode | `network.yml` read-only in dispatches; `--as-agent` only tightens; a command flag cannot loosen; writes refuse inside dispatches |
| Forged snapshot in a replaying run | `snapshots/` read-only in dispatches while replaying; key/hash/request checks on every read; residual for record mode (section 8) |
| L2 query text leaves through snapshots | `snapshots copy` refuses from L2 runs; M-B's live refusal unchanged; snapshots stay inside the run |
| Injection text in search output | Sanitised at print; verdicts (dedupe, rank) on raw records; injection twins test |
| Rendering attacks in quoted passages | Evidence-bundle fence strips ANSI/bidi/zero-width/controls |
| Vendored code drifts or is edited | `UPSTREAM.json` + integrity test (sha256, patch reversal, excerpt hash) |
| Similarity shim differs from rapidfuzz | 300 recorded rapidfuzz cases; dedup gold set (210 pairs) |
| Full text reaches an archive | No-export tests (5.10) |
| A service that cannot be replayed goes live in replay mode | `require_live` at scite, evidence, NotebookLM |

### 5.14 What M-C1 does not do

No new connector (PubMed, S2, OpenCitations, Unpaywall, DataCite: M-C2), no `verify-bib`
(M-C2), no PDF download (M-C2), no snowballing, no monitoring, no shared cross-process rate limiter
(M-C1-D4), no web page, no eval harness with Wilson intervals (M-H), no live smoke against real
indexes (every test is offline).

---

