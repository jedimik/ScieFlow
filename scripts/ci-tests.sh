#!/usr/bin/env bash
# The CI test gate (DECISIONS D22). GitHub runs exactly this after
# `uv sync --frozen --python 3.12 --all-extras --group dev`; run it the same way locally.
# Extra arguments go to pytest (for example --junitxml=PATH).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
exec uv run --frozen --no-sync python -m pytest -q -p no:cacheprovider \
  -m 'not slow and not live and not scite_live' \
  --ignore=tests/probes/test_k_task8_binding.py \
  "$@" tests
