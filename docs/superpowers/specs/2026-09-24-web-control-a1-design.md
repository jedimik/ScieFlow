# Web control, part A1 — run control and the coordinator conversation

**Status:** design approved in conversation 2026-09-24; awaiting spec review before planning.
**Position:** M2a (read-only web app, merged) → M2b (agent sandbox, merged) → **this**.

## Why this exists

The web app today is a viewer. You can watch runs, read artifacts and follow a live job log, but
every action — starting a run, answering a gate, marking a phase, changing who does what — still
happens in a terminal. That was deliberate: the read-only milestone deferred control until the
sandbox existed, because letting a browser start agents without filesystem confinement was the thing
worth being careful about. The sandbox now exists and is verified, so the deferral has expired.

The stated goal is larger than this milestone: **everything currently done through the CLI should be
possible in the browser.** That is 48 commands across eight groups (`run` 10, `chats` 8, `experiment`
7, `news` 7, `gate` 5, `workspace` 4, `research` 4, `agent` 3). This spec covers the first and most
expensive part of that programme, because it establishes the patterns every later page reuses.

## The programme this belongs to

| | Scope | Sequence |
|---|---|---|
| **A1** | runs, gates, agents, coordinator conversation | **this spec** |
| **C** | draft workbench — compare, select, merge, edit manuscript drafts | next |
| **D** | manuscript git sync — pull/push to GitHub/GitLab | with C |
| **B** | explorer — run history, artifacts, images, HTML reports, DVC-archived runs | after C |
| **A2/A3/A4** | experiments, research, workspace/chats/news pages | after B |
| **E** | container sandbox backend for macOS and native Windows | when those machines are needed |

The user works on Windows + WSL today, where the existing bubblewrap sandbox applies. macOS and
native Windows need a container backend (E) because bubblewrap is Linux-only; that is scheduled, not
skipped, and A1 does not depend on it.

## Decisions taken (user, 2026-09-24)

1. **Interaction:** both a structured launch form *and* a conversation — a wizard to start a run, and
   a chat panel to intervene while it runs.
2. **Execution model:** job-per-turn. Every agent interaction is a sandboxed job; chat turns are
   resumed sessions. Not a long-lived process, and not an embedded terminal.
3. **Scope of the CLI replacement:** eventually all of it; A1 covers runs, gates and agents.
4. **The run charter:** agreed plans are saved durably and re-stated to the agent on every turn,
   because long conversations drift from the original goal.
5. **Charter authorship:** both — the coordinator may propose a plan for approval through a gate, and
   the user may write and pin a charter directly.
6. **Platform:** Windows + WSL now; macOS and native Windows later, via E.

## Execution model

A run's conversation is a sequence of jobs. There is no second execution path.

- **Starting a run** creates the workspace, then launches the coordinator as a sandboxed job with a
  composed prompt. ScieFlow records the agent's session id on the run.
- **Each message** composes a new prompt and dispatches a *resumed* session as another sandboxed job.
- **The chat panel** is a view over those transcripts, ordered by the existing event timeline.

Everything the previous two milestones built keeps its meaning: each turn is confined by the sandbox,
each turn's wall time counts against the run's budget, each turn appears as `job.started` /
`job.finished`, and cancelling a reply cancels that job. Switching which agent handles the
conversation mid-run is simply dispatching the next turn to a different agent — the same mechanism
the draft workbench will need for swapping the merging agent.

**Session resumption is available on both agent families** (verified against the installed CLIs):

| Agent | Capture | Resume |
|---|---|---|
| `claude` | `-p --output-format=stream-json` | `--resume <session-id>` |
| `codex` | `exec --json` | `exec resume <SESSION_ID> <PROMPT>` (or `--last`) |

The exact field carrying the session id differs per CLI and may move between releases, so the
implementation pins it with a test that runs the real CLI rather than trusting a parser written from
documentation. An agent whose CLI cannot report a session id cannot host a conversation; the app must
say so plainly rather than silently starting a fresh context each turn.

## The run charter

A run gains a durable, versioned record of what has been agreed: the original goal, plus every plan
adopted during the conversation.

- The coordinator may **propose** a plan; adopting it is a gate, so the decision is recorded with an
  actor and a timestamp like every other approval.
