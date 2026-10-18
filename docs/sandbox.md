# The agent sandbox

## What it is

Every agent dispatch that goes through `scieflow agent run` — from the
terminal, or from the local web app, which calls the same service layer —
runs inside a [bubblewrap](https://github.com/containers/bubblewrap) sandbox.
The sandbox sees an allow-list of paths and nothing else: a read-only system
set, the repository (read-only), the dispatch's own run (writable), the grants
in `config/sandbox.yml`, and a private per-run agent home. A dispatch whose
sandbox cannot be built or proven to confine is refused, never run unconfined.

`AGENTS.md` rule 1 — run artifacts stay under `workspace/<slug>/`, and a run
never modifies `src/scieflow/` — is enforced by the process. It no longer
depends on the agent following it.

The only bubblewrap-aware code is `src/scieflow/core/sandbox.py`. The two
launchers that call it are `scieflow agent run` (`src/scieflow/core/agent_run.py`)
and the web app's service layer (`src/scieflow/core/service.py`), which
covers the web dispatch and conversational turn paths.

One dispatch path stays outside this boundary: the news module
(`src/scieflow/news/agents.py`) calls an agent CLI directly with
`subprocess.run`, on a prompt of its own making, with no run to confine it
to. It is narrower than a run dispatch — a fixed prompt, no run directory,
no `agent_overrides` — but it is not sandboxed, and nothing on this page
applies to it. Bringing it inside the boundary is future work.

## What a dispatch can see

| Path | Inside the sandbox |
|---|---|
| `/usr`, `/etc`, and the merged-usr links `/bin`, `/lib`, `/lib64`, `/sbin` | read-only |
| `/dev`, `/proc` | fresh device and proc mounts |
| `/tmp`, `/run` | private, empty tmpfs. The resolved DNS config is bound read-only when it lies outside the system set, so name resolution works with an empty `/run` |
| The repository root | read-only (D3). Everything under it is readable, including other runs' `workspace/<slug>/` files and their provenance repositories, except the other runs' agent homes (below) |
| This run, `workspace/<slug>/` | writable for a sub-agent dispatch. Its `provenance.git` is hidden (empty tmpfs) |
| This dispatch's agent home, `<run>/scratch/agent-home/<agent>/` | writable, mode 0700. Its `$HOME` points here |
| Other runs' agent homes, `workspace/<other>/scratch/agent-home/` | hidden (empty tmpfs) if the home exists when this dispatch's layout is built; a home created later, by a run that starts during this dispatch, is readable (read-only, never writable) until the next dispatch |
| Project `.env`, `.env.*` (not `.env_template`), `.dvc/config.local` | read as empty (overlaid with `/dev/null`) |
| `writable:` entries of `config/sandbox.yml` | writable |
| `readable:` entries of `config/sandbox.yml` | read-only |
| Python runtime, ScieFlow source root, `uv`, the agent CLI's install root | read-only |
| Credential `bind` entries | read-only at `$HOME/<target>` (read-write with `writable: true`) |
| The agent's own token (`env` credentials) | only in its environment variable. The token file on the host is not bound |
| The real home directory and everything in it not listed above, including `~/.ssh`, `~/.gnupg`, `~/.hermes`, `~/.claude`, `~/.codex`, `~/.gemini` | absent (not bound) |
| `/home`, `/root`, `/mnt`, `/srv`, `/var`, `/opt` | absent unless granted |
| `/run/user/<uid>/bus`, `/run/dbus/system_bus_socket` | absent |
| The network | open, shared with the host |
| Process IDs | a private PID namespace (`--unshare-pid`) |

The agent CLI's own install root can lie under its credential store. For
codex it is `~/.codex/packages/.../releases/<version>`. That root is bound
read-only; `auth.json` is not inside it. An install tree that holds a
credential file, or a hard-linked file, is refused (exit 77).

Before this allow-list, the whole real `$HOME` was bound read-only. That is
no longer true: `$HOME` is absent unless a grant names a path inside it.

### What a dispatch can write

| Caller | May write |
|---|---|
| Sub-agent (every dispatch that ships today) | its own `workspace/<slug>/`, its agent home, and the `writable:` entries |
| Coordinator (no production caller yet) | the whole `workspace/` tree, plus the `writable:` entries |

Today `src/scieflow/core/agent_run.py` is the only production call site for
`sandbox.writable_for()`, and it hardcodes `coordinator=False`. Every dispatch
shipped today takes the sub-agent row.

Within its writable grants a dispatch also gets a private `/tmp`, and
`UV_CACHE_DIR` set to `<run>/.uv-cache` (`workspace/.uv-cache` for a
coordinator). The shared `~/.cache/uv` is not granted, because `uv` hardlinks
cache files into `.venv`: a dispatch that could write the shared cache could
change the host virtualenv in place. A per-run cache costs a few tens of
kilobytes and no package is re-downloaded. The run archive skips `.uv-cache`
as rebuildable noise, so it never reaches DVC.

Nothing sandboxed can write `src/`, `config/`, another run's directory, or the
real home.

## Bind order

The sandbox is built in this order, and a later bind overlays an earlier one:

1. The system set: `/usr`, the merged-usr links, `/etc`, `/dev`, `/proc`, a
   tmpfs `/tmp` and a tmpfs `/run`.
2. The resolved DNS config target, when it lies outside the system set.
3. Read-only roots: the project root, the Python runtime that dispatches (and
   the editable-install roots it sees), the ScieFlow source root, `uv`, and
   every `readable:` entry. A path already inside a bound root is dropped.
4. The agent and tool install roots, read-only, with each symlink hop
   recreated as a link.
5. Hidden paths: the project secret files (as `/dev/null`) and other runs'
   agent homes that exist when the layout is built (as tmpfs).
6. The writable grants: this run, and the `writable:` entries.
7. The run's `scratch` directory, bound onto itself so it is a mount point and
   cannot be renamed from inside. Then the masks: `provenance.git` and
   `scratch/agent-home` (tmpfs). Then this dispatch's own agent home, bound
   back on top of the mask.
8. Credential binds, last.
9. `--chdir` to the working directory, which must lie inside a granted path.
   `--unshare-pid` and `--die-with-parent` are set.

There is no `--ro-bind / /` anywhere. Without a layout the sandbox is the
system set plus the writable grants only.

## The environment

The environment is an allow-list. Every host variable not on it is dropped,
and the dispatch's environment is the exact dict passed to the process. Nothing
is passed through `bwrap --setenv`, so no value appears in an argv.

Kept:

- `PATH` — the directories of the bound launchers (only those outside the
  system set), followed by `/usr/local/bin:/usr/bin:/bin`.
- `HOME` — the dispatch's agent home.
- `LANG`, `LC_*`, `TZ`, `TERM`.
- `UV_CACHE_DIR` — the per-run cache above.
- `SCIEFLOW_STATE_DIR`, `SCIEFLOW_PROMPT_ARGV_LIMIT`, `SCIEFLOW_LEGACY_QUIET`,
  `SCIEFLOW_MAILTO`.
- The agent's own token variable, only when that agent has an `env` credential.

The parent directories bwrap synthesises to hold read-only launchers may be
writable inside the sandbox, but they are per-sandbox tmpfs: nothing persists to
the host or to a later dispatch.

Dropped, among others: `SCITE_TOKEN`, `DISCORD_*`, `HERMES_*`,
`SSH_AUTH_SOCK`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, proxy variables
(`HTTPS_PROXY` and similar), CA-bundle variables (`SSL_CERT_FILE`,
`NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`), the legacy spellings
`RESEARCHX_MAILTO`, `RESEARCHX_PROMPT_ARGV_LIMIT` and `WHATSNEW_DB`, and
`SHELL`, `USER`, `LOGNAME`. A host that relies on a dropped variable loses that
setting inside the sandbox. Python inside the sandbox does not receive
`PYTHONPATH`, so it resolves `scieflow` through the venv's editable install (see
[residuals](#residuals)).

## The agent home

Each agent gets a private home at `<run>/scratch/agent-home/<agent>/`, created
mode 0700 and kept across dispatches. Within one run, `claude --resume` and
`codex exec resume` keep working. Nothing from the real home is visible.

- The agent name must match `[A-Za-z0-9][A-Za-z0-9._-]*`.
- Every component from `scratch` down must be a real directory. A symlink is
  refused (exit 77) and never followed. A symlinked `run/scratch` therefore
  refuses every dispatch in that run.
- Another run's agent home is hidden from this dispatch if it exists when the
  dispatch's layout is built. A dispatch cannot plant files in a sibling agent's
  home. A sibling home created later, by a run that starts during this dispatch,
  is readable (read-only, never writable) until the next dispatch.
- The archive never packs an agent home (`scratch/agent-home`), whatever the
  archive's skip setting is. That covers the run archive, workspace size and
  the chats bundle. `.dvcignore` lists the same path.

## Credentials

A sandboxed agent needs its login. Each agent has an entry under `credentials:`
in `config/sandbox.yml`, keyed by agent name, with a `mode` and a `reason`.
A dispatch with no entry for its agent is refused (exit 77) with the exact
setup step. A dispatch whose entry names a token file that is missing is refused
(exit 77) with a message that names that file.

- `mode: env` — the token is read from a private file, one line, owned by you,
  mode 0600. The default is `~/.config/scieflow/credentials/<agent>.token`, or
  the path in `file:`. It is set only in that agent's process environment,
  under `var:`. It never goes into an argv, a job record, a transcript, an
  event, or any file ScieFlow writes.
- `mode: bind` — the agent's own login file is bound read-only (or read-write
  with `writable: true`) at `$HOME/<target>`. `target` is relative and may not
  contain `..`. The source may not be the home, `.ssh`, `.gnupg`, `.hermes`,
  the credentials directory, or a whole agent store directory. A single file
  inside a store is allowed, for example `~/.codex/auth.json`.

The token file is refused (exit 77) if it is a symbolic link, not a regular
file, hard-linked, owned by another user, readable by group or others, empty,
over 4096 bytes, or more than one line, or if it lies inside a grant or bind.
A refusal never prints the token.

Setup for Claude (active now, for `claude` and `claude-paper`):

```
claude setup-token
# save the printed token, one line, to:
~/.config/scieflow/credentials/claude.token
chmod 600 ~/.config/scieflow/credentials/claude.token
```

Then `scieflow agent run` for `claude` dispatches sandboxed. Until that file
exists, a `claude` dispatch is refused with exit 77 and a message that names the
missing token file. The setup steps above are printed only for an agent with no
`credentials:` entry.

The token is yours. Revoke it through the provider's own account controls at any
time. Deleting the token file stops sandboxed dispatches for that agent (exit
77). Revoking the token at the provider makes the agent's own login fail.

Further rules, checked at dispatch time:

- A credential is handed over only when the agent's CLI lies outside every
  writable grant of the dispatch. A CLI inside the run (a planted `./claude`)
  is refused.
- With a credential, an `env` prefix on the command may set only
  `MAX_THINKING_TOKENS`. `PATH`, `HOME` and `LD_*` are refused.
- On the conversational (turn) path, `session_cmd` and `resume_cmd` may not
  start with `env`; the turn is refused.

### Agents that refuse until a human adds an entry

These agents have no active entry in the shipped `config/sandbox.yml`. A
sandboxed dispatch of each is refused (exit 77) until one is added: `codex`,
`codex-paper`, `codex-review`, `agy`, and `stub`. Enabling one is the user's
decision, not an agent's.

- For `codex`, `codex-paper` and `codex-review`: uncomment the matching
  `bind` example in `config/sandbox.yml` (source `~/.codex/auth.json`, target
  `.codex/auth.json`) after reviewing it, and set its `reason:`.
- For `agy`: uncomment its `bind` example (source
  `~/.gemini/antigravity-cli/oauth_creds.json`).
- For `stub` there is no shipped example. Add an entry deliberately, or run the
  stub with `--no-sandbox`.

## readable: folders the agent may read

`config/sandbox.yml` can also list folders **outside** the repository that an
agent may read. A `readable:` grant is read-only: the folder appears at the
same path inside the sandbox, and writes to it fail. Each entry is a mapping
with a `path` and a `reason`. The block is commented out by default:

```yaml
readable:
  - path: ~/datasets/shared-exports
    reason: >-
      de-identified exports the analysis agents need to read.
```

- `path` is absolute, `~/`-prefixed (expanded against the real home), or
  relative to the repository root. It must exist.
- `reason` is required and must not be empty.
- Only the folder you list is granted. Its siblings stay absent.
- A `readable:` entry that lies inside a writable grant is refused (exit 77).
  Otherwise the writable bind would make it writable.
- An entry already covered by a bound root (the project, the Python runtime,
  `uv`) is dropped as redundant.

The hard deny list is never overridable. After symlinks are resolved, an entry
is refused with exit 77 and a `job.refused` event if it equals one of the paths
below or is an ancestor of one:

- the home directory, `/`, `/home`, and the home's parent;

For the rest of the list, an entry is refused when it equals, lies inside, or is
an ancestor of one of these:

- `~/.ssh`, `~/.gnupg`, `~/.hermes`, `~/.config/scieflow/credentials`;
- the agent credential stores `~/.claude`, `~/.claude.json`, `~/.codex`,
  `~/.gemini`;
- the runtime socket areas `/run`, `/var/run`, `/proc`, `/sys`, `/dev`.

The same deny table applies to bind sources and to install roots, with two
exceptions: a credential `bind` may name a file inside a store, and an install
root may lie inside a store only as a clean `packages/**/{versions,releases}/<v>`
directory that holds no credential file.

The checks run when the policy is read and again when each bind is emitted.

## Hidden paths: project secrets and other runs' homes

Two protections apply to every dispatch, whatever `readable:` says:

- Secret files in the repository root are hidden. Inside the sandbox they read
  as empty: `.env`, `.env.*` (but not `.env_template`), and `.dvc/config.local`.
  The host files are untouched. A symlink at one of these names refuses the
  dispatch (exit 77), because a `/dev/null` overlay would land on the link's
  target.
- Every other run's agent home that exists when the dispatch's layout is built
  is hidden (see [the agent home](#the-agent-home)).

A `writable:` entry that contains a hidden path is refused (exit 77), because
the writable bind would re-expose it. A `workspace/` that is itself a symbolic
link refuses every dispatch, since other runs' homes cannot be hidden in it.

User-owned files in `config/` (for example `config/remotes.yml`,
`config/notebooklm.yml`, `config/chats.yml`) stay readable. They are not
secrets the sandbox hides, and the agents use some of them (see
[residuals](#residuals)).

## Refusals

On the command line, a refused dispatch exits with code **77**. The other codes
are **75** (a spent `wall_minutes` budget) and **124** (timeout). On the web and
conversational-turn paths the same refusal is raised as a service error, with
the same `job.refused` event.

When the dispatch belongs to a run, a `job.refused` event with `reason: sandbox`
and a `detail` is written to that run's timeline, and the transcript and stderr
carry the same message. The causes are:

- **bubblewrap is missing**, or it is installed but did not confine the proof
  (see [Requirements](#requirements)).
- **The sandbox proof failed**: `verify()` found a secret visible inside, or a
  write that was not blocked, or could not place its marker (see
  [verify() and doctor](#verify-and-doctor)).
- **No run named.** A sandboxed dispatch must name the run it writes into. A
  one-off dispatch has no run, so this refusal carries no `job.refused` event,
  only the exit code and the message. Use `--no-sandbox` deliberately for that
  case.
- **The run's `config.yml` still has a `sandbox:` key.** The per-run opt-out
  moved to `config/sandbox.yml` (see [the escape hatch](#the-escape-hatch)).
  Delete the key from the run's `config.yml`.
- **A `writable:` entry is not a directory**, or is one of the forbidden entries
  (see [the allow-list](#the-allowlist)).
- **A `readable:` entry** is malformed, missing, denied, or inside a writable
  grant.
- **A hidden path** is a symlink, or lies inside a writable grant.
- **A launcher directory** that joins the sandbox `PATH` lies inside a writable
  grant. An agent could plant an executable there for a later dispatch.
- **A credential** is missing, has the wrong mode, is hard-linked or a symlink,
  or its CLI lies inside a writable grant. The missing-credential message names
  the exact setup step.
- **An `env` prefix** has options (`-i`, `-S`, `-u`, ...), or with a credential
  sets anything other than `MAX_THINKING_TOKENS`.
- **A component of the agent home** is a symlink, or a `scratch` directory
  lies outside every grant.

Fix the cause, or opt out on purpose with one of the escape hatches below.
Never edit around a refusal.

## The escape hatch

A dispatch can opt out of the sandbox deliberately, two ways:

- `--no-sandbox` on `scieflow agent run`, for a one-off dispatch;
- an entry under `unsandboxed_runs:` in `config/sandbox.yml`, for every
  dispatch inside one named run:

```yaml
unsandboxed_runs:
  - slug: 2026-09-23-some-run
    reason: >-
      why this run must dispatch without filesystem confinement.
```

Both the slug and the reason are required, and the slug names one run, not a
path. The list is validated like `writable:` and lives in the same file, for
the same reason: `config/` is never writable inside a sandbox.

!!! warning "The per-run hatch used to live in the run's own `config.yml`"

    It does not any more. `workspace/<slug>/config.yml` is *inside* the
    dispatch's writable bind, so `sandbox: off` there was something a
    confined agent could write for itself — and, since `agent_overrides:`
    in that same file can replace an agent's `cmd`, it could then choose
    what the next unconfined dispatch executed. A `sandbox:` key in a run's
    config is now never honoured; because a stale one would leave a run
    looking opted out when it is not, the dispatch is **refused** (exit 77)
    with a message naming this file. Move the opt-out here and delete the
    key.

Either way, ScieFlow emits a `sandbox.disabled` event on the run's timeline
(actor `human`, with `agent` and `why` naming which of the two it was) —
visible with `uv run scieflow run events <slug>`, and on the run page of
the local web app, where every job the sandbox did not cover is marked
`· unsandboxed` next to its state. Opting out is never silent.

## The allowlist

`config/sandbox.yml` grants writable paths beyond a dispatch's own run,
deny-by-default: delete an entry and that path goes back to read-only.
Every entry is a mapping with a `path` and a `reason`:

```yaml
writable:
  - path: config/journals
    reason: >-
      paper-review and paper-draft cache journal profiles here, the one
      documented exception to AGENTS.md rule 1
      (src/scieflow/research/AGENTS.md).
```

`config/journals` is the one entry that ships by default, because those two
research workflows write journal profiles there as a documented, narrow
exception to the "run artifacts only" rule — everything else an agent
writes still goes under its own run.

The loader (`sandbox.allowlist_paths()`) rejects an entry that:

- escapes the repository (`../outside`, an absolute path like `/etc`);
- names the repository root, `config/` or `src/` wholesale (`.`, `config`
  or `src` — the first two would grant the allowlist file itself, and
  `src/` is module code, which is never an agent's to write);
- names the allowlist file itself (`config/sandbox.yml`);
- is malformed — not a mapping, missing or empty `path`, `writable:` not a
  list, or the file itself not valid YAML or not a mapping at the top
  level.

A `writable:` entry that is an ancestor of a run is a human policy decision
with a known consequence (see [residuals](#residuals)).

Agents cannot edit this file to grant themselves more: `config/` is never in a
sandboxed dispatch's writable set, coordinator or sub-agent alike, so
extending the allowlist — or [opting a run out](#the-escape-hatch) — is always a
human decision made outside any run. That is what makes the `sandbox.disabled`
event's `actor: human` true.

## verify() and doctor

Before a sandboxed dispatch launches, `verify()` proves that the sandbox
confines it. It runs the proof in the dispatch's real layout: the same `wrap()`,
the same `Layout` and the same masks as the launch. The CLI, service and turn
paths all call it.

The proof requires four things:

- The control location is writable on the host. Otherwise an absence proves
  nothing.
- The sandboxed probe runs, and writes its structured result into a granted
  sentinel. A missing or malformed result refuses the dispatch.
- A forbidden write to a directory outside every grant is blocked.
- These are absent inside: `.ssh` and `.hermes` in each home (the `HOME`
  environment value and the passwd home), `/run/user/<uid>/bus`,
  `/run/dbus/system_bus_socket`, and a host marker. The marker is a file named
  `.scieflow-verify-marker-<tag>`, placed outside every bind. It goes in every
  qualifying home. When no home qualifies, it goes in the first qualifying parent
  of the binds. Each marker is created exclusively with mode 0600 and removed on
  every exit path.

A few limits are part of the design:

- The probe checks only the set above. The network, abstract-namespace Unix
  sockets and SysV IPC stay open (see [residuals](#residuals)).
- A nested sandbox started by an agent CLI is not probed.
- A mask whose path does not exist yet is left out of the probe. A tmpfs mask
  only hides content, so the probe sees a superset of what the launch sees, and
  it cannot hide a leak. Creating the mountpoint would leave a host directory
  behind.
- If no directory outside every bind can hold the marker, the proof is refused.
  That happens for a project outside the home directory or under `/tmp` (see
  [residuals](#residuals)).

Doctor: `python -m scieflow.core.sandbox doctor` runs the same proof on a
throwaway grant under `~/.cache/scieflow/`. It creates that grant and removes
it. It does not touch your project, so a pass does not prove a particular
project's binds. It then prints one status line per credential, from file
metadata only. It never opens, reads, hashes or prints a token. Its exit code is
0 only if the proof passed. `setup/doctor.sh`'s sandbox section calls this
entry, so a failing doctor means dispatches are refused.

## Requirements

```bash
sudo apt-get install -y bubblewrap
```

`setup/install.sh` checks for `bwrap` alongside the other tools. Presence
alone is not enough — a bubblewrap that is installed but not actually
confining (unprivileged user namespaces disabled, a container without the
right capabilities, …) is more dangerous than one that is simply missing,
because it looks fine until an agent writes somewhere it should not. Run

```bash
setup/doctor.sh
```

and check the `== sandbox ==` section. It confirms `bwrap` is on `PATH`, and
then runs the confinement proof described above. Only a passing proof counts.

## What it does not do

The sandbox confines filesystem writes, and it limits what an agent can read.
It does not confine the following:

- **Its own login.** The agent can read its own credential, the token or the
  bound login file. The sandbox cannot prevent that.
- **The network.** It is open. A sandboxed process can reach any host the
  machine can reach, and it can send what it reads.
- **What it can read.** The repository (including other runs' files and their
  provenance repositories), its own run, its own agent home, the `readable:`
  folders, and the system and install roots. Only the agent homes of other runs that exist when the
  dispatch's layout is built, and the project secret files, are hidden (see the
  timing residual below).
- **The agent's own evidence.** The agent writes its own evidence into its run
  (K16 H7, return-to item B3). The sandbox does not check it.

A hostile or compromised agent can still send anywhere on the network whatever it
can read, including its own credentials. The sandbox stops it from planting or
corrupting files outside its run; it does not stop exfiltration. Do not treat it
as more than that.

## Residuals

These are known and disclosed. Each is a limit of the current design, not a
fixed bug.

- **R1 — the agent reads its own credential.** Unavoidable, because the CLI needs
  it to log in.
- **R2 — agents of one run share writable content.** An agent can write things a
  sibling agent's CLI trusts, through `agent_overrides` in the run's `config.yml`
  (flags, model, templates, environment prefixes on non-credentialed dispatches),
  through project-level CLI configuration inside the run (for example
  `run/.claude/`), and through any run file a CLI reads on start. Through those,
  one agent can make another agent's CLI run code with that agent's credential in
  its environment. The planted-executable form and the env-prefix form are
  refused, and sibling homes that exist when the layout is built are hidden. A full fix conflicts with the K Task 8
  binding and is a human decision (S7). Not fixed here.
- **R3 — the network is open.** Loopback TCP services, abstract-namespace Unix
  sockets, and SysV IPC (`--unshare-ipc` is not used) are reachable or shared with
  the host. The probe does not check them.
- **R4 — the agent home is visible in the web app's run file browser.** The
  browser serves any path in a run, the agent home included, to the authenticated
  user. ScieFlow writes no token there, but a CLI may cache derived account data
  in its home. A follow-up candidate, B7, hides `scratch/agent-home` in the browser.
- **R5 — dropped environment variables.** Proxies, CA-bundle variables, the legacy
  `RESEARCHX_*` and `WHATSNEW_DB` spellings, and `SHELL`, `USER`, `LOGNAME` are not
  passed in. A host that relies on any of them loses that setting inside.
- **R6 — `/sys` is not bound.** A nested sandbox inside the sandbox (for example
  `codex --sandbox workspace-write`) is not probed. A CLI installed as an
  interpreter script whose interpreter lies outside the system set may fail to
  run, and that surfaces only when a real CLI is tested.
- **R7 — the agent writes its own evidence** (K16 H7, return-to item B3).
- **R8 — refusals until a human enters credentials.** `codex`, `codex-paper`,
  `codex-review`, `agy` and `stub` refuse with exit 77 until their `credentials:`
  entries exist. See [agents that refuse](#agents-that-refuse-until-a-human-adds-an-entry).
- **R9 — user-owned `config/*.yml` files stay readable.** `remotes.yml`,
  `notebooklm.yml` and `chats.yml` are readable because agents use them (AGENTS.md
  rules 11 and 12). Only `.env`, `.env.*` and `.dvc/config.local` are hidden.

Further residuals, from the S1–S5 reviews and fix rounds:

- **A sibling run's agent home created during a dispatch is readable.** A home that
  exists when the layout is built is hidden. One created later, by a run that starts
  during this dispatch, is readable read-only until the next dispatch. It is never
  writable.
- **A coordinator's workspace-root grant still sees every run's agent home.**
  Agent dispatches never get that grant, so this affects only a coordinator grant.
  The grant is not a dispatch layout.
- **A human `writable:` entry that is an ancestor of a run** lets an agent rename
  `scratch` above its pin. The entry is refused only while another run's agent
  home exists. It is a policy decision the user makes.
- **The agent can copy its own environment into the run proper** (outside
  `scratch/agent-home`). The token then lands in an archive. This is inherent to
  handing a credential to the agent.
- **The host marker lives in the real home while `verify()` runs.** It is one file
  per qualifying home, and it is removed on every exit path. A killed `verify()`
  can leave a stale `.scieflow-verify-marker-*` file behind. This is cosmetic: the
  file is not a credential and does not weaken the proof.
- **A project with no writable directory above it outside the sandbox binds
  (for example, a project under `/tmp`) cannot be dispatched sandboxed.** No
  directory outside every bind can hold the marker, so `verify()` refuses. `doctor` still reports ok, because it proves confinement on its own
  throwaway grant under `~/.cache/scieflow`. Move the project under the home
  directory, or opt out deliberately.
- **Python inside the sandbox imports the checkout's `src/` through the venv's
  `.pth`.** The environment allow-list drops `PYTHONPATH`, so a `scieflow` run
  inside the sandbox resolves through the editable install. Tests that run
  `scieflow` modules inside bwrap must set `PYTHONPATH` explicitly.
- **A store install root can still bind a renamed copy of a credential file.** The
  store check looks at file names and hard links. It does not check content.
  Conversely, an install tree that legitimately holds hard-linked files is refused.
- **A symlinked launcher is checked by its directory, not its final file.** A
  symlinked `uv` or agent CLI whose target lies in a writable grant, while its own
  directory is not, is not caught.
- **A credential `file:` may contain `..`.** It is a human-written entry.
