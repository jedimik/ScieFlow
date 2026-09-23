# The agent sandbox

## What it is

Every agent dispatch — `uv run scieflow agent run …`, whether started from
the terminal or from the local web app — runs confined to the run it was
given. `AGENTS.md` rule 1 always said that run artifacts belong under
`workspace/<slug>/` and that a run never modifies module code under
`src/scieflow/`; that used to be a rule an agent had to follow on its own
word. It is now enforced by the process itself: the dispatch runs inside a
[bubblewrap](https://github.com/containers/bubblewrap) sandbox that cannot
write anywhere outside what it was granted, and a dispatch whose sandbox
cannot be proven is refused rather than run unconfined.

The only bubblewrap-aware code is `src/scieflow/core/sandbox.py`.

## What is confined

Each dispatch gets a writable set built by `sandbox.writable_for()`, plus
whatever the [allowlist](#the-allowlist) grants:

| Caller | May write |
|---|---|
| Sub-agent | its own `workspace/<slug>/` — nothing wider |
| Coordinator | the whole `workspace/` tree |

Both also get:

- a private `/tmp` (`--tmpfs /tmp`, invisible to the host and to other jobs);
- the tool cache — `$UV_CACHE_DIR`, or `~/.cache/uv` if that is unset —
  because `uv run` fails outright without a writable cache, and every agent
  that runs a `scieflow` command needs one;
- whatever `config/sandbox.yml` grants (see [the allowlist](#the-allowlist)).

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
- a `job.refused` event is written to the run's timeline with
  `reason: sandbox` and a `detail` describing what went wrong;
- the transcript file and stderr both carry the same message.

The causes are:

- **bubblewrap is missing** — install it (`sudo apt-get install -y
  bubblewrap`) and re-run.
- **bubblewrap is present but did not confine the proof write** — see
  [Requirements](#requirements); something about the host or container is
  disabling the confinement bubblewrap normally provides.
- **the dispatch's prompt belongs to no run** — a sandboxed dispatch must
  name the run it writes into; a one-off dispatch with no owning run has
  nothing to be confined to. Use the [escape hatch](#the-escape-hatch)
  deliberately for that case.

Two fixes: install (or fix) bubblewrap, or opt out on purpose with the
escape hatch below — never edit around the refusal.

## The escape hatch

A dispatch can opt out of the sandbox deliberately, two ways:

- `--no-sandbox` on `scieflow agent run`, for a one-off dispatch;
- `sandbox: off` in a run's `config.yml`, for every dispatch inside that
  run.

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
- names the repository root or `config/` wholesale (`.` or `config` —
  either would grant the allowlist file itself, defeating the point);
- names the allowlist file itself (`config/sandbox.yml`);
- is malformed — not a mapping, missing or empty `path`, `writable:` not a
  list, or the file itself not valid YAML or not a mapping at the top
  level.

Agents cannot edit this file to grant themselves more: `config/` is never
in a sandboxed dispatch's writable set, coordinator or sub-agent alike, so
extending the allowlist is always a human decision made outside any run.
