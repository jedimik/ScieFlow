# K — human-marked manager-harness command turns

Status: **PROPOSED — AWAITING USER APPROVAL**.
Design only; no implementation plan, harness enablement, or runtime approval is granted.
Evidence baseline: `0d7df13e5567ffba1bfe989765346079aff7de47`.
Source: ticket 16, especially its final 2026-10-04 correction (lines 85–112).
Paths below are relative to the repository; line numbers refer to that baseline.

## Decision and scope

K sends a human-marked command turn to the **selected manager harness holding the run's conversation**.
It is neither arbitrary shell nor a ScieFlow-subcommand allowlist, terminal emulator, or generic RCE endpoint.
Ticket 16's original shell/terminal alternatives and claims that typing admits uploads/promotions are superseded.
A harness may still invoke tools or expand a skill into consequential work: this remains security-critical.
K changes prompt ordering for one authenticated human submission, not the authority of its resulting actions.

The settled option C is message first, standing charter afterward for marked turns only.
Ordinary chat, module dispatches, generated opening prompts and manuscript merge turns remain charter first.
The charter remains present exactly once; budgets, gates, confinement and provenance protection remain operative.
The graphical application remains the primary interface; K covers verified harness features lacking a button.

Proposed UI: an unchecked “Harness command” checkbox beside the existing conversation composer, not a new pane.
Label the selected manager and support state next to it; reset the checkbox after submission and hand-over.
Show a command badge and actual composition order in turn history and the corresponding job detail.
Do not infer this mode from `/` text, a skill name, a pasted transcript, an agent reply or a file.
Submitting ordinary chat beginning with a slash continues to use ordinary prompt composition.
For marked input require the command at the first byte; refuse leading whitespace and NUL rather than normalize input.
Disable repeat submission while busy; the UI never automatically resends a request after a disconnect or ambiguous response.
Display that a recognized command may expand into a model prompt; this is not a terminal instruction channel.

## Current seams, not proposed APIs

| Existing seam | Evidence and consequence |
|---|---|
| Conversation storage | `src/scieflow/core/run/conversation.py:44–53,100–128`: `conversation.yml` has `agent`, `session`, `turns`; turns have `role`, `text`, `job_id`, `ts`, optionally final `state`. There is no command marker or composition field. |
| Selection | `src/scieflow/core/service.py:842–854,973–1018`: the picker uses enabled registry entries with session/resume commands and a registered family; hand-over clears the session, retains history and refuses an in-flight turn. This manager choice is not an `agent_config.ROLES` assignment. |
| Registry/run overlays | `src/scieflow/core/config.py:37–38`; `src/scieflow/core/agent_run.py:140–169,205–226`: selection reads the project registry, preparation applies owning-run overrides and optional role overrides. Capability checks must match the prepared effective harness, not merely the picker name. |
| Submission | `src/scieflow/web/pages.py:459–474` and `src/scieflow/web/api.py:180–207`: guarded POSTs reach blocking `service.say`; no stdin interaction endpoint exists. |
| Turn lifecycle | `src/scieflow/core/service.py:321–394`: checks precede recording, raw message goes to `logs/turn-*.md`, dispatch resumes the stored session, reply is parsed and recorded; prose can replace the transcript's raw JSON. |
| Composition | `src/scieflow/core/agent_run.py:97–115,230–233`: `compose_prompt` currently prepends the charter, or returns unchanged text when absent. K's explicit ordering argument is proposed, not present. |
| Invocation | `src/scieflow/core/agent_run.py:76–94,234–277`: `shlex` splits the configured template before prompt substitution; session/resume template wins over ordinary `stdin_cmd`; oversized unsafe conversational stdin is refused. |
| Session parsing | `src/scieflow/core/sessions.py:49–80,111–177`: family-specific session ids/text, tolerant partial output; `can_converse` proves parser/template availability, not slash-command support. |
| Jobs | `src/scieflow/core/jobs.py:30–48,141–182`: argv/cwd/state/logs are durable; stdin is one-shot prompt input or DEVNULL, never a PTY. No intent metadata exists today. |

