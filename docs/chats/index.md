# Chat backups

Your agent conversations live in per-tool directories under `$HOME`, and every
one of those stores keys its data by **absolute path**. Copy `~/.claude` to a
new laptop and `claude --resume` shows nothing, because the transcripts are
filed under a directory named after the old machine's paths and carry the old
`cwd` on almost every line.

`scieflow chats` fixes that, selectively. You pick the conversations you
actually want, it works out which skills and plugins those conversations used,
packs them into one encrypted file, and rewrites the paths when you restore on
the other machine.

## What it covers

| Tool | Where its chats live | Restorable |
|---|---|---|
| Claude Code | `~/.claude/projects/<path-slug>/<uuid>.jsonl` | yes — slug and `cwd` rewritten |
| Codex CLI | `~/.codex/sessions/…/rollout-*.jsonl` + rows in `state_5.sqlite` and `thread_history_1.sqlite` | yes — rows upserted, paths rewritten |
| Antigravity (`agy`) | `~/.gemini/antigravity-cli/conversations/<uuid>.db` | yes, with one caveat (below) |
| Gemini CLI | `~/.gemini/tmp/<slug>/chats/*.jsonl` | yes — `projectHash` recomputed |

ChatGPT is **not** covered. It keeps nothing on disk, and its account-wide
export cannot be imported back into ChatGPT, so there is nothing to restore.
Codex CLI is the OpenAI store that does have local transcripts.

## First run

```bash
uv sync --extra chats
uv run scieflow chats init          # writes config/chats.yml
uv run scieflow chats scan          # read-only inventory
uv run scieflow chats backup        # pick chats, write an encrypted bundle
```

`config/chats.yml` is yours and deny-by-default: nothing outside the store
roots listed there is ever read. Without it the module does nothing.

## What it will not do

- It never bundles credential files (`~/.claude/.credentials.json`,
  `~/.codex/auth.json`, `~/.gemini/oauth_creds.json`, the Antigravity OAuth
  token). That exclusion is not overridable by any flag.
- It never sends a bundle anywhere on its own. Moving the file between
  machines is your own action — a USB stick, `scp`, whatever you trust. If you
  want the repo's DVC storage to carry it, turn on
  [remote sync](remote.md) explicitly; it stays off by default.
- It never writes during a restore unless you pass `--apply`.

See [Security and privacy](security.md) for what a bundle still contains.

## Known limitations

- **Antigravity bodies are protobuf.** The conversation `.db` files copy and
  restore intact, but paths embedded inside `steps.step_payload` cannot be
  rewritten, and skill detection there is a byte scan marked *low confidence*.
  What does get remapped: the summary row's `workspace_uris`,
  `settings.json`'s `trustedWorkspaces`, and `history.jsonl`.
- **Codex sessions are large** — a few gigabytes is normal. Selecting
  deliberately is the point; a size preflight stops you packing more than
  `max_bundle_gb`.
- **Plugins are referenced, not embedded.** The bundle records
  `name@marketplace` and a version; restore prints the install commands. Pass
  `--with-plugin-payload` only if the target machine will be offline.
- **Legacy Codex threads show `-` for the record count.** Those pre-date the
  history database and have no rows to count; sort by date and size instead.
- **Message counts are records, not turns.** One user turn can be several
  records in a transcript.