- The user may **write and pin** a charter directly, without a proposal.
- **Every subsequent turn's prompt opens with the current charter.** This is the point of the
  feature: because ScieFlow composes each turn's prompt, the agent is handed the goal again every
  time it speaks and cannot drift away from it.
- Versions are kept, so the user can see how the goal moved and revert to an earlier one when a
  conversation has wandered.

It is stored as run data like status and budget, appears on the timeline, and is readable through the
API.

**The charter and the draft workbench are the same primitive**: a human-curated document that becomes
part of the next dispatch's prompt. In the charter it is the agreed plan; in the workbench it will be
the passages kept from several drafts plus the user's own edits. Building the primitive properly here
— a curated, versioned document pinned into prompt composition — means C is that mechanism with a
different editor on top, not a second system.

## Pages

- **Dashboard** gains a *Start a run* action, and its open-gates list becomes answerable rather than
  informational.
- **Run page** becomes the working surface: the conversation, the charter beside it, gates answered
  inline, phase and budget actions, and jobs that can be cancelled.
- **Start** (new): a wizard over the existing workflow definitions collecting goal, budget, approval
  mode and staffing, then launching the coordinator.
- **Agents** (new): reuses the existing plan → diff → apply machinery, so changing who performs which
  role shows the diff before anything is written.

## Write-path safety

A1 introduces the app's first mutating routes.

- Every mutation goes through the service layer; no route reaches into core state directly.
- CSRF is already enforced centrally in the session middleware, so a new route cannot forget it.
- Agent dispatches remain governed by the sandbox and its fail-closed refusal.
- **The read-only test evolves rather than disappearing.** Today it asserts every route is a safe
  method — the headline guarantee of the read-only milestone. It becomes an inventory test: every
  non-GET route must be session-guarded and CSRF-protected, and the set of mutating routes is listed
  explicitly, so a new one cannot appear unnoticed.
- **Three actions stay deliberate rather than becoming buttons.** `chats push`/`pull` needs the user's
  passphrase and, by AGENTS.md rule 16, must never begin on an agent's initiative. DVC uploads can be
  gigabytes and the sync rule requires asking first. `--promote` exists to be an explicit tier
  exception. In the browser these are confirmations stating the consequence, modelled as gates when
  they belong to a run.

## Live updates

Nothing new. The SSE transport built for the read-only app already streams events and job logs: the
chat panel tails the current turn's job log, and the timeline tails events. A conversation needs
exactly the transport a live job log needed.

## Testing

- Every mutation is tested three ways: refused without a session, refused without CSRF, and actually
  performs its effect.
- **The charter's pinning is tested by falsification** — a test asserts that the pinned text appears
  in the next turn's composed prompt, written so that removing the charter from prompt composition
  makes it fail. This is the requirement most likely to rot silently; without such a test the only
  symptom is an agent losing the goal, months later.
- Session resumption is pinned against the real CLIs, so a field rename in a future release is caught
  by a failing test rather than by a conversation that silently restarts each turn.
- The mutating-route inventory test replaces the safe-method test, and must fail when a new mutation
  is added without being listed.

## Risks

| Risk | Handling |
|---|---|
| A CLI changes its session-id output between releases | Pinned by a test against the real CLI; failure is a red test, not a silent context reset |
| An agent family cannot report a session id | The app states that agent cannot host a conversation, rather than restarting context invisibly |
| The charter grows until it crowds out the turn's actual content | Versioned and editable; the user can trim or revert, and the pinned portion is visible beside the chat |
| Per-turn process startup makes the conversation feel sluggish | Accepted: a second or two against agent thinking time measured in tens of seconds; the alternative costs the sandbox, the budget and the timeline |
| Mutating routes widen the attack surface of a loopback app | CSRF centrally enforced, session required, inventory test, and dispatches still sandboxed |

## Out of scope

The rest of the CLI (experiments, research, workspace, chats, news pages — A2 to A4); the draft
workbench (C) and manuscript git sync (D); the run explorer and DVC-archived run browsing (B); the
container sandbox backend for macOS and native Windows (E); any change to network egress or
credential scoping, which the sandbox spec already documents as out of its scope.
