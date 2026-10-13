#!/usr/bin/env bash
# File-ownership check for the scite branches (docs/research/review-contract.md).
#
# Fails (exit 1) when this branch — commits since the merge base with BASE plus
# uncommitted and untracked changes — touches a file owned by the parallel
# ScieFlowDev session (web) or one of the K16 files. Warns (exit 0) when a
# shared file loses lines: shared files take additive hunks only, but an
# in-place edit of a line is sometimes unavoidable and needs a human look.
#
# Usage: scripts/check_ownership.sh [BASE]   (default: dev/cloud-continuation,
#        or $SCIEFLOW_OWNERSHIP_BASE). Exit 2: BASE is not a known revision.
set -u
cd "$(dirname "$0")/.." || exit 2

base=${1:-${SCIEFLOW_OWNERSHIP_BASE:-dev/cloud-continuation}}
if ! git rev-parse --verify -q "$base^{commit}" >/dev/null; then
  echo "check_ownership: unknown base revision '$base'"
  exit 2
fi
mb=$(git merge-base "$base" HEAD) || { echo "check_ownership: no merge base with '$base'"; exit 2; }

# Owned by ScieFlowDev (web) or K16 — never edited from this branch.
owned='^(src/scieflow/web/|tests/web/|src/scieflow/core/(agent_run|service|jobs|sandbox|provenance|harness_commands)\.py$|src/scieflow/core/turn_[^/]*\.py$|src/scieflow/core/run/conversation\.py$)'
# Shared files: additive hunks only.
shared='^(config/agents\.yml|config/defaults\.yml|src/scieflow/core/agent_config\.py|src/scieflow/core/agent_configure\.py|src/scieflow/core/events\.py|schemas/gates\.yml|AGENTS\.md|src/scieflow/research/AGENTS\.md|pyproject\.toml)$'

list=$(mktemp) || exit 2
trap 'rm -f "$list"' EXIT
# NUL-delimited, so names with non-ASCII characters, quotes, tabs or newlines
# are compared as they are, not in git's quoted form.
{
  git diff --name-only -z --no-renames "$mb"        # committed + staged + unstaged
  git ls-files -z --others --exclude-standard        # untracked
} > "$list"

declare -A seen=()
bad=()
count=0
while IFS= read -r -d '' p; do
  [ -n "${seen[$p]+x}" ] && continue
  seen[$p]=1
  count=$((count + 1))
  if [[ $p =~ $owned ]]; then
    bad+=("$p")
  fi
done < "$list"

if [ "${#bad[@]}" -gt 0 ]; then
  echo "check_ownership: FAIL — files owned by ScieFlowDev (web) or K16 are touched (base $base):"
  printf '  %s\n' "${bad[@]}"
  exit 1
fi

while IFS=$'\t' read -r -d '' added deleted p; do
  if [[ $p =~ $shared ]] && [ "$deleted" != "0" ] && [ "$deleted" != "-" ]; then
    echo "check_ownership: warning — shared file $p loses $deleted line(s); shared files take additive hunks only"
  fi
done < <(git diff --numstat -z --no-renames "$mb")

echo "check_ownership: ok ($count changed files vs $base)"
exit 0
