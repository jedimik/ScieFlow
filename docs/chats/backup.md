# Backing up

## The short version

```bash
./scripts/chats-push.sh       # pick agents and projects, then bundle (and upload)
uv run scieflow chats backup  # the same, one chat at a time
```

That walks you through every step below and leaves one encrypted file in
`bundle_dir`.

## Step by step

### 1. See what is there

```console
$ uv run scieflow chats scan --tool claude

claude  (33 chats, 37.2M)
  2026-09-14  ScieFlow                 412 rec   5.2M  fix remote-exec policy refusal
  2026-09-11  ScieFlow                  88 rec   1.1M  news module agent settings
  2026-08-30  SegSnake                 210 rec   3.4M  figure captions for SoftwareX
...
33 chats, 37.2M total
```

`scan` reads and writes nothing else. Narrow it with `--tool`, `--since
2026-08-01`, `--project SegSnake`, or take `--json` for scripting.

### 2. Pick the chats

`backup` opens a checkbox list grouped by tool and project. Space toggles,
enter confirms.

```
── claude ──
 ❯ ◉ 2026-09-14  ScieFlow               5.2M  fix remote-exec policy refusal
   ◯ 2026-09-11  ScieFlow               1.1M  news module agent settings
   ◉ 2026-08-30  SegSnake               3.4M  figure captions for SoftwareX
── codex ──
   ◯ 2026-09-12  ScieFlow              12.0M  Demo thread
```

The same pre-filters work here: `backup --tool claude --since 2026-08-01`
shortens the list before you ever see it.

### 3. Skills and plugins are worked out for you

ScieFlow then reads the transcripts you picked and looks for what they
invoked: `Skill` tool calls, `/slash-commands`, and `mcp__<server>__*` tool
names. Each hit is resolved against what is installed — `~/.claude/skills`
(symlinks into `~/.agents/skills` are followed), the plugin registry in
`installed_plugins.json`, and the MCP servers in `~/.claude.json`.

The result comes back as a second checklist, pre-checked:

```
 ❯ ◉ skill: demo-skill      —  used by 2 chat(s)
   ◉ plugin: superpowers@claude-plugins-official  —  v6.3.0  —  used by 2 chat(s)
   ◉ mcp: notebooklm        —  used by 1 chat(s)
   ◯ skill: pdf             —  used by 1 chat(s)  —  low confidence
```

Antigravity hits are always *low confidence* and unchecked by default: its
transcripts are protobuf, so the match is a byte scan rather than a parsed
tool call.

### 4. Review the secret scan

Before packing, transcripts are scanned for secret-shaped strings — OpenAI and
Anthropic keys, GitHub tokens, AWS access keys, PEM private keys, bearer and
Slack tokens. You get **counts only**; the values are never printed, and never
written to the manifest.

```
secret-shaped strings (counts only — review before sharing):
  claude:2b36cf36-…  github-token×1, bearer-token×2
```

Deselect that chat, or accept it knowingly. `--no-scan-secrets` skips the pass.

### 5. Confirm and write

```
3 chats, 4 skills/plugins, 9.7M -> /home/you/scieflow-chat-bundles/scieflow-chats-pc1-20260920-1432.zip
Write this bundle? [y/N]: y
wrote /home/you/scieflow-chat-bundles/scieflow-chats-pc1-20260920-1432.zip.gpg  (3.1M)
```

The bundle is deflated, then encrypted with `age` (or `gpg`, whichever is on
PATH), and written `0600`.

## Repeatable backups

For a selection you want to reuse — a nightly job, or the same set of projects
every time — write a plan file, edit it, and replay it:

```bash
uv run scieflow chats plan backup-plan.yml     # every chat, all 'selected: false'
$EDITOR backup-plan.yml                        # flip the ones you want to true
uv run scieflow chats backup --plan backup-plan.yml --yes
```

`--plan` and `--all` are mutually exclusive, and `--plan` never prompts.

## Flags

| Flag | Effect |
|---|---|
| `--tool claude\|codex\|agy\|gemini` | limit to one store (repeatable) |
| `--since YYYY-MM-DD` | only chats updated on or after this date |
| `--project TEXT` | only chats whose project path contains this (repeatable) |
| `--all` | take everything that matched; no prompts |
| `--plan FILE` | replay a saved selection |
| `--out PATH` | write the bundle here instead of `bundle_dir` |
| `--with-brain` | include Antigravity `brain/` scratch dirs (large) |
| `--no-scan-secrets` | skip the secret scan |
| `--no-encrypt` | plaintext bundle; also requires `--yes` |
| `--yes` | skip the confirmation prompt |

## What is left out, and why

Excluded by default because it is re-downloadable, a cache, or a log:

| Path | Size here | Why |
|---|---|---|
| `~/.codex/packages` | 4.1G | re-downloaded on demand |
| `~/.codex/logs_2.sqlite` | 169M | logs |
| `~/.claude/plugins/{cache,marketplaces}` | 172M | re-installed from `known_marketplaces.json` |
| `~/.gemini/antigravity-cli/brain` | 122M | scratch space (`--with-brain` keeps it) |
| `debug/`, `statsig/`, `cache/`, `tmp/` | — | noise |

Excluded always, with no override: the four credential files listed in
[Security and privacy](security.md).

Add your own patterns with `exclude_extra` in `config/chats.yml`.

## Non-interactive use

With no TTY, `backup` refuses to guess: pass `--all` or `--plan FILE` together
with `--yes`. Without them you get

```
not written: pass --yes to confirm non-interactively
```
