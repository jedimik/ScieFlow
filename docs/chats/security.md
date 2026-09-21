# Security and privacy

A chat bundle is one of the most sensitive files on your machine. It contains
everything you and your agents said, plus the tool output they saw: source
code, file contents, command results, and anything you ever pasted into a
prompt. Treat it like a backup of your home directory, not like a log file.

## Never bundled

These four files are excluded in `stores/base.py:CREDENTIAL_FILES`, and no
flag overrides that:

- `~/.claude/.credentials.json` — Claude Code OAuth
- `~/.codex/auth.json` — Codex credentials
- `~/.gemini/oauth_creds.json` and `google_accounts.json` — Gemini CLI OAuth
- `~/.gemini/antigravity-cli/antigravity-oauth-token` — Antigravity OAuth

Two more places are scrubbed rather than dropped, because the rest of the file
is worth keeping:

- `~/.claude/settings.json` — the `env`, `apiKeyHelper` and `awsAuthRefresh`
  keys are removed.
- `~/.claude.json` — only `projects` and `githubRepoPaths` survive; machine
  identity (`userID`, `machineID`, `oauthAccount`) and every `cached*` blob
  are dropped, and MCP server `env` values become `<redacted>`.

## Still in there

Everything the transcripts contain. The scrubbing above protects config files,
not conversations. If you pasted a token into a prompt three months ago, it is
in that transcript.

## The secret scan

Before packing, each selected transcript is checked for secret-shaped strings:
OpenAI (`sk-…`) and Anthropic (`sk-ant-…`) keys, GitHub tokens (`ghp_…` and
friends), AWS access keys (`AKIA…`), PEM private-key headers, `Bearer` tokens
and Slack tokens.

You get a **count per chat, per pattern**. The matched text is never printed,
never logged and never written to the manifest — only `github-token×1`.

It is a prompt to think, not a guarantee. It catches shapes it knows about; a
password, an internal hostname or a customer name will sail straight past.
Deselect a flagged chat if the count surprises you. `--no-scan-secrets` turns
the pass off.

## Encryption

Encryption is on by default. ScieFlow shells out to `age` (preferred) or
`gpg` — whichever is on `PATH` — so there is no Python crypto dependency and
no key material handled by ScieFlow itself:

- `age -p` or `gpg --symmetric --cipher-algo AES256` prompt you for a
  passphrase on the terminal.
- Set `encryption.recipient` in `config/chats.yml` to an `age` public key to
  encrypt to a key instead of a passphrase.
- If the configured backend is missing but the other one is installed,
  ScieFlow uses that and says so.

`--no-encrypt` writes a plaintext zip. It also requires `--yes`, so it can
never happen by accident, and the confirmation line says `(UNENCRYPTED)`.

## File permissions

The bundle is written `0600`. During `inspect` and `restore` it is decrypted
into a `0700` temporary directory, which is removed when the command exits —
including when it fails.

## Transport

ScieFlow never sends a bundle anywhere. There is no upload command, no remote
target, no cloud integration. Moving the file between machines is your action
and your choice of channel. If you put it on a shared drive or a cloud sync
folder, remember that the encryption passphrase is the only thing protecting
it there.

## For agents

`src/scieflow/chats/AGENTS.md` binds any agent operating this module: never
start a backup or restore unprompted, never transmit a bundle, never work
around the credential exclusion or the running-CLI refusal.
