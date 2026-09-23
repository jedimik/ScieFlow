# Agent sandbox — design

**Status:** design approved in conversation 2026-09-23; awaiting spec review before planning.
**Milestone position:** M2a (web foundation, merged) → **this** → M2c (control: answering gates,
starting runs, headless coordinators from the browser).

## Why this exists, and why it comes before the control milestone

ScieFlow dispatches agent CLIs unattended. `AGENTS.md` rule 1 tells them where they may write —
inside `workspace/<slug>/`, never into module code — but nothing enforces it. Today the rule is
honoured because the agents choose to honour it.

The control milestone will let a browser start a coordinator headless. Launching agents from a web
page, with no human watching the terminal, is the point at which "the rule is advisory" stops being
acceptable. So the boundary has to become real first. That ordering was the user's decision.

There is a second reason. ScieFlow's own `config/agents.yml` currently *disables* the protections
the vendors ship:

| Agent | Flag ScieFlow passes today | Effect |
|---|---|---|
| `codex`, `codex-paper`, `codex-review` | `--sandbox workspace-write` | Codex's own sandbox is on |
| `claude`, `claude-paper` | `--dangerously-skip-permissions` | Claude Code's guardrails off |
| `agy` | `--dangerously-skip-permissions` | off |

Those flags exist because unattended runs cannot answer permission prompts. The consequence is that
two of three agent families currently run with no filesystem restriction at all. This milestone
replaces that patchwork with one boundary ScieFlow owns and applies uniformly.

## Decisions taken (user, 2026-09-23)

1. **Shape:** one ScieFlow-owned sandbox, **filesystem confinement only**. Not vendor-by-vendor
   configuration, and not network egress control.
2. **When the sandbox cannot be established:** **fail closed**, with an explicit, recorded per-run
   escape hatch.
3. **Writable set:** the run, plus a **configurable allowlist** for documented exceptions.
4. **Scope:** sub-agent dispatches *and* coordinators. Experiment sweeps and sync jobs are excluded.
5. **Mechanism:** **bubblewrap only**, added to `setup/install.sh` and `setup/doctor.sh` as a
   requirement.

## Threat model, stated honestly

**What this stops.** An agent — whether confused, badly prompted, or acting on injected instructions
in a paper or web result — writing outside the run it was given: editing `src/`, rewriting
`config/`, touching another run's results, or scribbling into `$HOME`.

**What this does not stop, and will not pretend to.** Agents need their model API and their
credentials to authenticate. `~/.claude`, `~/.codex` and `~/.gemini` all hold live tokens and remain
**readable** inside the sandbox, and the network is **unrestricted**. A genuinely hostile agent can
therefore still read what it is allowed to read and send it to a host it is allowed to reach.
Closing that requires an egress proxy with a domain allowlist and credential scoping; it is a
separate, larger piece of work and is explicitly out of scope here.

This boundary is worth having anyway: it converts "the agent could destroy your repo" into "the
agent can only affect its own run", which is the failure mode that actually occurs in practice.

## Measured facts about the target environment

Everything below was probed on the development machine (WSL2, kernel 6.18.33.2), not assumed. These
measurements are load-bearing for the design.

