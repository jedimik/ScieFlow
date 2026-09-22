#!/usr/bin/env bash
# Back up agent chats and upload them: pick agents, pick projects, done.
#   ./scripts/chats-push.sh                       # ask
#   ./scripts/chats-push.sh --tool claude --yes   # no questions
# Flags are passed through to scripts/chats_sync.py push (see --help).
cd "$(dirname "$0")/.." || exit 1
exec uv run scripts/chats_sync.py push "$@"
