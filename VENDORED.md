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
| `LICENSE` | `LICENSE` | `1126322e2cc8d165adc4c792eeb195717de2bcc7b39be1ce77959d78e87ef685` | `1126322e2cc8d165adc4c792eeb195717de2bcc7b39be1ce77959d78e87ef685` | none |

Every vendored file is byte-identical to its upstream text, except for the recorded patch
(`dedupe.py`) and the ScieFlow header that `query.py` adds to its excerpt. `_fuzz.py` and
`__init__.py` are ScieFlow-authored and are not pinned.

- `_fuzz.py` replaces `rapidfuzz.fuzz.token_sort_ratio` (M-C1-D2, not a ScieFlow dependency). It
  is checked against 300 cases recorded from rapidfuzz 3.14.6 with exact float equality
  (`tests/vendor/test_fuzz_parity.py`).
- Not vendored: the rest of `connectors/base.py` (it imports httpx, the cache and the snapshot
  store), the upstream package `__init__.py`, and `verify.py`.
- Upstream tests ported for the vendored modules: `tests/vendor/test_researcher_{model,dedupe,rank,sanitize}.py`.
  The removals in `test_researcher_sanitize.py` (the verify tests) are listed in that file's header.

## Rules

- Rule: never edit a vendored file except through a recorded patch. A change goes into `patches` in
  UPSTREAM.json, and `tests/vendor/test_vendored_integrity.py` checks it.
- Update procedure: copy the new upstream files from a tag, re-pin the sha256 values in
  UPSTREAM.json, update the table above and CREDITS.md, then run `tests/vendor` and the dedup gold set.