| Probe | Result |
|---|---|
| `bubblewrap --version` | 0.9.0 (installed 2026-09-23 at the user's hand) |
| Write to the granted directory | allowed |
| Write to `$HOME` | **blocked** (`Read-only file system`) |
| Write to the repo's `src/` | **blocked** |
| Read the repo (skills, schemas, templates) | allowed — required, agents read their protocols |
| Read `~/.claude` | allowed — the documented limit above |
| Unprivileged user namespaces | work (`unshare --user --map-root-user --net`) |
| `uv run` with only the run dir writable | **fails**: `failed to open .../.cache/uv/sdists-v8/.git: Read-only file system` |
| `uv run` with `~/.cache/uv` also bound read-write | succeeds; `uv run scieflow run list` exits 0 |

The last two rows are why the tool cache is part of the writable set rather than an afterthought:
without it, every agent that runs a `scieflow` command fails immediately.

Not installed here, and therefore not usable as a mechanism: `firejail`, `podman`, `apptainer`
(note `setup/install.sh` checks for `apptainer` but only `singularity-ce` 4.1.1 is present — a
pre-existing discrepancy, recorded but out of scope). Docker is the Windows Desktop CLI, running in
a different VM.

## The boundary

| Process | Writable | Readable | Explicitly denied |
|---|---|---|---|
| Sub-agent (`scieflow agent run`) | its own `workspace/<slug>/`; a private `/tmp`; the tool cache; allowlist entries | the repository, its run, the system | `src/`, `config/`, other runs, `$HOME`, everything else |
| Coordinator | the whole `workspace/` tree; private `/tmp`; tool cache; allowlist entries | same | same |

A coordinator gets the workspace tree rather than one run because creating and steering runs is its
job; it still cannot reach module code, configuration or the home directory.

**How the two are told apart:** the caller declares it, rather than the sandbox guessing.
`sandbox.writable_for(project, *, run_dir: Path | None, coordinator: bool)` returns the set.
`scieflow agent run` passes `coordinator=False` and the run that owns its prompt file; the control
milestone's coordinator launcher passes `coordinator=True`. A sub-agent dispatch with no owning run
is refused rather than silently promoted to the wider set — a dispatch that cannot name its run has
no business writing to the workspace tree.

**The tool cache is named explicitly**, not inferred: `UV_CACHE_DIR` when set, otherwise
`~/.cache/uv`. It is bound read-write for every sandboxed process, because the probes showed
`uv run` fails outright without it.

Network access is unrestricted for both. Credentials remain readable for both.

## Mechanism

A new module, `src/scieflow/core/sandbox.py`, is the only place that knows bubblewrap exists. Its
surface:

- `available() -> bool` — is the mechanism present and usable?
- `wrap(argv: list[str], *, writable: list[Path], cwd: Path) -> list[str]` — the sandboxed argv.
- `verify(writable: list[Path]) -> None` — prove confinement; raise `SandboxUnavailable` otherwise.
- `SandboxError` / `SandboxUnavailable` exceptions.

The wrapper binds the filesystem read-only, then binds each writable path read-write over it, with a
private `/tmp` and its own PID namespace, dying with its parent. The shape validated by probe:

```
bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
      --bind <writable> <writable> [--bind ... ] \
      --chdir <cwd> --unshare-pid --die-with-parent -- <argv>
```

`--die-with-parent` matters: the job runner already kills a dispatch's whole process group on
timeout or cancel, and the sandbox must not become a way for a child to outlive that.

## Proving the sandbox works, every time

A sandbox that is configured but not confining is worse than none, because it is trusted. Before a
dispatch is allowed to proceed, `verify()` runs a throwaway probe **inside** the sandbox that
attempts a write to a path outside the granted set. If that write succeeds, confinement is not in
effect and the dispatch is refused.

This costs one extra short-lived process per dispatch, which is negligible beside an agent call
measured in minutes. It is what distinguishes "we passed the right flags" from "we watched it block
a write", and it defends against the mechanism changing underneath us — a new bubblewrap release,
a kernel or WSL change, a container without the required namespaces.

`setup/doctor.sh` runs the same probe standalone, so the check is available outside a dispatch.

## Fail closed, and the escape hatch

When `available()` is false or `verify()` fails, the dispatch is **refused** with exit code **77**
(`EX_NOPERM`), alongside the existing 75 for a budget refusal and 124 for a timeout — a refusal must
stay distinguishable from an agent's own failure. The error names the cause and the fix
(`sudo apt-get install -y bubblewrap`). A `job.refused` event records `reason: sandbox` so the
refusal appears on the run's timeline and in the web app rather than only in a terminal.

The hatch is deliberate and visible:

- `sandbox: off` in a run's `workspace/<slug>/config.yml`, or `--no-sandbox` on a one-off
  `scieflow agent run`;
- every unsandboxed dispatch emits an event, so the run's history shows it;
- `scieflow run show` and the web app's run page surface that the run is unsandboxed.

The hatch exists because a safety control that cannot be turned off gets worked around in ways
nobody records. Making it explicit and logged is safer than making it impossible.

## The allowlist

`config/sandbox.yml`, user-owned, deny-by-default:

```yaml
# Paths a sandboxed agent may write in addition to its own run.
# Agents cannot edit this file: config/ is never writable inside the sandbox.
writable:
  - path: config/journals
    reason: "paper-review and paper-draft cache journal profiles here (research/AGENTS.md)"
```

An absent file means no extra writes. Entries are relative to the repository root; an entry that
escapes the repository, or names `config/sandbox.yml` itself, is rejected at load.

The important property is structural rather than procedural: `config/` is never in any writable set,
so **an agent cannot grant itself more access**. Extending the list is always a human edit.

This resolves a real conflict in the current documentation. `AGENTS.md` rule 1 says all run
artifacts live in `workspace/<slug>/`; `src/scieflow/research/AGENTS.md` grants an explicit
exception for the journal-profile cache in `config/journals/`, which paper-review and paper-draft
both depend on. Without the allowlist, enforcing rule 1 would silently break those workflows.

## Integration

- `agent_run.prepare` computes the writable set from the dispatch's owning run and the allowlist,
  and reports it on the `Dispatch` it returns.
- `jobs.start` accepts the writable set and wraps the argv. Because it is the single place every
  dispatch passes through, nothing can route around the sandbox by accident.
- `service.dispatch_agent` passes it through unchanged, so the web app inherits the boundary without
  knowing it exists.
- Experiment sweeps and sync jobs are not wrapped: stage runs already execute under
  apptainer/singularity and legitimately need containers, GPUs and scratch space.

## Setup and documentation

- `setup/install.sh`: one `check bwrap "sudo apt-get install -y bubblewrap (required: agent
  sandboxing)"` line, in the file's existing pattern.
- `setup/doctor.sh`: bubblewrap present **and** the confinement probe passes — the file already
  performs functional checks of this kind (it compiles a trivial LaTeX document, pings agents
  headless), so this fits its established shape.
- New `docs/sandbox.md`: what is confined, what is not, how to read a refusal, how the escape hatch
  works, and how to extend the allowlist.
- `AGENTS.md` rule 1 gains a sentence: the boundary is now enforced, and a dispatch that tries to
  leave it fails rather than succeeding quietly.
- `docs/runs.md` and `docs/cli.md` gain cross-references.

## Testing

- **Unit:** writable-set computation for a sub-agent and for a coordinator; allowlist parsing,
  including the rejection of an escaping entry; argv construction.
- **Functional**, skipped when bubblewrap is absent so the suite stays portable: a real sandboxed
  process writes inside its run and is refused outside it; `uv run` succeeds inside the sandbox with
  the tool cache granted.
- **The self-check has to be tested by defeating it:** hand `verify()` a deliberately broken wrapper
  (one that does not confine) and assert it raises rather than passing.
- **Fail-closed:** with bubblewrap made to appear absent, a dispatch is refused and the event is
  recorded; with the escape hatch set, it proceeds and the event says so.
- **The headline test:** a sandboxed dispatch cannot write to `src/`. If one assertion survives from
  this milestone, it should be that one.

## Risks

| Risk | Handling |
|---|---|
| Bubblewrap behaves differently on another machine or kernel | `verify()` runs per dispatch, so a machine where confinement fails refuses rather than pretending |
| The writable set is too narrow and agents break in confusing ways | The `uv` cache case was found by probing and is already in the design; the first plan task re-runs these probes before anything is built |
| The allowlist erodes into a general permission grant | Entries require a reason; `config/` is never writable, so growth is always a human decision |
| An agent needs a path nobody anticipated | It fails closed with a clear error naming the path, which is the discoverable failure; the allowlist is the sanctioned answer |
| The escape hatch becomes the default | Every unsandboxed dispatch is an event on the run's timeline, visible in `run show` and the web app |

## Out of scope

Network egress control and credential scoping (the honest limits above); sandboxing experiment
stages; sandboxing user-invoked commands such as `scieflow chats` or DVC sync; and any change to the
vendors' own sandbox flags in `config/agents.yml`, which stay as they are — this boundary sits
outside them and does not depend on them.