Retain selected-harness-specific argv/stdin/resume behavior; never switch an oversized resumed turn to a one-shot command.
No login, passphrase relay, PTY, global harness settings editor, permission-mode control or trust bypass is introduced.
Keep the existing final parsed reply plus job link; raw job SSE is diagnostic JSON, not token-streamed conversation prose.
This distinction is documented at `docs/web.md:154–163` and implemented at `src/scieflow/core/service.py:379–394`.

## Historical and documentary capability evidence

`spike-findings.md` is unavailable; the ticket is historical summary evidence, not a recovered probe transcript.
Ticket 16:91–101 reports Claude `-p` dispatching installed skills when the message starts with the command,
trailing text tolerated, and an A/B control using `--disable-slash-commands`; `/help` was recognized but unavailable headless.
Ticket 16:108–112 reports unknown slash names falling through to model text, Codex stopping on repository trust,
and agy ordering not established. These observations do not establish support on the installed binaries now.

Documentary references carried forward from the 2026-10-08 proposal/review for design risk,
not freshly retrieved here and not local capability verification:

- S1: Claude Code, “Run Claude Code programmatically”, `https://code.claude.com/docs/en/headless`.
  Describes noninteractive `-p`, structured output and prompt-invoked forked skills; ordinary mode discovers host/project extensions.
- S2: Claude Code, “Extend Claude with skills”, `https://code.claude.com/docs/en/skills`.
  Describes argument substitution/appending, isolated forked skill contexts, tool allowances, model overrides and dynamic command injection.
- S3: OpenAI, “Non-interactive mode”, `https://developers.openai.com/codex/noninteractive`.
  Describes `exec`, repository requirements and distinct stdin patterns, not proof of K command parsing.
- S4: OpenAI, CLI command reference, `https://developers.openai.com/codex/cli/slash-commands`.
  Describes interactive composer shortcuts/queued input; those UI behaviors are not automatically available through `exec`.

Inference from S1/S2: moving the charter after a skill name can make it skill arguments rather than ordinary chat context.
A skill that consumes positional arguments, omits argument propagation, forks, changes model/effort, injects commands,
or loads hooks can defeat naive assumptions about charter reach, staffing or harmless prompt expansion.
The first enabled set should therefore be verified inline prompt skills whose composition preserves the charter;
exclude interactive-only built-ins, model/effort overrides and unverified forks/background behavior. Broader support needs separate evidence/review.
Typing a UI command into a headless prompt cannot be promised to open its UI or alter ScieFlow state.
S2 also documents stacked leading skill names: initial K admits one command with adapter-validated argument grammar,
refusing stacked/unverified invocations rather than validating only the first slash token. This must be tested after expansion.

Proposed fail-closed support policy: a small code-owned Claude-first policy with a finite named command set
and dated, freshly verified evidence for the effective harness version, invocation template and extension identity.
No initial names are certified by this documentation task; support stays disabled until authorized verification succeeds.
This is not a new persistent catalogue, plugin-discovery service, management UI or configuration key.
Do not infer support from `sessions.FAMILIES`, skill self-descriptions or agent-written files.
An effective template/version/extension mismatch disables supported mode until reverified, with visible help.
Claude is the first candidate, not enabled by this proposal; Codex and agy remain unknown/disabled until independently verified.
Unknown or misspelled names in marked mode are refused before dispatch, with an explicit “send as ordinary chat” alternative.
No silent fallback or automatic resend; a successful process exit is not proof that the harness intercepted the command.

Fresh verification before each harness is enabled must demonstrate first and resumed turns, argv and supported stdin,
actual command interception versus ordinary/disabled controls, unknown-name behavior, readable session continuity,
charter reach after skill expansion, extension/tool effects, and failure without broadening trust or permissions.
Use authorized, bounded fixtures/probes in implementation preparation; none were run for this documentation task.

## Human boundary, composition and durable audit

