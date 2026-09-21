# chats module — instructions for AI agents

Selective backup and cross-machine restore of agent chat stores
(`~/.claude`, `~/.codex`, `~/.gemini`, `~/.gemini/antigravity-cli`).

## Hard rules

1. **User-invoked only.** Never run `scieflow chats backup` or
   `scieflow chats restore` on your own initiative. A bundle is a copy of the
   user's entire conversation history; deciding to make one is theirs.
2. **Never bundle credentials.** `.credentials.json`, `auth.json`,
   `oauth_creds.json` and `antigravity-oauth-token` are excluded in
   `stores/base.py:CREDENTIAL_FILES` and that list is not flag-overridable.
   Never add a path that defeats it, and never suggest `--no-encrypt` as a
   convenience.
3. **Never transmit a bundle.** Do not upload, attach, paste or `scp` one, and
   never run `scieflow chats push` or `pull`. The optional DVC transport
   (`remote.enabled` in `config/chats.yml`) exists for the user to run by
   hand; it is off by default and enabling it is their decision, not a
   suggestion you act on. Moving a bundle between machines is their action.
4. **Restore is confirmed, never assumed.** `restore` is a dry run unless
   `--apply`. Present the change table and wait for the user's word before
   adding `--apply`. Never add `--overwrite` unless they asked for it by name.
5. **Respect the running-CLI refusal.** If `restore` refuses because `claude`,
   `codex` or `agy` is running, stop and ask the user to close it. The Codex
   and Antigravity SQLite files are open at that moment and a partial write
   loses threads. Do not work around it.
6. `config/chats.yml` is user-owned and deny-by-default. It bounds every store
   root that may be read. Never widen it; ask instead.

## Command surface

`scieflow chats init | scan | plan | backup | inspect | restore`, plus the
user-only `push` / `pull` when remote sync is enabled.
`scan` is read-only and safe to run when the user asks what is on the machine.

## Shape

Each store is a `StoreAdapter` (`stores/base.py`) with five calls —
`discover`, `collect`, `sidecars`, `transcript_texts`, `plan_restore` — so
every tool shares one selection list, one manifest and one remap pass.
Path rewriting lives in `remap.py` and is JSON-aware: only values under
`PATH_KEYS` are substituted, never free text. Archiving reuses
`scripts/sflib/archive.py` for its zip-slip guard and CRC verification.
