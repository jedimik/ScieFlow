# Future Ideas

Notes on directions discussed but deliberately out of scope for now. None of
them are blocked by the current architecture.

## 1. Remote access with login (single user)

Reach the GUI from other machines behind a password.

- The login itself is easy: NiceGUI runs on FastAPI — login page, hashed
  password (argon2/bcrypt), session cookie, auth middleware. ~A day of work,
  slots into `gui/` without touching core.
- The real cost is network exposure: TLS (reverse proxy, e.g. Caddy), the app
  shells out to agent CLIs authenticated as the host user (a compromised
  session is close to RCE — lock agent tools down to web-search only),
  rendered agent markdown is untrusted content (sanitize, no raw HTML),
  no brute-force protection or audit trail for free.
- **Pragmatic recommendation:** don't expose to the internet. Use
  Tailscale/WireGuard or an SSH tunnel (`ssh -L 8080:localhost:8080 …`);
  password login then becomes a second layer, not the only wall.

## 2. Multi-user: users connect their own agent accounts via browser

The biggest possible jump — from standalone tool to multi-tenant hosted
service. Three routes, in order of preference:

1. **Per-user API keys (realistic route).** User pastes their
   Anthropic/OpenAI/Gemini API key; stored encrypted; injected as env var
   into that user's agent subprocess. All three CLIs accept key auth. Days of
   work on top of user accounts; the provider-sanctioned path.
2. **Relaying CLI OAuth login through the browser.** `claude login` /
   `codex login` flows expect terminal + browser on the same machine
   (localhost callback). Server-side relaying is fragile, undocumented, and
   consumer-subscription terms (Claude Pro/Max, ChatGPT Plus) generally don't
   permit shared server-side use. Hardest and least legitimate — avoid.
3. **Hosted mode calls provider APIs directly** (web-search-capable APIs),
   local mode keeps CLIs. Cleanest long-term, weeks of work — rebuilds the
   agent layer per provider.

Security stakes change qualitatively — custodian of other people's
credentials and money:

- Credentials encrypted at rest, never logged; HTTPS mandatory.
- CLI subprocesses need per-user isolated `HOME` (CLIs cache tokens in the
  home dir — shared HOME leaks tokens between users), ideally containers.
- Per-user rate limits, run quotas, audit trail (runs spend users' money).
- Everything from idea 1 becomes mandatory.

Architecture hooks already in place: `agents.py` adapters can take per-user
env vars; `db.py` can grow a `users` table; core stays single-user-agnostic.

## 3. Review-base ideas (noted 2026-07-19, Phase 4 design)

- **Per-interest digest compilation:** compile all stored reports for one
  interest across runs into a single chronological digest markdown.
- **Paused/muted interests:** a flag to skip an interest without deleting it.
- **Archive export:** download the whole review base (all runs) as one file
  or zip from the GUI.

## 4. Smaller ideas mentioned along the way

- Run logs viewable in the GUI: store subprocess output per run in TinyDB,
  render on the Run page. Trivial, could land in Phase 2.
- Built-in scheduling (cron/systemd installer subcommand).
- API-key mode for machines without agent CLIs installed.