The existing authenticated page/API boundary accepts a proposed explicit mode enum: `chat` (default) or `harness-command`.
The route derives human origin from its guarded request, never from a client-supplied actor string inside text.
A marker and the accepted message are passed explicitly through the synchronous in-memory `say`, dispatch and preparation chain;
`compose_prompt` must not discover it in prompt files, YAML, slash text or environment variables.
Generated callers have no authority to opt into this mode; legacy records default to ordinary chat.
This makes K a human-originated dispatch instruction, not a global prompt-order toggle.

Preparation must consume that same accepted text, not validate it then silently reread an agent-writable prompt file.
The proposed seam passes the accepted text into preparation; prompt files are audit artifacts, not later input authority.
Verify the exact UTF-8 bytes entering argv/stdin against the accepted text plus the composed charter.
Preserve token boundaries and text without trimming, command rewriting or prefixing explanatory prose.
The proposed 64 KiB UTF-8 input limit applies to each marked message only, before dispatch; it is not a shared
ordinary-chat/draft/form-body cap. Charter expansion and existing safe argv/stdin checks remain separate.

Proposed new audit metadata: stable turn id, input kind, composition order, selected manager/family,
support-policy revision, message digest, charter version/digest and correlated job id on the human turn and job metadata.
Record the exact composed input once with the prompt/transcript artifacts; argv alone is insufficient for stdin cases.
Keep raw user text separate from composed input; do not prepend explanatory prose before the command.
For a marked turn the message precedes the existing charter header/separator; no charter means unchanged message.
Malformed charter data fails closed, as for ordinary dispatch; record the absent-charter case honestly.
The transcript/job page must explain command-first versus charter-first even when the reply transcript becomes prose.

Run files are writable by the dispatched agent; persisted mode/order/digests/job linkage are audit only.
Changing those records never grants command mode, enqueues work or causes replay. There is no persistent protected
intent/receipt store, one-use authorization database or automatic replay/queue in this first slice.
HTTP session/CSRF tokens and credentials never enter prompt artifacts, jobs or events.

Reuse `turn.sent`, `turn.received`, `turn.session_lost`, `job.*` and their structured data for correlation/mode.
`src/scieflow/core/events.py:19–42` declares a closed `TYPES` frozenset (with the existing `note.*` escape).
No new event type is needed or proposed; avoid arbitrary event names supplied by the harness.
Refusals with no dispatch are request feedback; actual sandbox refusals retain `job.refused`.

## Transport and loopback security

Extend `POST /runs/{slug}/say` and `POST /api/v1/runs/{slug}/conversation`; retain existing cancellation and SSE paths.
No websocket, terminal socket, raw-keystroke channel or new bidirectional transport is needed.
Existing session/CSRF checks are `src/scieflow/web/auth.py:53–75,110–126,133–181`;
bind restrictions are at `:35–46`. `src/scieflow/web/sse.py:21,93–111` guards streams by session.
Inspection of `auth.py` and `src/scieflow/web/app.py:27–85` finds no explicit request Host/Origin validation today.
Loopback bind, SameSite and absent CORS do not by themselves establish DNS-rebinding or Origin protection.

The first slice retains the existing token/session/CSRF contract and SSE session guards without weakening them.
It adds no universal Origin/Referer requirement, Host/tunnel allowlist migration or shared body-size middleware.
Malicious cross-site requests without the retained session/CSRF credentials must still fail; absence of Origin
alone is not a rejection criterion today. SSE keeps session authentication, not a new CSRF query token.
Loopback/DNS-rebinding risk remains unresolved, alongside same-origin compromise and hostile local processes.
Existing route inventory entries suffice for extended endpoints; add command-mode cases to `SAMPLES`/guard tests,
and add both inventories for any truly new unsafe route (`tests/web/mutating_paths.py:18–70`).
Keep new blocking handlers plain `def` (`tests/web/test_async_routes.py:57–70`).

### Separately bounded shared HTTP-hardening follow-up

