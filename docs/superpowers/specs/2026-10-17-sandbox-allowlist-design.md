# Allow-list agent sandbox — design (2026-10-17)

- **Status:** proposed. The human's decisions are recorded in `RUN/approvals/k-task8-sandbox-allowlist-decisions-20261017.md`.
- **Base:** `dev/k16-accepted-foundation` @ `ca57cd2`.
- **Branch:** `dev/sandbox-allowlist`.
- **Worktree:** `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008/scratch/sandbox-allowlist-20261017`.

## Problem

`sandbox.wrap()` starts every dispatch with `--ro-bind / /`. Everything the user can read stays readable inside, including:
- `~/.ssh`;
- `~/.hermes/.env`;
- every agent CLI's login (`~/.claude`, `~/.codex/auth.json`, `~/.gemini`);
- the rest of `/mnt/data`.

`/run` is shared too. Through the user D-Bus, the `systemd --user` socket, the system bus and the gpg/ssh agents, the agent can start unconfined processes. `sandbox.environment()` also passes this process's whole environment through, including any token variables.

The Task 8 r8 reviews made these the H6/H7 disclosures. The human declined to release on that basis.

## Goal

A dispatch can see only what it needs:
- the system's program files;
- the ScieFlow repo, read-only;
- its own run, writable;
- its agent CLI's install directory;
- the folders the human lists;
- its own agent's login and nothing else.

## Decisions

| # | Decision | Source |
|---|---|---|
| D1 | Replace `--ro-bind / /` with an allow-list root. The read-only system set is `/usr` plus the merged-usr links (`/bin`, `/lib`, `/lib64`, `/sbin`), `/etc`, `--dev /dev`, `--proc /proc`, `--tmpfs /tmp` and `--tmpfs /run`.<br>`/home`, `/mnt`, `/srv`, `/root`, `/var` and `/opt` are absent unless granted.<br>`/etc/resolv.conf` is bound as its resolved target, so DNS works with an empty `/run`. | human: "allow selected directories" |
| D2 | `HOME` is a persistent, per-run, per-agent directory: `workspace/<slug>/scratch/agent-home/<agent>/`. It is writable because it is inside the run, and the archive skips it. This keeps `claude --resume`, `codex exec resume` and agent caches working within a run. Nothing from the real home is visible. | operator default |
| D3 | The repo is bound read-only. The existing writable grants (`writable_for`, `config/journals`) and the provenance mask stay exactly as they are today. | unchanged |
| D4 | The agent's executable is resolved on the host and bound read-only by its install root: the launcher path plus `realpath`'s versioned root. Example: `~/.local/share/claude/versions/<v>`, or for codex `~/.codex/packages/standalone/...`. The install root is never `~/.codex` itself, because that holds `auth.json`.<br>`uv`, the repo `.venv` target interpreter and uv-managed Python roots are bound read-only too.<br>K16 execution capture and turn protection must accept the new form. | operator default |
| D5 | Add a `readable:` list to `config/sandbox.yml`, with a `path` and a `reason` on every entry, validated like `writable:`. A hard deny list is never overridable: the real `$HOME` itself, `~/.ssh`, `~/.gnupg`, `~/.hermes`, `~/.config/scieflow/credentials`, and every agent credential directory. Such entries are refused with exit 77. | human: yes |
| D6 | Credentials are configured per agent in `config/sandbox.yml` `credentials:`. There are two modes:<br>• **`env`**: read a token from a user-owned file outside the sandbox (default `~/.config/scieflow/credentials/<agent>.token`, required mode 0600) and set it only in that agent's process environment. For Claude this is `CLAUDE_CODE_OAUTH_TOKEN`, made with `claude setup-token`; the user can revoke it any time.<br>• **`bind`**: bind that agent's own login file or directory into its private `HOME`. This is for CLIs without an env-token login.<br>If an agent has no entry, its dispatch is refused with exit 77 and a message naming the exact setup step. The agent can still read its own credential; that is unavoidable and disclosed. | human: setup-token for Claude |
| D7 | The environment becomes an allow-list (`--clearenv`). Kept: `PATH` (sandbox-safe), `HOME`, `LANG`/`LC_*`, `TZ`, `TERM`, `UV_CACHE_DIR`, the `SCIEFLOW_*` variables dispatch needs, and the agent's credential variable. Everything else is dropped, for example `SCITE_TOKEN`, Discord/Hermes keys and `SSH_AUTH_SOCK`. | operator default |
| D8 | Extend `verify()` and `setup/doctor.sh`. Besides the existing write proof, a probe inside the sandbox must find these absent: `~/.ssh`, `~/.hermes`, `/run/user/$UID/bus`, `/run/dbus/system_bus_socket`, and one non-granted host directory. Any failure refuses the dispatch with exit 77. | operator default |
| D9 | The network stays open. A domain allow-list proxy becomes return-to item **B6**. | human |
| D10 | Out of scope: the news module's unsandboxed path (unchanged, already documented), and the `--no-sandbox`/`unsandboxed_runs` escape hatches (unchanged). | — |
| D11 | Once this merges into the K16 line, Task 8 is re-bound to this sandbox. The release question returns with H6/H7 rewritten to the remaining exposure: the agent's own login, open network, and the readable repo. | human: one probe of the real thing |

## Remaining exposure (to disclose)

- **Its own login:** the agent can read its own credential, the token or the bound login.
- **Open network:** it can send what it sees to any host.
- **What it can read:** the repo, its run, and the `readable:` folders.
- **K16 H7 still applies:** the agent can write its own evidence. That is return-to item B3.

## Tickets

| # | Title | Blocked by | Delivers |
|---|---|---|---|
| S1 | Allow-list root and per-run agent home in `wrap()` (tracer bullet) | — | A fake agent inside the sandbox reads the repo, writes its run, finds `~/.ssh`, `~/.hermes` and `/run/.../bus` absent, and `HOME` persists across two dispatches of one run. `uv run python` works. |
| S2 | `readable:` list with the hard deny list | S1 | Parser, validation and refusal (exit 77). The `config/sandbox.yml` comment block and `docs/sandbox.md` section are updated. |
| S3 | Agent and tool install-root binding, plus K16 execution-capture compatibility | S1 | claude, codex, agy and uv resolve inside (tested with fake installs mirroring each layout); turn protection and the execution capture tests are green. |
| S4 | Environment allow-list and per-agent credentials (`env` and `bind`) | S1, S3 | Only listed variables survive. The token reaches only its own agent. A missing setup gets exit 77 with the exact instruction, and doctor reports each agent's credential status. |
| S5 | Confinement proof in `verify()` and doctor | S1, S2 | The secret-absence probe runs, and a deliberately leaky wrap is refused. |
| S6 | Docs and integration | S2–S5 | `docs/sandbox.md` is rewritten ("What it does not do" now lists only the own login, the network and readable grants). The full suite passes. Spec review and code/security review (Sonnet 5.5 @ High) both PASS. |
| S7 | Re-bind Task 8 to the allow-list sandbox | S6 and a human merge decision | New binding generation and both reviews, then the release question with the new H6/H7 text. |

Routing: Opus 5.5 coordinates, Haiku 5.5 codes test-first, Sonnet 5.5 reviews, all @ High. A usage limit means pause, never a model swap. No live `claude` call happens during S1–S6: all tests use fake CLIs.

## Human setup (needed only before S7's probe)

- Run `claude setup-token` once.
- Save the token to `~/.config/scieflow/credentials/claude.token` with mode 0600. Doctor will check it.
