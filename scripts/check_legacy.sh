#!/usr/bin/env bash
# Re-runnable smoke check: every command shape old agent chats use must still
# work after the module merge (docs/MIGRATION.md, src/scieflow/core/legacy.py).
# Each line runs the old form in a harmless mode and must exit 0.
set -u
cd "$(dirname "$0")/.."
fail=0
check() {
  local label=$1; shift
  if out=$("$@" 2>&1); then
    printf 'ok    %s\n' "$label"
  else
    printf 'FAIL  %s\n%s\n' "$label" "$(printf '%s\n' "$out" | tail -3)"
    fail=1
  fi
}
check "scripts/agent_run.py --help"       uv run scripts/agent_run.py --help
check "scripts/stub_agent.py (import)"    uv run python -c "import runpy; runpy.run_path('scripts/stub_agent.py', run_name='x')"
for s in search_openalex search_arxiv search_europepmc search_crossref \
         validate_findings check_citations zotero_export; do
  check "scripts/$s.py --help"            uv run scripts/$s.py --help
done
check "expx --help"                       uv run expx --help
check "expx compare --help"               uv run expx compare --help
check "whatsnew --help"                   uv run whatsnew --help
check "from sflib.config import …"        uv run python -c "import sys; sys.path.insert(0,'scripts'); from sflib.config import repo_root; repo_root()"
for s in lit-review gap-discovery paper-draft paper-review evaluator \
         experiment-designer experiment-runner framework-extender \
         literature-support model-routing research-loop; do
  check "skills/$s/SKILL.md"              test -f "skills/$s/SKILL.md"
done
check "RESEARCHX_MAILTO fallback"         env -u SCIEFLOW_MAILTO RESEARCHX_MAILTO=a@b.c uv run python -c "from scieflow.core import legacy; assert legacy.env('SCIEFLOW_MAILTO')=='a@b.c'"
exit $fail
