#!/usr/bin/env bash
# Fetch a chat bundle from DVC storage and restore it (dry run first).
#   ./scripts/chats-pull.sh                       # ask which bundle
#   ./scripts/chats-pull.sh --latest --tool claude
# Flags are passed through to scripts/chats_sync.py pull (see --help).
cd "$(dirname "$0")/.." || exit 1
exec uv run scripts/chats_sync.py pull "$@"