Shared Host/Origin protection needs its own scope and approval, not an implicit prerequisite to option C or all of F+G.
Before adoption it must define a trusted configured public-origin/allowed-authority seam: `create_app(project, token)`
currently receives neither (`web/app.py:27`; `web/serve.py:29–39`). Decide different-port tunnel compatibility,
browser and cookie-plus-CSRF script-client migration, token-exchange/redirect behavior and SSE handling, with migration tests.
Trust must come from configuration, never forwarded headers or unvalidated request Host/Origin/Referer.
K's required independent pre-merge security review must evaluate the retained contract and residual risk.
If the new feature is unsafe on those guards, enablement blocks pending separately approved hardening;
compatibility is not a reason to weaken a guard or silently expand this first slice.

## Confinement, concurrency, cancellation and resume

`src/scieflow/core/service.py:210–258` guards budget, verifies confinement, tracks jobs/spend and writes transcripts.
`src/scieflow/core/agent_run.py:267–273` requests `coordinator=False`: a conversation does not acquire the workspace-root grant.
`src/scieflow/core/sandbox.py:56–73,96–105,350–362` masks the owning run's `provenance.git` after writable binds.
Keep that narrowed grant and mask for commands; never switch to the broader coordinator grant because the UI calls it a manager.
Existing project sandbox opt-outs can produce `writable=None` (`agent_run.py:180–198`); K must refuse that degraded case.
No K control edits sandbox policy, enables remote access, answers a human-only gate or bypasses ScieFlow approvals.

Permission preservation is literal: baseline Claude `session_cmd` and `resume_cmd` already include
`--dangerously-skip-permissions` (`config/agents.yml:19–20`, also ordinary templates at `:16–17`).
Do not claim every harness currently prompts for tools. Preserve the current configured permission mode;
K introduces no model/effort/tool/permission escalation and no silent template change to make a command work.
ScieFlow approval gates and filesystem confinement are separate controls, not harness permission prompts.

Today `_turn_in_flight` is only a job scan and explicitly has a race (`service.py:271–291`);
`store.update_yaml` locks one read-modify-write, not the whole turn (`src/scieflow/core/store.py:39–44,77–84`).
Proposed first slice: one admitted conversational turn per run, no queued chat/commands and no injection into a running process.
Use one run-scoped admission boundary shared by sends and hand-over: serialize selection/session checks,
reservation and launch so another request cannot pass the check before the first job is recorded.
Use a dedicated cross-process run admission lock, distinct from YAML/store locks, acquired without queueing;
hold it through the synchronous turn's session/reply finalization, closing the gap after process exit and before session recording.
Derive its identity from the canonical project/run, not client text or writable audit records. Its file and parent
must be outside every effective agent writable grant or protected by verified confinement; an ordinary lock file
inside the writable run is insufficient. Check overrides/allowlists and refuse marked admission if protection
cannot be proved. Same-UID mode bits alone are not protection; this adds no receipt store or replay authority.
It reserves admission, not prompt authority; existing job-state checks still refuse a surviving running job after lock-owner loss.
Concurrent sends/hand-overs fail visibly as busy. Disable repeat UI submission while busy and never auto-resend.
This is race prevention for admitted turns, not exactly-once execution, durable idempotency or restart-proof retry suppression.
Re-check manager enabled/session capability, session revision and command support at admission; stale forms fail rather than target a newly selected harness.
Locks for store updates remain short; do not hold them while waiting on a model or gate.
Only the dedicated admission reservation spans the turn; cancellation can bypass it so the human can stop a busy turn.
Other dispatches remain governed by their existing resource locks; K grants no way around a campaign/preview lock.

