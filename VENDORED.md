# Vendored code

This package vendors code from **researcher** v1.0.0 (MIT License; the licence text is
`src/scieflow/_vendor/researcher_core/LICENSE`). Credits: [CREDITS.md](CREDITS.md).

## Pin

- Upstream: https://github.com/sokolmarek/researcher (URL as recorded in the M-C1 plan; not re-checked against GitHub).
- Version: 1.0.0 (`core/pyproject.toml`, the top CHANGELOG entry 1.0.0, 2026-07-16).
- Snapshot: e4ddbf4 (gap-eval snapshot), the read-only gap-evaluation copy `src/researcher`. That the
  snapshot equals the `v1.0.0` tag was not re-checked against GitHub; this is an open residual.
- Machine-readable pin, patches and hashes: `src/scieflow/_vendor/researcher_core/UPSTREAM.json`.

## Vendored files

| File (in `researcher_core/`) | Upstream path | Upstream sha256 | Local sha256 | Patch |
|---|---|---|---|---|
| `model.py` | `core/researcher_core/model.py` | `a551a6ff8847404d2ae7a73de0aa0461b7d1e150bfa6ddadc5d436c10afca1a8` | `a551a6ff8847404d2ae7a73de0aa0461b7d1e150bfa6ddadc5d436c10afca1a8` | none |
| `rank.py` | `core/researcher_core/rank.py` | `4ad8dda1c02b1c0a9a96a9084663357f78dcdaa4708afc11800c0d349ba15df2` | `4ad8dda1c02b1c0a9a96a9084663357f78dcdaa4708afc11800c0d349ba15df2` | none |
| `sanitize.py` | `core/researcher_core/sanitize.py` | `89bd6f55090cdbfdfd771cfe32ab636823bfe29dd135579660fca3a25a12c723` | `89bd6f55090cdbfdfd771cfe32ab636823bfe29dd135579660fca3a25a12c723` | none |
| `dedupe.py` | `core/researcher_core/dedupe.py` | `c40ba675b28b3b502b060a4436035107b8c428f4d9106b6a0ee1f49d009820a5` | `b6af139ed6e4fe7b94b59a2cd87bb7c08f06406c0c082b9db630b5a8cedfba04` | `from rapidfuzz import fuzz` -> `from ._fuzz import fuzz` |
| `query.py` | `core/researcher_core/connectors/base.py` | `505374780dc07bf538fecd95243d7804dbf6e72893cd0bba59fce743301505e8` | `85928224f4e6cdcbf9fc5ebc00f16c16ed5ab9d8ea8d37436f3760807b565423` | excerpt of lines 86-203 (excerpt sha256 `592a41473d89a2288fa30f0b1f65f342937e69d334b553d8d8c6bfab833ff3dc`) with a ScieFlow header; no other change |
| `contributors.py` | `core/researcher_core/export.py` | `544f5cc75b1269bf0fdb59379d567894f80e27795d15716f6008b2a8137c5dd7` | `df2a388579eff06e6e5726a529c311f842af01803ff662fcd485464b66e74438` | excerpt of lines 809-1000 (excerpt sha256 `4b40e691af9a30d7995949c3c309ad824b7544b6ef6fb8ee450191f82c15cf7e`) with a ScieFlow header; no other change |
| `latex_engine.py` | `scripts/latex_engine.py` | `f1135c22666cb153496380c5096857d10092d060a9b37990adaaa6ea282607bf` | `f1135c22666cb153496380c5096857d10092d060a9b37990adaaa6ea282607bf` | none |
| `templates/cover-letter.tex` | `templates/latex/cover-letter.tex` | `6e21b6e4c2a36925dbc86557546e60185df563db4a53301a5bbd8a73184ab1b5` | `6e21b6e4c2a36925dbc86557546e60185df563db4a53301a5bbd8a73184ab1b5` | none |
| `templates/response-to-reviewers.tex` | `templates/latex/response-to-reviewers.tex` | `b6ccf83797df3f78ba459471ee948d4547ea370071eb1ac3f5fc6769150bb3e8` | `b6ccf83797df3f78ba459471ee948d4547ea370071eb1ac3f5fc6769150bb3e8` | none |
| `LICENSE` | `LICENSE` | `1126322e2cc8d165adc4c792eeb195717de2bcc7b39be1ce77959d78e87ef685` | `1126322e2cc8d165adc4c792eeb195717de2bcc7b39be1ce77959d78e87ef685` | none |
| `verify.py` (M-C2 t1) | `core/researcher_core/verify.py` | `86d61475192b15b75fad0e7a48ca61fe15559f7d28b4aa534bffcc8a000fce15` | `826ffcd2bb7b8b5b4660316e52ce744e9b11c2a3f832b7cd34ee05ab5f6aa455` | five import redirections: `from rapidfuzz import fuzz` -> `from ._fuzz import fuzz`; `from . import PARSER_VERSION, __version__` -> `from ._deps import PARSER_VERSION, __version__`; `from .connectors import BaseConnector, SourceError, create_connector` -> `from ._deps import BaseConnector, SourceError, create_connector`; `from .snapshots import SnapshotSession` -> `from ._deps import SnapshotSession`; `from .status import StatusEntry, check_status_async` -> `from ._deps import StatusEntry, check_status_async` |
| `monitor.py` (M-C2 t1) | `core/researcher_core/monitor.py` | `a71880ef3865e55851d31640d533054137d0ebdeb0bfcdaf1722bb9b56e42239` | `6bf72d14020a7b161dc6509dd8caafc27636a6a98b07e7e0fce4a3b54deff9e8` | `from .provenance import ProvenanceEvent, RunContext, normalize_ts` -> `from ._deps import ProvenanceEvent, RunContext, normalize_ts` |
| `bib.py` (M-C2 t1) | `core/researcher_core/bib.py` | `3765fc93aa3df3ff144f8636f291971718fa755596b0afdacf82e9883114cb49` | `3765fc93aa3df3ff144f8636f291971718fa755596b0afdacf82e9883114cb49` | none |
| `errors.py` (M-C2 t1, excerpt) | `core/researcher_core/connectors/base.py` | `505374780dc07bf538fecd95243d7804dbf6e72893cd0bba59fce743301505e8` | `652683a186c36a4eb194df8bef9a04ab2560239b8724ee46d906abbd32b2b7f3` | excerpt of lines 206-271 (excerpt sha256 `9086484bfea71e73f7fa1868a9aaf745026e7a29aaf3f5683391c61a20fc21fa`) with a ScieFlow header; no other change |
| `provenance_ts.py` (M-C2 t1, excerpt) | `core/researcher_core/provenance.py` | `6c77c217333fc4eea106ae5c8edcabec975503fb214942b109539ef20c3d2002` | `03fbd217c77ed83047d2f12247c1ceab2869db62ce7d024e2bd834a099174de5` | excerpt of lines 118-224 (excerpt sha256 `2da36f1267df1d782b7ac3626e677335c8f96caca72726f22386aa57e95e6a5f`) with a ScieFlow header; no other change |
| `schemas/verification-report.schema.json` (M-C2 t1) | `core/schemas/verification-report.schema.json` | `d2ed7d87fface8e1e61114de20c2f9237d7f39d8814ab467f713b953c654dd2d` | `d2ed7d87fface8e1e61114de20c2f9237d7f39d8814ab467f713b953c654dd2d` | none |

