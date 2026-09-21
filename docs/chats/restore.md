# Restoring on another PC

## Before you start

- Install the same extra there: `uv sync --extra chats`, and create
  `config/chats.yml` with `scieflow chats init`.
- Install the decryption backend you used — `age` or `gpg`.
- **Close `claude`, `codex` and `agy`.** Restore refuses to write while any of
  them is running: their SQLite databases are open, and a partial write loses
  threads. This is a hard refusal, not a warning.

## 1. Look inside first

```console
$ uv run scieflow chats inspect scieflow-chats-pc1-20260920-1432.zip.gpg
created  2026-09-20T14:32:07+00:00
source   you@pc1  home=/home/you
tools    agy, claude, codex
chats    3
  claude  2026-09-14  /home/you/Github/ScieFlow          fix remote-exec policy refusal
  codex   2026-09-12  /home/you/Github/ScieFlow          Demo thread
  agy     2026-09-08  /home/you/Github/SegSnake          figure captions
skills/plugins  4
  skill   demo-skill                           bundled
  plugin  superpowers@claude-plugins-official  reference
secret-shaped strings in 1 chat(s) — counts only
```

`inspect` decrypts into a temporary `0700` directory, prints the manifest, and
cleans up. It changes nothing.

## 2. Dry run

```bash
uv run scieflow chats restore scieflow-chats-pc1-20260920-1432.zip.gpg
```

```console
path rewrites:
  /home/you  ->  /home/me

12 change(s):
  create         /home/me/.claude/projects/-home-me-Github-ScieFlow/2b36….jsonl  — slug -home-you-… -> -home-me-…
  skip-exists    /home/me/.claude/settings.json  — credentials and env already stripped
  merge          /home/me/.claude.json  — 2 project entries
  sqlite-upsert  /home/me/.codex/state_5.sqlite  — 14 rows across 2 tables (thread 019fdc93)
  create         /home/me/.codex/sessions/2026/09/12/rollout-….jsonl  — thread 019fdc93
  merge          /home/me/.codex/config.toml  — 1 project block(s) appended
  create         /home/me/.gemini/antigravity-cli/conversations/c052d459….db  — protobuf body copied verbatim
  create         /home/me/.claude/skills/demo-skill  — skill directory

create=7  merge=3  skip-exists=1  sqlite-upsert=1

dry run — nothing written. Re-run with --apply to write.
```

Add `--diff` to see a unified diff for every `merge` before you commit to it.

## 3. Fix the path mapping if needed

The default mapping is the bundle's source `$HOME` to your `$HOME`, which
covers the common case of the same projects under a different username. When a
project moved somewhere else entirely, say so:

```bash
uv run scieflow chats restore bundle.zip.gpg \
  --map /home/you/Github=/data/projects \
  --map /home/you/scratch=/tmp/scratch
```

`--map` is repeatable and longest-prefix wins. Re-run the dry run until the
targets read the way you want.

## 4. Apply

```bash
uv run scieflow chats restore bundle.zip.gpg --apply --yes
```

## What actually gets rewritten

| Store | Rewritten | Left alone |
|---|---|---|
| Claude Code | project slug directory name; `cwd` and `trackingPath` on every record; `~/.claude.json` `projects` keys and `githubRepoPaths` values | paths mentioned in prose or tool output inside a message |
| Codex CLI | `threads.rollout_path` and `threads.cwd`; `payload.cwd` in the rollout; `[projects."…"]` headers in `config.toml` | tool-call arguments inside thread items |
| Antigravity | summary `workspace_uris` and `app_data_dir`; `history.jsonl` `workspace` | everything inside the protobuf `step_payload` |
| Gemini CLI | slug directory; the header's `projectHash`, recomputed as `sha256(<new path>)`; `projects.json` | message bodies (byte-identical) |

Rewriting is JSON-aware, never `sed`: only values under known path-bearing
keys are substituted, so a transcript that merely *mentions* a path keeps its
text intact.

## Safety rules

- **Nothing is overwritten.** A chat file that already exists is reported as
  `skip-exists` and left alone; SQLite rows go in with `INSERT OR IGNORE`.
  `--overwrite` flips both, and you have to ask for it.
- **Metadata files are snapshotted.** Before any `merge` or `sqlite-upsert`,
  the target gets a sibling `.bak` (e.g. `~/.claude.json.bak`) if one does not
  already exist.
- **Writes are atomic.** Generated files go to `.<name>.tmp-<pid>` and are
  renamed into place.
- Non-interactive shells need `--yes`, exactly as `backup` does.

## Troubleshooting

**`claude --resume` shows nothing.** Claude looks up transcripts by the slug
matching your current `pwd`. Check the slug that was written:
`ls ~/.claude/projects` — if it does not match
`$(pwd | tr / -)`, your mapping was wrong. Re-run the restore with the right
`--map`.

**A restored Codex thread is missing from `codex resume`.** The rollout file
and the `threads` row are separate. `sqlite3 ~/.codex/state_5.sqlite "select
id, cwd, rollout_path from threads where id='<uuid>'"` — if the row is absent,
the restore reported `state_5.sqlite not found — start that CLI once to create
it`. Launch `codex` once so it creates its schema, then restore again.

**"not found — start that CLI once to create it".** ScieFlow never creates
another tool's database; the CLI owns that schema. Run the tool once, then
re-run the restore.

**Plugins are referenced but not installed.** The bundle stores
`name@marketplace` and a version, not the payload. Install them the normal
way, e.g. `claude plugin install superpowers@claude-plugins-official`. Use
`--with-plugin-payload` at backup time if the target machine has no network.

**Antigravity conversation opens but its file links point at the old machine.**
Expected: those paths are inside protobuf step payloads and cannot be
rewritten. The conversation itself is intact.

**Restore refuses: "codex still running".** Close it. If you are certain it is
a stale match, check with `pgrep -x codex`.
