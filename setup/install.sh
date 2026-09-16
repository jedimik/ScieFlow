#!/usr/bin/env bash
# ScieFlow bootstrap: installs what it can, prints exact commands for the rest.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== ScieFlow install =="

if ! command -v uv >/dev/null 2>&1; then
  echo "✗ uv missing. Install it first:"
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi
echo "✓ uv $(uv --version | head -1)"
uv sync --all-extras
echo "✓ python deps synced (extras: experiments, research)"

missing=0
check() { # name, install hint, post-install hint
  if command -v "$1" >/dev/null 2>&1; then
    echo "✓ $1"
  else
    echo "✗ $1 → install: $2"
    [ -n "${3:-}" ] && echo "       then: $3"
    missing=1
  fi
}

check claude    "npm install -g @anthropic-ai/claude-code" "claude (login on first run)"
check codex     "npm install -g @openai/codex" "codex login"
check agy       "see https://antigravity.google (CLI install docs)" "agy install"
check apptainer "see https://apptainer.org/docs (needed for recorded experiment runs)" ""
check conda     "see https://docs.conda.io (only for conda-based stage environments)" "conda env create -f envs/experiments.yml"
check zot       "see the zot-cli project README (research: Zotero export)" "zot config init"
check latexmk   "sudo apt-get install -y latexmk texlive-latex-extra biber (research: paper drafts)" ""

echo
if [ "$missing" -eq 1 ]; then
  echo "Some tools are missing — install the ones your modules need (commands above)."
fi
echo "Next: setup/doctor.sh   (add --agents for a live headless ping; costs tokens)"
