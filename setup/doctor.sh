#!/usr/bin/env bash
# ScieFlow environment check. Usage: setup/doctor.sh [--agents]
# --agents also live-pings each enabled agent CLI headless (costs tokens).
set -u
cd "$(dirname "$0")/.."

fail=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=1; }
wrn() { printf '  \033[33m⚠\033[0m %s\n' "$1"; }

echo "Binaries:"
for b in uv claude codex agy apptainer zot; do
  if command -v "$b" >/dev/null 2>&1; then ok "$b"; else bad "$b missing (see setup/install.sh)"; fi
done
if command -v latexmk >/dev/null 2>&1; then ok "latexmk"; else
  wrn "latexmk missing — only needed for LaTeX builds: sudo apt-get install latexmk texlive-latex-extra"
fi

echo "Python deps:"
if uv run python -c "import yaml, click, requests, jsonschema, numpy, skimage" 2>/dev/null; then ok "importable"; else bad "run: uv sync --all-extras"; fi

echo "Framework self-test:"
if uv run pytest -q >/dev/null 2>&1; then ok "test suite passes"; else bad "uv run pytest fails"; fi

echo "Zotero:"
if command -v zot >/dev/null 2>&1; then
  if zot --no-interaction stats >/dev/null 2>&1; then ok "zot reachable"; else bad "zot cannot reach Zotero (start Zotero / zot config init)"; fi
fi

echo "Scholarly APIs:"
apis=(
  "openalex|https://api.openalex.org/works?per-page=1"
  "crossref|https://api.crossref.org/works?rows=1"
  "europepmc|https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=test&format=json&pageSize=1"
  "arxiv|https://export.arxiv.org/api/query?search_query=all:test&max_results=1"
)
for entry in "${apis[@]}"; do
  name="${entry%%|*}"; url="${entry#*|}"
  if curl -sf --max-time 20 "$url" >/dev/null; then ok "$name"; else bad "$name unreachable"; fi
done

if command -v latexmk >/dev/null 2>&1; then
  echo "LaTeX compile test:"
  tmp=$(mktemp -d)
  printf '\\documentclass{article}\\begin{document}ok\\end{document}\n' > "$tmp/t.tex"
  if (cd "$tmp" && latexmk -pdf -interaction=nonstopmode t.tex >/dev/null 2>&1); then ok "compiles"; else bad "latexmk failed on a trivial doc"; fi
  rm -rf "$tmp"
fi

if [ "${1:-}" = "--agents" ]; then
  echo "Agent headless ping (costs tokens):"
  tmpd=$(mktemp -d)
  printf 'Reply with the single word: pong' > "$tmpd/ping.md"
  for a in claude codex agy; do
    command -v "$a" >/dev/null 2>&1 || { bad "$a missing"; continue; }
    if uv run scieflow agent run "$a" "$tmpd/ping.md" "$tmpd/$a.log" >/dev/null 2>&1 \
       && grep -qi pong "$tmpd/$a.log"; then ok "$a"; else bad "$a headless ping failed (auth? flags in config/agents.yml?)"; fi
  done
  rm -rf "$tmpd"
fi

echo
if [ "$fail" -eq 0 ]; then echo "All checks passed."; else echo "FAILURES above — fix before running workflows."; fi
exit "$fail"