Cancel through existing `jobs.cancel`, terminating the process group (`jobs.py:185–237`); cancellation bypasses turn admission,
not authentication. Preserve partial output, final state and measured spend; do not retract a command already partly executed.
A disconnected tab does not cancel a job; reopening reads its durable state. Never automatically replay a lost/cancelled turn.
On restart use existing job reconciliation (`jobs.py:240–249`) and recorded job/turn evidence to show uncertainty,
not reconstruct or replay command authority. If a prior submission's outcome is uncertain, show its known state/linkage
and require an explicit human decision before another send; do not label work unexecuted merely because a response was lost.
No claim is made that audit records can prove duplicate suppression after a crash or lost acknowledgement.
Resume is a new explicit submission using a usable stored session; missing/uncertain session recovery is governed below.
Changing manager clears the session. Failed command recognition never triggers an unmarked automatic fallback.

## Output and degradation

Render command, audit labels, reply and diagnostics as escaped text; keep Jinja autoescaping and DOM `textContent`.
Prior art: `src/scieflow/web/templates/run.html:184–206`, `src/scieflow/web/templates/job.html:11–22`, `tests/web/test_conversation_page.py:45–52`.
Never use `|safe`, HTML interpretation, terminal hyperlinks, cursor actions, clipboard escapes or executable Markdown.
Proposed display limits: 200,000 characters per output/error panel, 16 KiB per SSE line, 200,000 live DOM characters;
show explicit truncation and a safe artifact link rather than silently dropping content.
Strip complete ANSI CSI/OSC sequences; visibly escape residual controls (except newline/tab) and directional formatting controls.
Chunked escapes/long unterminated lines must be bounded before accumulating in SSE/parser memory.
These are proposed caps: `pages.py:537–541` currently slices after reading the whole log and has no truncation notice;
`sse.py:67–86` reads unbounded chunks/pending lines, and `sessions.parse` currently receives whole transcripts.
For K's presentation/parser path propose 64 KiB read chunks and a 2 MiB aggregate parse-read budget per completed turn,
with bounded partial-line/JSON accumulation (16 KiB per line); oversized records yield an incomplete parse, not guessed data.
Bound reads before decoding/materializing output. K must bypass today's whole-log reads in `service.py:249,379`;
use streaming artifact copying/reference plus bounded parsing, not full-transcript materialization followed by truncation.
Oversized/partial output is an honest incomplete-read/parse state alongside the unchanged process/job state.
Keep safe, authenticated access to the retained raw log, with bounded escaped viewing or an artifact download;
never render its contents as HTML. Process success alone does not prove command interception or a complete parsed reply.
If bounded parsing cannot establish a usable session id, show session-recovery-required and block ordinary automatic
continuation. A previously established id may be retained only when the verified family recovery rule establishes it
is still usable; otherwise require an explicit human choice to recover or deliberately start a new session/hand-over.
Never silently replay, silently start a new session, or describe completed work as unexecuted due to parse limits.

Shared stdout/stderr file capture and job lifecycle semantics stay unchanged: no byte-triggered process kill is proposed.
Ordinary chat, experiments, merges and other jobs acquire no hidden execution/capture/input limits from K.
Raw-log/disk growth is an existing residual risk, not solved by display caps. Any future capture/termination cap
requires a separately approved per-job policy with explicit partial-work/session semantics, not a UI truncation rule.
Sanitize display separately from retained exact audit/log bytes; no passphrase/credential input is supported at all.
Unknown capability, missing binary/auth/session, invalid input/charter, budget exhaustion, sandbox failure,
incomplete output/parse, timeout and cancellation each show the reason, job/turn linkage if created, and safe next action.
No retry-until-green, configuration fallback, model fallback, hidden permission change or login form is offered.

## Individual CLI-only rulings and related exclusions

| `docs/web.md:499–514` item | Ruling under K |
|---|---|
| Chats push/pull | Stays CLI-only. Human harness text is not execution of `scieflow chats`; no backup/restore initiation shortcut or browser passphrase exposure. Rule 16 and credential exclusions remain. |
| DVC uploads | Stays outside K. Harness input is not upload consent; rule 15 size/count/first-sync approval and explicit upload gates remain. |
| `--promote` | Stays CLI-only in the initial F slice. K is not `agent configure`, and typing harness text is not a staffing exception or gate answer. |
| Per-agent fields/promotions/demotions | K admits none as ScieFlow controls. F proposes structured supported field edits separately; promotions/demotions remain CLI-only pending that design's approval. |