Every vendored file is byte-identical to its upstream text, except for the recorded patches
(`dedupe.py`; `verify.py`, five; `monitor.py`, one) and the ScieFlow headers that the excerpts
(`query.py`, `contributors.py`, `errors.py`, `provenance_ts.py`) add to their pinned line ranges.
`_fuzz.py`, `_deps.py` and `__init__.py` are ScieFlow-authored and are not pinned.

- `_fuzz.py` replaces `rapidfuzz.fuzz.token_sort_ratio` (M-C1-D2, not a ScieFlow dependency), and
  since M-C2 t1 also `rapidfuzz.fuzz.ratio` (M-C2-D1). It is checked against 300 cases recorded
  from rapidfuzz 3.14.6 with exact float equality (`tests/vendor/test_fuzz_parity.py`,
  `tests/vendor/test_fuzz_ratio.py`).
- Not vendored: the rest of `connectors/base.py` (it imports httpx, the cache and the snapshot
  store), the rest of `core/researcher_core/export.py` (only its lines 809-1000, the ORCID, ROR
  and CRediT validators, are vendored in `contributors.py`), the upstream package `__init__.py`,
  the upstream connector package, `snapshots.py`, `status.py` and the provenance ledger. verify.py
  and monitor.py import those; the import redirections above point them to `_deps.py`, the
  ScieFlow-authored stand-ins that refuse with NotImplementedError and name their replacement
  (M-C2-D1, tests `tests/vendor/test_deps.py`).
- `latex_engine.py` (TeX engine detection and compile commands) and the two LaTeX templates
  `cover-letter.tex` and `response-to-reviewers.tex` are byte copies with no patch (M-F t1).
- Upstream tests ported for the vendored modules: `tests/vendor/test_researcher_{model,dedupe,rank,sanitize}.py`,
  and for M-F t1 `tests/vendor/test_researcher_contributors.py` (upstream `core/tests/test_export.py`
  lines 446-566, the ORCID, ROR and CRediT sections) and `tests/vendor/test_researcher_latex_engine.py`
  (upstream `scripts/tests/test_latex_engine.py`).
- M-C2 t1: `tests/vendor/test_researcher_{verify,monitor,bib}.py` (upstream `core/tests/test_verify.py`,
  `test_monitor.py`, `test_bib.py`; imports rewritten, the removed tests and helpers listed in each
  file's header). Their fixtures are byte copies under `tests/fixtures/kernel2/`. The excerpts are
  tested by `tests/vendor/test_vendored_excerpts.py`, the stand-ins by `tests/vendor/test_deps.py`.
  The removals in `test_researcher_sanitize.py` (the verify tests) are listed in that file's header.

## Rules

- Rule: never edit a vendored file except through a recorded patch. A change goes into `patches` in
  UPSTREAM.json, and `tests/vendor/test_vendored_integrity.py` checks it.
- Update procedure: copy the new upstream files from a tag, re-pin the sha256 values in
  UPSTREAM.json, update the table above and CREDITS.md, then run `tests/vendor` and the dedup gold set.
