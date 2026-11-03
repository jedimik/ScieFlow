#!/usr/bin/env bash
# The CI test gate (DECISIONS D22). GitHub runs exactly this after
# `uv sync --frozen --python 3.12 --all-extras --group dev`; run it the same way locally.
# Extra arguments go to pytest (for example --junitxml=PATH).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
# Hermetic test environment (M-I t1 reproduction finding). The sandbox tests need temp
# directories outside /tmp (the sandbox mounts a private tmpfs on /tmp) and under the
# user's home, where test_s2 expects /home to be an ancestor of the test homes; no agent CLI
# (claude, codex, agy) may be on the test process PATH. uv is resolved before PATH changes.
UV=$(command -v uv)
mkdir -p "$HOME/.cache"
TEST_TMP=$(mktemp -d "$HOME/.cache/scieflow-ci.XXXXXX")
TEST_PATH=$(echo "$PATH" | tr ':' '\n' | while IFS= read -r d; do
  [ -n "$d" ] || continue
  for c in claude codex agy; do [ -e "$d/$c" ] && continue 2; done
  echo "$d"
done | paste -sd: -)
set +e
env TMPDIR="$TEST_TMP" PATH="$TEST_PATH" "$UV" run --frozen --no-sync python -m pytest -q -p no:cacheprovider \
  -m 'not slow and not live and not scite_live' \
  --ignore=tests/probes/test_k_task8_binding.py \
  "$@" tests
RC=$?
set -e
rm -rf "$TEST_TMP"
exit "$RC"