Remote operations remain governed by `scripts/remote/remote.py`, user policy and Kerberos boundary (AGENTS.md rule 11).
No raw SSH, credential acquisition, remote-policy editing, archive upload or chats transport is enabled by a skill command.
Revise `docs/web.md` during implementation to describe K honestly, not to replace the CLI-only list with “use the terminal pane”.

## Acceptance, slices and approval boundary

Required acceptance cases before enablement/merge:

- Explicit human command first/charter last exactly once; ordinary slash chat and all generated callers remain charter first.
- Spoofed marker/actor in prompt, reply, YAML, skills or files cannot authorize command mode; old records stay chat.
- Preparation consumes the accepted marked bytes even if a prompt artifact changes; NUL/leading whitespace refuse unchanged.
- The 64 KiB marked-message validation leaves ordinary chat/draft forms and shared middleware limits unchanged.
- Durable mode/order/charter/job correlation survives reload, stdin, parsing, cancellation and missing session ids.
- Verified capability admits only named support; typos, changed templates/extensions/versions and unknown harnesses are disabled/refused.
- First/resumed argv/stdin tests retain the usable session; unsafe stdin refuses; expansion preserves the full charter exactly once
  without new model/effort/tool/permission escalation or changes to current permission templates.
- Racing sends/hand-overs cannot launch conflicting turns, including finalization; busy UI suppresses repeat submits.
  A dispatched sandboxed agent cannot unlink/replace the admission lock or its parent; unsafe effective grants refuse.
  Lost acknowledgements/jobs, disconnects and restart never auto-replay; uncertainty requires a human decision, not an idempotency claim.
- Budget exhaustion, normal gates, narrowed sandbox, provenance mask and refusal of unsandboxed K are exercised.
- Malicious cross-site forms, bad/missing session or CSRF, malformed forms, token exchange and unauthenticated SSE exercise
  the retained contract for both POST modes; valid cookie-plus-CSRF script clients and mutation inventory equality stay covered.
  Tests do not assert absent-Origin rejection or Host validation; the security review records residual rebinding risk.
- HTML/script, ANSI/OSC, controls, split sequences, huge lines/logs and partial JSON remain escaped, bounded and visibly degraded.
- K performs bounded reads without whole-transcript materialization; incomplete parsing preserves honest process state,
  retained-log access and safe session recovery. Display limits never terminate a job or silently cap other job kinds.
- Existing conversation/charter/merge behavior remains green; offline suite and `uv run mkdocs build --strict` pass.

High-level slices only: (1) approved human boundary and finite Claude-first support evidence;
(2) explicit intent/composition/audit/admission in the shared service; (3) composer/history/help and bounded existing output paths;
(4) compatibility documentation, acceptance coverage and independent security review. These are not an execution plan.
An **independent security review is a required pre-merge gate**, covering human authority, extension effects,
retained authentication/CSRF/SSE and residual Host/Origin risk, sandbox/provenance, accepted-byte integrity,
admission/uncertain outcomes, bounded output/session recovery and credential/permission exclusions.

User approval is still needed for this proposal, finite-command evidence policy, synchronous admission/
uncertain-outcome behavior and marked-input/display/parse cap defaults. Option C itself is settled, not reopened here.
No unnamed command is approved by accepting this design; the implementation plan must name candidate commands,
and enablement requires the fresh evidence and security review above.
Shared HTTP-hardening and any execution capture/termination policy are separate future approvals, not choices silently
adopted by approving K. No live probe or harness support has been authorized or established by this documentation revision.
Ticket 16 traceability: scope/sandbox/transport/prompts/CLI list/output/audit/concurrency are ruled above;
pane/unknown names/transcript order/per-harness degradation are explicit; slices and security gate are named.
Ticket completion requires explicit design approval and strict-docs verification; this proposal alone does not close ticket 16.
