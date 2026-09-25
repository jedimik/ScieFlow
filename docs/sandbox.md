# The agent sandbox

## What it is

Every agent dispatch that goes through `scieflow agent run` — from the
terminal, or from the local web app, which calls the same service layer —
runs confined to the run it was given. `AGENTS.md` rule 1 always said that
run artifacts belong under
`workspace/<slug>/` and that a run never modifies module code under
`src/scieflow/`; that used to be a rule an agent had to follow on its own
word. It is now enforced by the process itself: the dispatch runs inside a
[bubblewrap](https://github.com/containers/bubblewrap) sandbox that cannot
write anywhere outside what it was granted, and a dispatch whose sandbox
cannot be proven is refused rather than run unconfined.

The only bubblewrap-aware code is `src/scieflow/core/sandbox.py`.

One dispatch path stays outside this boundary: the news module
(`src/scieflow/news/agents.py`) calls an agent CLI directly with
`subprocess.run`, on a prompt of its own making, with no run to confine it
to. It is narrower than a run dispatch — a fixed prompt, no run directory,
no `agent_overrides` — but it is not sandboxed, and nothing on this page
applies to it. Bringing it inside the boundary is future work.

## What is confined

Each dispatch gets a writable set built by `sandbox.writable_for()`, plus
whatever the [allowlist](#the-allowlist) grants:

| Caller | May write |
|---|---|
| Sub-agent | its own `workspace/<slug>/` — nothing wider |
| Coordinator | the whole `workspace/` tree |

The coordinator row exists for a coordinator launcher that a later milestone
will add. Today, `src/scieflow/core/agent_run.py` is the only production
call site for `sandbox.writable_for()`, and it hardcodes
`coordinator=False`: every dispatch shipped today takes the sub-agent path.

Both also get:

- a private `/tmp` (`--tmpfs /tmp`, invisible to the host and to other jobs);
- whatever `config/sandbox.yml` grants (see [the allowlist](#the-allowlist)).

`uv run` fails outright without a writable cache, so every dispatch needs
one — but it is never the host's. ScieFlow points `UV_CACHE_DIR` at
`.uv-cache` **inside the dispatch's own writable area** (its run directory;
`workspace/.uv-cache` for a coordinator). The shared `~/.cache/uv` is not
granted, because uv hardlinks cache files into `.venv`: a dispatch that
could write the shared cache would be writing the host virtualenv in place,
and the next `uv run`, `pytest` or `scieflow serve` on the host would
execute whatever it put there. A per-run cache costs a few tens of
kilobytes and about a tenth of a second on its first use; no package is
re-downloaded, because the existing `.venv` is still read from the
repository. The run archive already skips `.uv-cache` as rebuildable noise,
so it never reaches DVC.

Neither can write `src/`, `config/`, another run's directory, or `$HOME`.
Everything else under the repository stays **readable** — the sandbox binds
`/` read-only over the whole filesystem before layering the writable grants
on top — because agents need to read their own protocols, skills and
schemas to do their job at all.

## What it does not do

The sandbox confines filesystem writes. It does not confine anything else:

- **The network is unrestricted.** A sandboxed process can reach any host
  the machine can reach.
- **Credentials remain readable.** `~/.claude`, `~/.codex`, `~/.gemini` and
  similar credential stores are under `$HOME`, which is bound read-only —
  writable to nobody, but still on the readable filesystem, along with
  every agent CLI's own config.

A hostile or compromised agent can still send anywhere on the network
whatever it can read from disk, including its own credentials. The sandbox
stops it from planting or corrupting files outside its run; it does not stop
exfiltration. Do not treat it as more than that.

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

and check the `== sandbox ==` section: it confirms `bwrap` is on `PATH`
*and* runs a real sandboxed process that must write into a granted
directory and must fail to write into `$HOME`. Only both together count as
a pass.

## When a dispatch is refused

If the sandbox cannot be built, or cannot be proven to confine, ScieFlow
refuses the dispatch rather than run it unconfined:

- exit code **77**, alongside the existing **75** (a spent `wall_minutes`
  budget) and **124** (timeout);
- when the dispatch belongs to a run, a `job.refused` event is written to
  that run's timeline with `reason: sandbox` and a `detail` describing what
  went wrong;
- the transcript file and stderr both carry the same message.

A dispatch with no owning run has no timeline to write to, so for that cause
(below) there is no `job.refused` event — the refusal is reported only
through the exit code and the transcript/stderr message.

The causes are:

- **bubblewrap is missing** — install it (`sudo apt-get install -y
  bubblewrap`) and re-run.
- **bubblewrap is present but did not confine the proof write** — see
  [Requirements](#requirements); something about the host or container is
  disabling the confinement bubblewrap normally provides. The proof write
  targets a directory that is outside every writable grant and writable
  here: `$HOME` normally, and the nearest enclosing directory that
  qualifies when `$HOME` does not — which is what lets a coordinator that
  is itself sandboxed, and so sees a read-only `$HOME`, still dispatch. If
  no such directory exists, the dispatch is refused too: a control that
  cannot fail proves nothing.
- **the run's `config.yml` still carries a `sandbox:` key** — the per-run
  opt-out moved to `config/sandbox.yml`; see
  [the escape hatch](#the-escape-hatch).
- **a `writable:` entry names something that is not a directory** — fix the
  entry in `config/sandbox.yml`.
- **the dispatch's prompt belongs to no run** — a sandboxed dispatch must
  name the run it writes into; a one-off dispatch with no owning run has
  nothing to be confined to. This is raised before any run is known, so no
  `job.refused` event is emitted for it; the exit code and stderr are the
  only record. Use the [escape hatch](#the-escape-hatch) deliberately for
  that case.

Two fixes: install (or fix) bubblewrap, or opt out on purpose with the
escape hatch below — never edit around the refusal.

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

Both the slug and the reason are required, and the slug names one run —
not a path. The list is validated exactly like
[`writable:`](#the-allowlist) and lives in the same file, for the same
reason: `config/` is never writable inside a sandbox.

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

Agents cannot edit this file to grant themselves more: `config/` is never
in a sandboxed dispatch's writable set, coordinator or sub-agent alike, so
extending the allowlist — or [opting a run out](#the-escape-hatch) — is
always a human decision made outside any run. That is what makes the
`sandbox.disabled` event's `actor: human` true.
