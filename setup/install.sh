#!/usr/bin/env bash
# ScieFlow bootstrap: submodules + python env + tool checks.
set -euo pipefail
cd "$(dirname "$0")/.."

git submodule update --init --recursive
uv sync

command -v claude >/dev/null 2>&1 || echo "WARN: 'claude' CLI not found — required to run the loop"
command -v conda  >/dev/null 2>&1 || echo "WARN: 'conda' not found — required for ExperimentX (expx env)"
command -v apptainer >/dev/null 2>&1 || echo "WARN: 'apptainer' not found — required for recorded ExperimentX runs"

echo "OK. Vendor environments: see vendors/ExperimentX/README.md and vendors/ResearchX/README.md"
