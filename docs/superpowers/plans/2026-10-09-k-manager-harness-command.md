# K Manager Harness Command Implementation Plan

> For agentic workers: use the installed Superpowers subagent-driven-development workflow, adapted to native Hermes task cards, with fresh Sol/High implementers, independent Astra/Ultra specification then security/quality review, and final whole-change review. Steps below use checkbox tracking. Repository approvals override generic commit instructions.

Status: REVISED PROPOSED IMPLEMENTATION PLAN — correction round 2, CORRECTED PROPOSAL FOR INDEPENDENT RE-REVIEW, NOT self-approved READY. Actual round-1 independent review closes original R1–R4 at plan-contract level ONLY and returns R5/DOC1 FINDINGS. Owner run35 selects the precise protection-only/target-only refinement and new erratum; only the pre-created independent re-review can close R5/DOC1. K-PLAN-C1 remains CLOSED as a human candidate/expected-behaviour decision. This documentation phase does not authorize implementation, live probes, certification, enablement, commits or integration.

Goal: add explicit human-marked command-first/charter-last turns to the selected conversational manager, without making the conversation a shell or letting agent-written records authorize commands.

Architecture: retain the existing guarded POST endpoints, synchronous service and job lifecycle. Carry accepted human text and intent explicitly in memory through preparation; reserve send/hand-over with one protected nonqueueing cross-process admission boundary through final recording. Keep capability fail-closed and raw artifacts separate from bounded parsing and sanitized presentation.

Tech stack: existing Python >=3.11 package, verified cloud Python 3.12 environment, FastAPI/Starlette, Jinja, YAML, subprocess jobs, pytest and MkDocs. No new dependency or permission-template change is proposed. Owner-selected K admission backend is Linux descriptor flock; unsupported-platform ordinary compatibility is explicit below, not a busy/unsafe-lock fallback.

Spec: RUN/approvals/k-manager-command-design.md, approved immutable SHA-256 `2fb5bdb47f16df073a2cd117daca2993f0d4eb25c8f34a41aa4a49b6e63b57ae`. Human gate `01M4FQ210HRETTNB36VJJ0SV22`, approved 2026-10-09T19:01:02+00:00, per RUN/approvals/k-fg-native-answered-readback-20261009.json. The snapshot's old PROPOSED heading is historical. That design approval permits this plan, not its execution.

## Source identity and notation

- ROOT = `/home/ubuntu/ScieFlow-claude-f2976f52`.
- RUN = ROOT/workspace/cloud-dev-tickets-20261008.
- W = RUN/scratch/ticket22-worktree: accepted, READ-ONLY, uncommitted Ticket22 source generation.
- P = RUN/scratch/k-plan-worktree: clean detached merged-base planning checkout; only this proposed plan is authored there.
- Both HEADs are `0d7df13e5567ffba1bfe989765346079aff7de47`. W's accepted five-file patch SHA-256 is `01a84ca9504c8faf4104d891be7f4508873b17ec013c940531a55c87b6f6deb9`.
- All `W/path:line` citations below mean the actual accepted source, not P's older service/test bytes. New files/symbols are explicitly PROPOSED; they do not exist today. Task numbers are dependencies, not permission to start.
- K = future RUN/scratch/k-feature-worktree at that same base, with an exact uncommitted copy of W's five-file patch ONLY AFTER G3 explicitly approves this local test-base preparation. No carry-forward occurs now. Verify base, patch equality, empty starting staging and actual imports with positive and deliberate wrong-source controls before feature RED. This is not branch integration/merge/commit or final relocation. Acceptance distinguishes unchanged carried Ticket22 bytes from K's incremental diff; never test P's older source as the accepted baseline.
- Mandatory owner ruling: RUN/reviews/k-owner-run32-disposition.md, SHA-256 `32dfcfd9ec4ab8eeb4c7e9c7a3713847b74debf6342bd44ae1c447eed16a9f8e`. Original draft remains byte-preserved at RUN/scratch/k-plan-run29-original.md, SHA-256 `b8b4801af5caa8192e8750807c1e0aff03c0d9dd47ee91822a4629bc96f1344f`; original author/review reports are historical evidence, not overwritten verdicts.
- Mandatory R5/DOC1 refinement: RUN/reviews/k-owner-run35-disposition.md, SHA-256 `8d5fb2e47208ce60cb91c8ba9d0007565aa3f7ecf06ad891b7c9d3b90c1b3727`. Round-1 plan remains byte-preserved at RUN/scratch/k-plan-run33-reviewed.md, SHA-256 `c066a6717730b066e0500b5d13a043ef11c3972694818a24f537fd342792928e`. Actual RUN/reviews/k-plan-r1-rereview.md is FINDINGS, not READY. Preserve sealed RUN/reviews/k-plan-r1-correction.md; its inaccurate old lines31/42/46 are superseded only by NEW RUN/reviews/k-plan-r2-correction.md, not worker instructions or changed limits.

## Global constraints

1. Mode is exactly `chat` / `harness-command`, default `chat`. Only an explicitly authenticated human-marked submission changes composition; ordinary slash chat, generated opening prompts, module dispatches and merge turns stay charter-first, charter exactly once.
2. The sole production CANDIDATE is raw `/scieflow-menu`, exactly that spelling, with no user arguments, trailing space/newline or second command. It is not certified. `/scieflow-k-probe` is a synthetic future mechanism fixture only. No built-ins, arbitrary skills, Codex or agy are supported.
3. Preserve exact accepted UTF-8 input; reject leading whitespace/NUL without normalization. Marked input limit is 64 KiB UTF-8. It is not a new ordinary-chat/draft/form-body limit; charter expansion and existing argv/stdin limits remain separate.
4. Preserve selected manager, effective owning-run overrides, first/resumed templates, stdin safety, narrowed `coordinator=False` grant, provenance mask, budgets, gates, session/CSRF/SSE guards and current harness permissions. Unsandboxed/degraded K refuses. Baseline Claude templates already skip permission prompts; do not claim every tool asks.
5. On supported Linux, ALL sends and hand-overs share one protected nonblocking cross-process reservation through final recording; cancel bypasses admission only, never auth. On unsupported platforms K refuses and existing ordinary chat/hand-over remains available WITHOUT the new admission guarantee. Supported-platform busy/unsafe/corrupt locks never take that compatibility branch. No queue, replay, durable exactly-once guarantee or receipt database.
6. Audit YAML, job fields, digests, events and prompt artifacts are agent-writable audit data, never command authority or replay instructions. Do not serialize HTTP tokens/credentials. A hostile same-origin client or local privileged process remains a residual threat.
7. Rendered panels and aggregate live DOM: 200,000 UTF-16 code units, including sanitation expansion/notices, never split a Unicode scalar. Job-log SSE wire line: 16 KiB UTF-8 including data-prefix/newline framing AFTER sanitation/encoding. Raw completed-record/partial-record/escape accumulator: at most 16 KiB BYTES; each read: at most 64 KiB BYTES; completed K parse: at most 2 MiB aggregate BYTES, K-only. These are separate units/limits, not a universal capture cap or a separately selected display-line cap. Raw copy/download has no new total cap: finite opened extent, bounded-memory streaming and mutation/incomplete checks. Existing web/files.MAX_INLINE = 200,000 BYTES is an inline-file threshold, not the UTF-16 display budget or a raw-copy/download limit. Bound before decode/materialization; show truncation/incomplete state and retained handle-safe raw access.
8. Sanitize presentation only: strip complete ANSI CSI/OSC sequences, visibly escape residual controls except newline/tab and bidi-format controls; Jinja autoescape / DOM textContent, never HTML/Markdown execution. Raw audit/log bytes remain exact.
9. Incomplete output preserves actual job state and requires explicit safe session recovery if usability is not established. No silent new session, unmarked fallback, retry-until-green or replay after disconnect/restart/timeout/cancel.
10. No hidden execution/capture/input cap on non-K work, byte-triggered kill, disk-capture redesign, global configuration toggle, permission change, login/PTY, upload/backup/promotion shortcut or scientific run. Shared HTTP hardening, raw-log disk growth and Host/Origin risk remain separate, explicitly reviewed issues.
11. Development staffing stays native `openai-codex`, coordination/review `gpt-6-astra` / Ultra, implementation/docs `gpt-6.1-sol` / High, no fallback. Every writer proves its own exact initialized runtime before edits. Do not change ScieFlow research staffing.

## Settled candidate decision and future evidence

K-PLAN-C1 — CLOSED AS A HUMAN CANDIDATE/EXPECTED-BEHAVIOUR DECISION, not a live certification. RUN/approvals/k-command-confirmation-20261009.md records “Yes it can. Proceed with next tickets” for exact raw no-user-argument `/scieflow-menu`, expected to complete finitely/noninteractively. RUN/approvals/k-implementation-intent-20261009.md already carries express go-ahead. No repeat eligibility/go-ahead questionnaire, candidate substitution, skill rewrite/install or extra C1 consent is needed for planning/readiness.

G4/G5 later test the unchanged candidate: bounded completion without terminal/user input; actual interception versus ordinary/disabled controls; first/resumed argv and forced stdin; charter reach exactly once after expansion; invariant effective model/effort/tools/permissions; no unexpected forks/background work or workflow/configuration effects outside consent. A real failure returns an evidence-based finding and keeps support disabled, not an automatic rewrite/fallback/retry. No observed installed success/failure is asserted now. Skill/menu directives remain potential effects to test, not proof that human confirmation is invalid.

Discovery is not established by ROOT/skills existing or dependency installation (`W/setup/install.sh:8–35`). No harness inventory/login/version/model request is performed by this correction. `/scieflow-k-probe` and the inert protocol fixture cannot certify `/scieflow-menu` or a different deployment.

## Documentary provenance and limits

Carry forward only actually returned prior documentary observations: original preparation's public browser headless/skills sections and the review's valid final `scratch/checks/k-plan-review-run30-primary-docs-v5.json`; owner report's subsequent real browser Python os/fcntl and native Kanban sections. These inform proposals, not installed-platform/race/candidate verification. Owner report explicitly WITHDRAWS its initial premature web.run attribution. The prior review's static initial attribution and primary-docs-v3/v4 unsupported web.run receipts remain INVALID, preserved and not evidence. This correction makes no fresh documentary-retrieval claim. The missing historical spike transcript is not reconstructed. Official headless/skills behavior and safe descriptor APIs do not replace G4/G5 exact-identity evidence; no template/bare-mode/permission workaround is selected.

## Current source map

| Responsibility | Real source seam / regression anchor |
|---|---|
| Template splitting, composition, override resolution | `W/src/scieflow/core/agent_run.py:76–115`, `W/src/scieflow/core/agent_run.py:118–169`, `W/src/scieflow/core/agent_run.py:201–277`; existing prepare/owning_run_workspace/load_prompt_override read an existing prompt and build Dispatch without command metadata; new resolution/composition interfaces below are PROPOSED. |
| Charter single-read snapshot | `W/src/scieflow/core/run/charter.py:36–90`; use snapshot once for text/version/digest, retaining current effective stripped charter text semantics. |
| Service lifecycle / race / generated opening | `W/src/scieflow/core/service.py:210–258`, `W/src/scieflow/core/service.py:271–394`, `W/src/scieflow/core/service.py:1003–1018`, `W/src/scieflow/core/service.py:1069–1080`; registry-only checks/parser must not be mistaken for effective prepared capability. |
| Target-only selection / pre-create compatibility | `W/src/scieflow/core/service.py:973–1000`, `W/src/scieflow/core/service.py:1043–1080`, `W/src/scieflow/core/sessions.py:159–177`; _check_can_converse loads registry and checks target known/enabled/two templates/registered family only; start_run prechecks before creation, then hand-over and ordinary say, preserving RunStartedError after creation. |
| Conversation and events | `W/src/scieflow/core/run/conversation.py:44–128`, `W/src/scieflow/core/events.py:19–55`; no command schema/revision/annotate API exists today. |
| Short state locks / state override | `W/src/scieflow/core/store.py:39–84`, `W/src/scieflow/core/project.py:63–74`; store locks queue for 30 seconds and live beside writable YAML. state_dir has an environment override. Neither is already a protected admission boundary. |
| Jobs / cancellation / loss | `W/src/scieflow/core/jobs.py:30–75`, `W/src/scieflow/core/jobs.py:141–182`, `W/src/scieflow/core/jobs.py:185–249`; output is streamed to files; stdin one-shot/DEVNULL, no PTY. |
| Actual sandbox grants and masks | `W/src/scieflow/core/sandbox.py:56–105`, `W/src/scieflow/core/sandbox.py:146–214`, `W/src/scieflow/core/sandbox.py:243–308`, `W/src/scieflow/core/sandbox.py:311–362`, `W/src/scieflow/core/agent_run.py:180–198`; opt-out is run-slug/policy based and grants derive from project/run/coordinator, not manager. Old public helpers load policy separately; NEW TurnProtection producer factors pure rules behind compatible helpers, capturing policy once for admitted paths. No safe admission-root resolver exists today. |
| Parser and ordinary fallback | `W/src/scieflow/core/sessions.py:22–80`, `W/src/scieflow/core/sessions.py:111–177`; receives full strings, not bounded streams. |
| Guarded routes | `W/src/scieflow/web/pages.py:459–474`, `W/src/scieflow/web/api.py:180–207`, `W/src/scieflow/web/auth.py:53–126`, `W/src/scieflow/web/auth.py:133–181`; plain def remains essential. |
| Diagnostic read/stream/artifact | `W/src/scieflow/web/pages.py:532–549`, `W/src/scieflow/web/pages.py:569–604`, `W/src/scieflow/web/sse.py:21–111`, `W/src/scieflow/web/files.py:28–94`; full read then slice and unbounded SSE pending are current gaps. |
| Presentation | `W/src/scieflow/web/templates/run.html:169–217`, `W/src/scieflow/web/templates/job.html:11–25`, `W/src/scieflow/web/templates/file.html:1–7`; JavaScript is inline here, NOT an existing static/app.js module. |
| Actual fixtures | `W/tests/core/conftest.py:73–96`, `W/tests/core/test_agent_run.py:623–775`, `W/tests/web/conftest.py:17–93`; tests use stub/Claude-shaped output, not a certified Claude command. |
| Regression surface | `W/tests/core/test_service.py:233–449`, `W/tests/web/test_conversation_page.py:19–106`, `W/tests/web/test_sse.py:46–79`, `W/tests/web/test_sse.py:136–290`, `W/tests/web/mutating_paths.py:18–70`. |
| Build and runner | `W/pyproject.toml:54–83`, `W/mkdocs.yml:50–52`, `RUN/tools/ticket22_pytest.py:15–36`, `RUN/tools/with_project_fixture_mount.py:14–60`; Ticket22 runner is hardcoded to W and is NOT a K source selector. |

## Owner-selected decisions — subject to independent re-review

RUN/reviews/k-owner-run32-disposition.md selects D0–D5; RUN/reviews/k-owner-run35-disposition.md selects the exact R5/DOC1 refinement, not six new human questions or feature consent. Original R1–R4 are independently CLOSED only at plan-contract level per actual round-1 re-review; R5/DOC1 remain for independent closure. Re-review reads the entire revised artifact, limits rework to these changes and fix-caused consequences, preserves prior coverage/closures, and returns a material conflict precisely rather than silently expanding scope. A structure/seal pass is not semantic accuracy or feature acceptance.

| ID | Selected contract | Evidence / cost if wrong |
|---|---|---|
| D0 | Future fresh K tree plus exact uncommitted Ticket22 patch only under explicit G3 local preparation; exact imports/negative source control; no current carry-forward or branch integration. | Source identity section; wrong base invalidates regression evidence. |
| D1 | Canonical project `.scieflow/turn-admission`, independent of SCIEFLOW_STATE_DIR; versioned hash of unambiguous canonical project/run encoding; stable inode; Linux descriptor `flock(LOCK_EX|LOCK_NB)`, separate open descriptions per entrant/thread, no unlink/soft lock/timeout queue. Neutral reservation precedes resolution; ONE independent sandbox.TurnProtection from the same captured policy drives opt-out/narrowed launch grants and full grant/alias/replaceable-ancestor/project-wide/coordinator-workspace protection. Shared typed validate_protection receives it directly on send and nonlaunch paths; no old-current/target dispatch pair. All supported-platform sends/hand-overs participate; unsafe/unconfined/corrupt/busy refuses visibly. Unsupported-platform K refuses but legacy ordinary chat/hand-over remains without the new guarantee; this branch NEVER handles supported-backend busy/unsafe. | Source store/project/sandbox; wrong choice permits replacement/duplicate launch. Mode bits alone do not prove protection; real child denial required. |
| D2 | Frozen guarded HumanSubmission; no-artifact ResolvedTurn → pure PreparedTurn → same-object launch, retaining the EXACT independent TurnProtection. Nonlaunch constructs protection without manager/session/charter/prompt/certification; validates TARGET only from one captured registry with private pure _validate_conversation_target. Persisted nonnegative selection_generation and canonical decimal revision; fixed audit-only schema and atomic K finalize_turn. Existing ordinary/generated signatures and pre-create target wrapper retain compatibility. | R1/R2/R5 ordered contracts below; wrong snapshot causes double preparation/spend/stale targeting or blocks first selection/recovery. Tokens/digests are not auth. |
| D3 | Empty finite code-owned certificate; exact raw `/scieflow-menu` only after G4/G5. Match agent/family/enabled, resolved executable and evidence-bound binary digest/version, both effective templates, model/effort, canonical cwd/discovery and finite reviewed non-secret extension/settings effects. Bounded local read-only safe identity readers only; no send/GET version/model command, secret inspection, dynamic catalogue or missing-field wildcard. Fixture and deployment identities stay distinct. | Human C1 closure is not evidence. Wrong identity changes effects under familiar text; missing/uninspectable/writable unpinnable effects remain unsupported. G4 still chooses actual cost/effect ceiling. |
| D4 | Authenticated deliberate new session via existing hand-over/reset, required expected_revision and explicit human confirmation; same-manager reset allowed even with session None, busy refuses. TARGET eligibility plus independent safe protection, not old-manager executability/charter/certification, governs nonlaunch recovery. Every successful hand-over including same-manager bumps generation; same-manager non-reset preserves session/uncertainty separately. Confirmed reset clears only current session/recovery and bumps even if session absent, retaining history/jobs. Different-manager recovery is explicit/revision-checked. No arbitrary replacement session id, guessed old-id reuse or replay. | Existing record_session(None) is NOT K recovery. Wrong uncertainty handling duplicates effects or makes valid recovery impossible; writable pending/finalized diagnostics are not exactly-once proof. |
| D5 | Selected shared scope: run job output/error, job-log SSE `_log`, conversation/history/command/audit/error/argv in run.html/job.html and existing authenticated file_view acquisition/consumption including inline text/raw download. Exact cap units in constraint 7. Separate `_events` run timeline gains only fixed bounded correlations, never command/charter/reply/log payloads; legacy event-history materialization is NOT universally bounded by K. | R4 stable handles below; wrong readers leak/hang/truncate or regress ordinary media/range/auth. No listing/upload/archive/export/server/middleware/capture/input redesign. Raw disk growth/Host-Origin risks remain. |

Ordinary compatibility costs are intentional and visible: Linux busy/unsafe admission can refuse ordinary sends/hand-overs; malformed generations cause integrity errors; shared display may truncate visibly; shared artifact acquisition refuses unsafe/nonregular races. Unsupported-platform ordinary behavior has no new lock guarantee. Independent re-review must disposition these boundaries, not assume universal cross-platform protection or universal filesystem hardening.

## Proposed interface and file map

All entries here are NEW or MODIFIED PROPOSALS, not descriptions of existing functions.

- Create `src/scieflow/core/turn_input.py`: frozen `HumanSubmission(message: str, mode: Literal['chat','harness-command'], selection_revision: str | None)`; `accept_human_submission(message, mode, selection_revision) -> HumanSubmission` only in guarded normal page/API paths. None revision is legacy chat only. Downstream requires message equality and conversational use; actor/slash/environment/stored fields never synthesize intent. No cookies/tokens in this object. `validate_marked_input(message: str) -> bytes` separately checks exact UTF-8, leading whitespace/NUL/64 KiB; production candidate grammar is stricter.
- Create `src/scieflow/core/harness_commands.py`: frozen `HarnessIdentity`/`SupportStatus`, `validate_candidate_raw(message) -> None`, pure `effective_identity(resolved) -> HarnessIdentity` projecting ALREADY captured safe inputs, pure `support_for(identity) -> SupportStatus`, empty `CERTIFIED_POLICIES`. Bounded local non-secret identity I/O occurs only in resolve_turn (or independently bounded UI projection), never inside pure prepare_turn; Task1 uses frozen synthetic input records until Task2 supplies integration. No mutable cfg/later registry reread. Store code-owned revision/dated evidence, binary/version binding, both templates, staffing/cwd/discovery/extension fingerprints; missing fields unsupported, not wildcard. Tests inject synthetic support locally only.
- Modify existing `src/scieflow/core/sandbox.py`: NEW deeply immutable `TurnProtection` and `resolve_turn_protection(project, *, run_dir) -> TurnProtection`. Producer requires only trusted canonical project/run plus shared sandbox policy/path inputs, NOT manager/session/message/prompt/mode/charter/certification. Validate/load sandbox allowlist ONCE; factor existing pure opt-out/grant validation rules behind compatible public helpers, deriving both from the SAME captured mapping. Freeze canonical project/run/workspace identities, narrowed run-plus-allowlist grants (`coordinator=False`), provenance masks, complete project-wide allowlist/coordinator-workspace coverage, aliases/replaceable-ancestor facts and safe policy/path validation fingerprints; no wrapped mutable dict/list or runtime environment/credentials. Reservation alone owns lock descriptors/inode identity; protection identifies corresponding project/run facts. No new catalogue/store or persisted command authority. All conversation managers use this one policy-derived envelope; no per-agent grant API or missing-old-manager omission.
- Modify `src/scieflow/core/agent_run.py`: NEW immutable `ResolvedTurn` with effective agent/family/templates/model/effort, canonical run/cwd/current session, timeout, immutable `protection: TurnProtection`, frozen environment launch inputs, safe identity/fingerprints and one charter snapshot. NEW `resolve_turn(project, agent, *, run_dir, session, cwd=None) -> ResolvedTurn` validates explicit canonical structural owning run without prompt_file. Called once INSIDE held reservation, it invokes resolve_turn_protection ONCE and stores that exact value; launch grants/masks derive from protection, not another loader. Factor override mapping/regular-file/control-pair validation into shared run-based loader; old file-based prepare retains strict existing-prompt/unique-owner behavior. Read effective config/overrides once, preserve stale in-run sandbox-key refusal from captured config, and use `charter.snapshot` once for stripped text/version/digest. No second policy/config/charter resolution, no mutable history/config dicts, no secrets/environment serialized into audit. Nonlaunch does not use this full resolver or read run config/charter for protection.
- NEW pure `agent_run.prepare_turn(resolved, submission) -> PreparedTurn`: immutable exact accepted/composed bytes, composition/charter/audit metadata, tuple-valued argv and immutable identity/environment launch fields; `prepared.protection is resolved.protection`. Never wrap mutable Dispatch by reference. Pure composition/capability decision uses only resolved safe inputs, first/resume/stdin safety, no file artifact/reread. Existing `prepare`, `compose_prompt`, Dispatch construction and generated/role callers stay compatible, charter-first; no command-authority argument added to a file-based generated path.
- Create `src/scieflow/core/turn_admission.py`: `reserve(project, ws)` context returning reservation with exact typed `validate_protection(protection: TurnProtection)`; NEW AdmissionBusy/AdmissionUnsafe. Neutral canonical reservation comes BEFORE effective resolution. Send explicitly passes prepared.protection (the same resolved.protection); nonlaunch explicitly passes ONE independently constructed TurnProtection, never a full/dummy ResolvedTurn/PreparedTurn or marked intent. Check canonical project/run match, complete effective/project-wide writable set, aliases/replaceable ancestors/coordinator coverage and policy/path fingerprints against held reservation; retaining narrowed coordinator=False launch grants is separate from global protection coverage. Revalidate identities before launch or selection-state mutation without semantic reload; invalidation refuses. Never unlink stable inode; unsupported ordinary branch D1 only, no protection skip/Linux fallback.
- Modify `service.say(..., actor='human', *, human_submission: HumanSubmission | None = None)`; NEW private `_launch_prepared_turn(project, prepared, *, artifacts, turn_audit)` consumes the SAME PreparedTurn, never calls prepare/resolve/config/charter again. On supported Linux ordinary say also uses reserved frozen launch inputs (absent object maps ONLY to legacy chat, never marked); retain its charter-first composition and uncapped ordinary sessions.parse behavior. Old dispatch_agent remains compatible for module/generated non-say callers and unsupported-platform ordinary branch. Share launch/wait/spend privately, no second prepare for a supported admitted send. All launch fields derive from PreparedTurn. Extend `jobs.start(..., turn_audit=None, sandbox_mask=None)` keyword-only: old omitted-mask behavior intact, prepared path passes captured masks instead of recomputing from mutable paths. Frozen inputs converted to fresh existing API lists/dicts, never mutable references. No permission/grant expansion.
- Modify `set_conversation_agent(..., actor='human', *, expected_revision=None, reset_session=False)` and existing page/API hand-over forms. NEW private pure `service._validate_conversation_target(agent, registry_snapshot)` retains existing target known/enabled/session_cmd/resume_cmd/registered-family checks; existing `_check_can_converse(project, agent)` remains a load-once wrapper with old pre-create semantics/error reasons. Nonlaunch captures registry once INSIDE reservation and calls pure validator on TARGET only, never resolves old/current manager. Selection adds no executable-existence/version/extension/certification/charter or budget-spend precondition; subsequent send performs full effective owning-run override/template/binary/charter/capability validation. Explicit reset requires authenticated human confirmation/current expected_revision, even same manager/session absent. Ordinary absent-revision hand-over remains legacy-compatible only outside unresolved K recovery; supplied tokens always checked inside reservation. No arbitrary new session id. Same-manager non-reset bumps generation but preserves session/uncertainty; different-manager recovery is explicit/revision-checked and retains history. Ordered nonlaunch contract below owns state recording.
- Modify conversation mutation helpers, not just endpoints: NEW selection_generation nonnegative int (reject bool/malformed), missing legacy zero on read WITHOUT write; NEW `selection_revision(doc) -> str` canonical decimal text. Every successful hand-over including same-manager, every explicit reset including empty session, every appended human attempt, every ordinary session write and every final session/reply/recovery transaction increments in the same short YAML transaction. Audit-only linkage need not increment. Supplied revisions must be canonical decimal and equal current value; mandatory for marked/reset/recovery, optional absent only for legacy ordinary calls. Malformed stored value is visible integrity/recovery error, never zeroed. A→B→A and finalization invalidate old forms.
- Preserve `conversation.add_turn(..., turn_id='', audit=None)` old callers; NEW `annotate_turn(ws, turn_id, *, job_id, audit)` attaches audit only, no message rewrite. NEW `finalize_turn(ws, turn_id, *, job_id, session_id, reply, state, audit, recovery_required)` performs atomic K session/reply/recovery/finalized-diagnostic/generation update under a short state lock before releasing admission. Ordinary record_session/add_turn callers stay valid. Audit schema_version=1: turn_id, input_kind, composition_order, selected_manager, family, policy_revision, message_sha256, charter_version/sha256, composed_artifact, job_id, parse_complete/reason, accepted_revision/final_revision. Optional Job.turn_audit defaults None for old JSON. No startup reconstruction/replay. Existing events only fixed bounded correlations, no new TYPES or raw command/charter/reply/log payload.
- Create narrow `src/scieflow/core/turn_artifacts.py`: `open_run_artifact(root_identity, relative_path) ->` context-managed `RegularArtifact` owning fd and initial dev/ino/size/stat identity. Trusted run/job-derived locations, pinned root and no-follow dir_fd traversal, nonblocking/no-follow leaf then fstat/type/link checks; no unchecked Path in consumers. NEW exclusive output acquisition in pinned run/log directory for copy, no symlink-follow/truncate. Tests/core/test_turn_artifacts.py covers root/leaf/ancestor/alias/type races and cleanup. This is selected K/shared-reader acquisition scope, not universal filesystem redesign.
- Create `src/scieflow/core/turn_output.py`: `read_completed_turn(handle: RegularArtifact, family: str) -> BoundedSession(id, text, complete, reason)`; `copy_raw_log(handle, exclusive_target, *, chunk_bytes=65536)` returns complete/incomplete metadata; `display_panel(handle) -> DisplayPanel(text, truncated, reason)`; bounded `DisplaySanitizer.feed(bytes, final=False) -> str`. Both K full-read sites are bypassed; ordinary sessions.parse stays unchanged. Handle snapshot extent/fstat mutation checks and chunk bounds apply before decode; raw bytes unchanged, failure never rewrites actual job state.
- Modify web/pages.py/api.py/sse.py, run.html/job.html and web/files.py plus pages.file_view: descriptor ownership throughout inline/read/download HTTP consumption, NEVER validate-path then reopen. Preserve media allowlist, attachment/nosniff, invalid/missing shapes, contained-alias compatibility by resolved-relative pinned traversal and existing range/HEAD behavior where installed stack exposes it. Raw logs force attachment/octet-stream. No new mutating route, listing redesign, server, middleware or Host/Origin policy.
- Tests: focused input/policy/admission/artifact/output files; extend existing agent_run/service/conversation/jobs, sandbox and web guard/page/file/SSE tests. NEW inert tests/fixtures/k_conversation_protocol.py and named shared k_conversational_project fixture below, not production stub/skill changes. Offline protocols are not actual interception evidence.
- Future run tools: proposed `RUN/tools/k_feature_pytest.py` and `RUN/tools/k_harness_capability_probe.py`, reviewed before execution. First must accept `--worktree K`, guard exact imports, preserve scripts pythonpath, use existing environment and fixture mount. Second exists/executed only under Task 8's live consent. Authored helpers live in tools, generated outputs in scratch.

## Execution and review focus

### R1 ordered marked-turn contract

1. Retain request auth/CSRF. Validate enum, exact marked bytes and bounded grammar without artifacts.
2. Canonicalize trusted project/run and acquire neutral nonqueueing reservation BEFORE current selection/configuration reads.
3. Read current conversation; compare required revision inside admission, reject busy/recovery/selection mismatch. No prompt/turn/job yet; existing surviving-running-job scan still applies after lock-owner loss.
4. resolve_turn ONCE inside reservation, with ONE independent resolve_turn_protection/policy capture stored on ResolvedTurn; validate effective enabled family/templates/candidate/certification. Pure prepare_turn composes accepted text and captured charter ONCE, retaining the SAME protection. Explicit reservation.validate_protection(prepared.protection) checks all frozen grants/identity/ancestors against held reservation. No artifact required; malformed charter/unknown/mismatch/unsafe input refuses here. Full send validation remains distinct from TARGET-only nonlaunch eligibility.
5. Existing wall-budget guard and real sandbox verification use that snapshot, no model job. Existing budget checkpoint/job.refused evidence and temporary verification controls are allowed pre-attempt evidence, NOT prompt/human-turn/model-job artifacts. Verify frozen effect-bearing fingerprints before launch; change causes identity-invalidated refusal, NEVER second semantic resolution/preparation. Path/sandbox identity comparisons are not a new effective-config read. No claimed atomic freeze against privileged host mutation.
6. Cross explicit attempted-turn boundary: allocate stable turn_id, create exact raw/composed audit artifacts and correlated human turn/pending diagnostic. From here, failures preserve attempt/uncertainty; missing job_id does not prove no execution.
7. _launch_prepared_turn consumes exactly that PreparedTurn and its SAME protection; jobs.start grants/captured masks derive from it, attach job_id to existing human turn, synchronous wait, actual spend once, selected bounded parse/raw copy. No marked detach/fallback or old dispatch_agent double prepare.
8. finalize_turn atomically records usable-session or recovery, reply/actual process state/audit/finalized diagnostic/generation before unlock, including fail-safe errors. Recording failure leaves uncertain pending/job evidence and no replay consent. YAML locks never span launch/wait/gate.

Changing config/grants/templates/charter after preparation must either leave launched snapshot unchanged or abort explicitly; changing binary/effect identity fails closed. Tests count resolve/prepare/charter/grant/spend calls and inspect actual launch fields, not joined argv or preparer strings alone.

### R5 ordered nonlaunch hand-over/reset contract

On supported Linux, independent protection construction is mandatory; no full dispatch-shaped object is needed or allowed:

1. Retain existing auth/CSRF and explicit-reset confirmation in guarded controllers. Canonicalize trusted project/run via existing service path checks plus admission identity rules, then acquire SAME neutral nonqueueing reserve(project, ws), with no prompt/job/budget spend/harness invocation.
2. Inside reservation read conversation; validate stored generation and supplied revision, require revision for explicit reset or unresolved-K recovery, enforce busy/surviving-job refusal. Malformed generation is an integrity refusal, never reset to zero; missing ordinary token compatibility is only outside unresolved recovery.
3. Capture registry ONCE inside reservation; pure _validate_conversation_target(agent, registry_snapshot) checks TARGET only. Independently construct ONE TurnProtection from canonical project/run and captured sandbox policy; explicitly reservation.validate_protection(protection). Full allowlist/alias/root/replaceable-ancestor/project/coordinator coverage and unconfined/unsafe refusal remain D1, even when current manager absent/broken or charter malformed. No full resolve_turn/prepare_turn, charter.snapshot, executable/version/extension discovery, support_for, model/budget-spend or sandbox-execution launch probe.
4. Revalidate relevant reservation/path/policy and target-registry identities before mutation without rereading/re-resolving semantic decisions. If changed, refuse with zero selection/session/recovery/generation write, not a second policy/registry load. Malformed registry, invalid target, stale revision and unsafe safety inputs refuse honestly. No claim of atomicity against privileged host writes; next send independently validates its launch snapshot.
5. One short conversation transaction applies hand-over/reset and centralized generation update: EVERY successful hand-over including same-manager increments; same-manager non-reset preserves session/uncertainty, confirmed reset clears only current session/recovery and increments even if already absent. Different-manager deliberate recovery requires current revision and retains all history/jobs. No client replacement id/old-id guess. Unlock ONLY after state recording completes; failed validation leaves state unchanged, no supported-Linux legacy fallback.

First selection has no current manager and is valid. Removed/disabled/invalid-family/missing first or resume template/missing executable old A may be switched AWAY FROM to eligible B under safe protection. Same-manager reset still validates TARGET (disabled/missing-template target refuses); missing executable alone is not a new selection check or certification, and subsequent send fails dispatch safely. Malformed unrelated charter does not block selection/reset; subsequent send still refuses malformed charter before prompt/human-turn/model-job artifacts. Missing charter remains ordinary-send compatible; malformed safety inputs still block nonlaunch.

Preserve start_run's existing pre-create _check_can_converse check and create → set_conversation_agent → ordinary say convenience flow/RunStartedError after creation. Inner hand-over and later send each own their admission; do not recursively reserve or claim one lock covers whole convenience call. Unsupported ordinary branch remains explicitly separate under D1, K disabled; Linux busy/stale/corrupt/unsafe never falls back. Tasks2–4/7 and A08/A15 below test these construction and compatibility boundaries, not live certification.

### R4 stable artifact and bounded-consumption contract

Derive job output from canonical trusted admitted run/jobs and valid existing generated-id basename (`W/src/scieflow/core/store.py:23–36`, Crockford ULID, 26 characters with valid leading range), then `.log`/`.err`; no normalizing malformed ids or trusting Job.log/err/run_dir to choose authority. Global log route locates unique actual canonical record under known project job roots and verifies basename/id/ownership; unscoped ordinary jobs use explicitly trusted project state/jobs. K always admitted run. Keep O(1) selected-record state polling after trusted location acquisition, not project scans each poll.

Pin trusted root identity, walk canonical relative components with dir_fd/no-follow directory opens, open leaf nonblocking/no-follow BEFORE fstat regular-file/dev/ino/stat validation. Reject changed root, escape, unexpected link/type or unsupported proof; no FIFO/device/socket read. Contained aliases resolve first, then traverse resulting relative path from pinned root without changed-component following; never reopen original alias/absolute path. K raw logs refuse multiply linked files; shared artifact tests must reject outside hardlink sentinel provenance or fail the affected branch, not assume a pathname proves provenance. Ordinary stable files and contained symlink artifacts remain compatible when confinement is proved.

All parser/copy/panel/download operations consume owned RegularArtifact fd through completion/disconnect; exclusive copy target via pinned logs dir, no preexisting-target follow/truncate. Acquisition races cannot redirect bytes after validation. Stable fd does not mean immutable content: initial size is finite upper snapshot extent for completed parse/copy/download; compare fstat before/after for growth/truncation/metadata change, report incomplete, never claim exact complete copy. No read(-1), unbounded line accumulation or job-state rewrite. Downloads stream finite extent with bounded memory (not capture ceiling). After HTTP headers, mutation terminates safely with incomplete-transfer diagnostics; no prose appended to raw bytes or complete-transfer claim. Live SSE pinned offsets stop visibly on replacement/type/size regression, not kill job. Close all descriptors on exceptions/disconnect/cancelled response. Privileged in-place rewriting is not cryptographically immutable audit.

Inline text also uses bounded handle consumption; raw K logs cannot regain unbounded access through another text viewer. Job-log `_log` bounds/sanitizes wire output; `_events` timeline is distinct, fixed correlations only, no universal legacy history cap. Listing, upload/archive/export and general filesystem/HTTP hardening are excluded. Installed response range/HEAD compatibility must be characterized and preserved in Task6, not silently dropped by a generic StreamingResponse.

Five high-risk cases, each assigned below: prompt artifact replaced after acceptance (Task 2); resumed cfg/extension identity changed (Tasks 1–2); process exits before session recording while another hand-over enters (Tasks 3–4); a UTF-8/OSC/JSON line never terminates (Tasks 5–6); ambiguous acknowledgement after actual command effects (Tasks 4, 7–8). A cosmetic green page is not evidence for any of them.

Future test command convention (NOT executed by this plan author): owner establishes exact absolute K and RUN, then uses the reviewed source selector from K. Names in examples are per-attempt immutable receipts; choose a fresh suffix at execution. The proposed source selector is not today's Ticket22 runner.

```sh
cd "$K"
"$ROOT/.venv/bin/python" -B "$RUN/tools/run_check.py" k-task2-red-a1 -- \
  "$ROOT/.venv/bin/python" -B "$RUN/tools/with_project_fixture_mount.py" k-task2-red-a1 -- \
  "$ROOT/.venv/bin/python" -B "$RUN/tools/k_feature_pytest.py" --worktree "$K" \
  -p no:cacheprovider --basetemp @BASE@ tests/core/test_agent_run.py -k k_ -q \
  --junitxml "$RUN/scratch/k-task2-red-a1.xml"
```

Do not set interpreter environment variables to bypass earlier refusals. Prove the selector with positive exact-import and deliberate wrong-source controls before relying on it. Wrapper/mount/unmount exit and cleanup must be recorded with the pytest result. No live marker or model CLI is included in offline slices.

### Task 1: Finite candidate policy and explicit input contract

Depends on G0–G3 readiness/approval, D0 local base preparation and source-selector verification. D0–D5 are owner-selected, C1 human-closed; certification still waits G4/G5. G3 approves bounded offline disabled infrastructure, not live probes.

Files: create turn_input.py, harness_commands.py and their two test files; future run source-selector helper plus its bounded tests. Read actual candidate skill; do NOT change/install it or config.

Interfaces: produces HumanSubmission, input validators, HarnessIdentity/SupportStatus and empty CERTIFIED_POLICIES for Tasks 2/4/7. The route cannot select synthetic fixture tokens.

- [ ] RED: write exact-input/candidate tests below; additional negative vectors `/scieflow-men`, `/help`, `/scieflow-menu /other`, `/scieflow-menu\n`, `/scieflow-menu `, slash lookalike and NUL. Unknown identity, Codex/agy, changed version/binary/template/extension/model/effort/cwd/discovery, disabled effective manager and missing certification refuse pre-attempt, no prompt/turn/model-job or turn.sent event. Separate existing budget-checkpoint/sandbox-refusal evidence is allowed later in ordered step5. Safe identity reads are bounded/local/non-secret; dynamic hooks, unpinnable writable effects, missing digest or arbitrary version command refuse.

```python
import pytest
from scieflow.core.turn_input import validate_marked_input
from scieflow.core.harness_commands import validate_candidate_raw

@pytest.mark.parametrize('message', [' /scieflow-menu', '\t/scieflow-menu', '/scieflow-menu\0'])
def test_k_marked_input_not_normalized(message):
    with pytest.raises(ValueError):
        validate_marked_input(message)

@pytest.mark.parametrize('count,allowed', [(32768, True), (32769, False)])
def test_k_input_limit_uses_utf8_bytes(count, allowed):
    message = 'é' * count
    if allowed:
        assert validate_marked_input(message) == message.encode('utf-8')
    else:
        with pytest.raises(ValueError):
            validate_marked_input(message)

def test_k_production_candidate_is_exact():
    validate_candidate_raw('/scieflow-menu')
    with pytest.raises(ValueError):
        validate_candidate_raw('/scieflow-k-probe')
```

These are proposed tests; imports come from the two proposed modules. The length utility accepts a non-command string at the limit; candidate grammar is a separate stricter validator, not an expanded production allowlist.

- [ ] Run named new input/policy tests plus exact/wrong-source selector controls. Capture assertion/import RED, not a provider refusal; after scaffolding, ensure negative controls exercise actual validation rather than only missing imports.
- [ ] Implement minimal validators and deny-by-default policy; effective_identity consumes bounded safely captured resolution inputs, support_for is pure. G4 must enumerate safe binary/version/effect inputs and cost/identity evidence before any actual version probe. Fixture extension/cwd evidence never wildcards deployment identity. Do not add a catalogue/editor/global switch/agent-writable or forged certification placeholder.
- [ ] GREEN same nodes plus agent-config/owning-run override regression tests. Verify no registry/templates/skills changes and all unsupported identities remain disabled.
- [ ] Independent specification THEN security/quality review; owner dispositions actual identity findings without reopening C1, accepts exact diff. Do not commit without separate approval.

### Task 2: Accepted-byte preparation and charter composition

Depends on Task1 accepted. Files: agent_run.py/shared run-based loader, existing sandbox.py pure policy factoring, service.py pure target-validator extraction/load-once wrapper; tests/core/conftest.py, test_agent_run.py/test_turn_input.py/test_sandbox.py/test_service.py; NEW tests/fixtures/k_conversation_protocol.py. Interfaces: NEW TurnProtection/resolve_turn_protection and private _validate_conversation_target contract/tests, resolve_turn/ResolvedTurn and pure prepare_turn/PreparedTurn from file map; no existing prompt required, no manager dependency for protection. Old prepare/compose/generated/role/public sandbox helpers/pre-create wrapper preserved. Task2 isolates producer/pure contracts; real admission integration and the admitted example below run after Tasks3–4, never resolve before reservation in service.

R3 fixture construction BEFORE use: proposed shared tests/core/conftest.py fixture below uses the existing core `project` (real config/status pair and logs, `W/tests/core/conftest.py:73–96`), not test_agent_run.project_with_run's nonconversational stub. `_add_agent` below is the same append safety pattern as `W/tests/core/test_agent_run.py:702–704`, defined in shared conftest for service tests too; do not import test modules or alter production stub. yaml.dump safely quotes the templates with real fixture path; template prompt operand follows a plain `first`/`resume` token, preserving existing stdin-safety checks.

```python
# Proposed shared tests/core/conftest.py additions; all imports explicit.
import shlex
import sys
from pathlib import Path
import pytest
import yaml

def _add_agent(project_root, name, block):
    path = project_root / 'config' / 'agents.yml'
    path.write_text(path.read_text() + f'  {name}:\n{block}')

@pytest.fixture
def k_conversational_project(project):
    ws = project.run_dir('r1')
    protocol = Path(__file__).resolve().parents[1] / 'fixtures' / 'k_conversation_protocol.py'
    prefix = f'{shlex.quote(sys.executable)} {shlex.quote(str(protocol))}'
    cfg = {'cmd': prefix + ' first {prompt}',
           'session_cmd': prefix + ' first {prompt}',
           'resume_cmd': prefix + ' --resume {session} resume {prompt}',
           'family': 'claude', 'enabled': True, 'timeout_min': 1,
           'model': 'fixture', 'reasoning': 'high'}
    block = ''.join('    ' + line + '\n' for line in yaml.safe_dump(cfg).splitlines())
    _add_agent(project.root, 'k-protocol-fixture', block)
    prompt_file = ws / 'logs' / 'k-baseline.md'
    return project, ws, prompt_file
```

NEW inert tests/fixtures/k_conversation_protocol.py proposed protocol (not a command expander; accepts arbitrary final argv text including slash/flags, or stdin with no operand). It never invokes model/tools, discovers extensions or writes workflow files. Deterministic Claude-shaped init/result allows real zero-exit and usable-session controls; input digest validates actual fixture input against separate launch-byte assertions.

```python
import hashlib
import json
import sys

args = list(sys.argv[1:])
session_id = 'k-session-1'
if args[:1] == ['--resume']:
    session_id = args[1]
    args = args[2:]
assert args and args.pop(0) in ('first', 'resume')
assert len(args) <= 1
text = args[0] if args else sys.stdin.read()
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session_id}))
print(json.dumps({'type': 'result', 'session_id': session_id, 'result': 'K protocol reply',
                  'input_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}))
```

Ordinary baseline-positive test below runs in future authorized K on the accepted W source generation with ONLY test-local fixture additions and existing APIs; W itself stays READ-ONLY. Capture PASS before marked implementation. It does not certify interception. Preparation assertion and actual inert subprocess result both must be reached (sandboxed service positives in Task4 remain separate).

```python
import hashlib
import json
import subprocess
from scieflow.core import agent_run, sessions
from scieflow.core.run import charter

def test_k_ordinary_slash_stays_charter_first(k_conversational_project):
    project, ws, prompt_file = k_conversational_project
    charter.set_text(ws, 'K charter sentinel')
    prompt_file.write_text('/scieflow-menu')
    d = agent_run.prepare(project, 'k-protocol-fixture', prompt_file, conversational=True)
    sent = d.stdin_text if d.stdin_text is not None else d.argv[-1]
    assert sent.startswith(agent_run.CHARTER_HEADER)
    assert sent.count('K charter sentinel') == 1
    proc = subprocess.run(d.argv, input=d.stdin_text, capture_output=True, text=True,
                          cwd=d.cwd, timeout=10, check=False)
    assert proc.returncode == 0
    parsed = sessions.parse({'family': 'claude'}, proc.stdout)
    assert parsed.id == 'k-session-1' and parsed.text == 'K protocol reply'
    result = json.loads(proc.stdout.splitlines()[-1])
    assert result['input_sha256'] == hashlib.sha256(sent.encode('utf-8')).hexdigest()
```

Marked composition/file-swap behavioural RED after imports/scaffolding: test-local support_for injection provides synthetic finite identity only; a deliberately charter-first preparer must fail the exact-byte assertion, not import/template setup. `offline_k_support` is a NEW local monkeypatch fixture built in Task1, returning SupportStatus(enabled=True, reason='', policy_revision='fixture-only') only for k-protocol-fixture's exact captured identity, denying everything else; production policy remains empty. Pure APIs below are proposed, not present today.

```python
import pytest
from scieflow.core import agent_run
from scieflow.core.turn_input import accept_human_submission
from scieflow.core.run import charter
from scieflow.core.turn_admission import reserve  # PROPOSED Task3; integrated test after admission exists

@pytest.mark.parametrize('force_stdin', [False, True])
def test_k_accepted_input_wins_over_artifact(k_conversational_project, offline_k_support,
                                           monkeypatch, force_stdin):
    project, ws, prompt_file = k_conversational_project
    charter.set_text(ws, 'K charter sentinel')
    if force_stdin:
        monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 10)
    accepted = accept_human_submission('/scieflow-menu', 'harness-command', '0')
    with reserve(project, ws) as reservation:
        resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)
        prepared = agent_run.prepare_turn(resolved, accepted)
        assert prepared.protection is resolved.protection
        reservation.validate_protection(prepared.protection)
        prompt_file.write_text('/hostile-replacement')  # audit artifact, never input authority
        sent = prepared.stdin_text if prepared.stdin_text is not None else prepared.argv[-1]
        expected = '/scieflow-menu\n\n---\n\n' + agent_run.CHARTER_HEADER + '\n\nK charter sentinel'
        assert sent.encode('utf-8') == expected.encode('utf-8')
        assert (prepared.stdin_text is not None) == force_stdin
        assert '/hostile-replacement' not in sent
```

- [ ] RED: no existing prompt at resolution/refusal, pair/regular-file/unique structural owner and role regressions; no nominally frozen mutable config/Dispatch fields. Accepted message mismatch/nonconversational marked caller refuses; slash/actor/files/YAML/env have no authority. Malformed/absent charter; first/resume argv/stdin; hostile braces/quotes/backticks literal. Utility-only hostile-byte tests do not broaden exact production grammar.
- [ ] RED R5 producer/target contracts: test_k_protection_policy_loaded_once freezes opt-out/narrowed grants/masks/full project/coordinator/alias/ancestor facts from ONE policy load, no manager/session/prompt/charter dependency; count immutable identity through resolve/prepare. test_k_target_validation_is_snapshot_only covers known/enabled/both templates/registered family with captured registry; old _check_can_converse loads once and preserves each old error/pre-create refusal. Invalid target/bad policy refuses, not half-ResolvedTurn; no executable/version/certification selection probe. Public unsandboxed_runs/allowlist_paths/writable_for compatibility tests stay green.
- [ ] Record actual ordinary-positive PASS, then intended marked-order/file-swap assertion REDs; retain import RED separately, never use unrelated template/missing-directive error as decisive RED. Count source/charter reads once; mutate cfg/grants/templates/charter after preparation to show unchanged snapshot or explicit abort, no unchecked re-resolution. Real launch assertions follow Task4.
- [ ] Implement NEW immutable protection producer/pure target validator and immutable captured full resolution/pure composition; single captured policy supplies opt-out/grants/protection, same charter snapshot drives send text/version/digest. Full send preserves stale in-run sandbox-key refusal from captured run config; independent protection does not read that config/charter. prepared.protection is resolved.protection, no masks/grants reread on launch. First/resumed safety unchanged, no shell=True/permission flags/one-shot fallback/global reorder. Old prepare retains strict prompt ownership; existing public helpers and _check_can_converse load-once wrapper/pre-create semantics unchanged.
- [ ] GREEN same nodes plus existing charter/oversized/override tests `W/tests/core/test_agent_run.py:637–775`; exact argv element/stdin UTF-8 and resumed session operand, fixture zero exit/reply/session/digest. Repeat forced stdin first/resumed with low limit, not a failing stub as success.
- [ ] Independent specification THEN security/quality reviews of accepted-byte/snapshot/fixture reachability and old APIs; freeze diff before Task3.

### Task 3: Protected shared admission reservation

Depends on Task2 accepted. Files: turn_admission.py/test_turn_admission.py; service.say/set_conversation_agent reservation plumbing, test_service.py and sandbox tests. Interfaces: reserve context, exact typed validate_protection(protection: TurnProtection), errors and independently produced frozen protection. Neutral reservation BEFORE resolution, no original admit(writable=...) contract. Send passes prepared.protection retained from full resolve; nonlaunch passes ONE resolve_turn_protection output without old-manager/charter/certification/full dispatch dependencies. No dummy composition/object, omitted protection or YAML lock spanning a model call. Both use same complete project/run/grant/alias/ancestor/coordinator envelope.

- [ ] RED: use two separate processes with pipe/barrier acknowledgements, not sleeps, to attempt same canonical run lock; second fails busy without waiting/queueing, different run succeeds. Symlink spellings of the same canonical run use the same reservation. Add simultaneous send/send and send/hand-over barriers before job creation. Pause after process exit but before record_session/add_turn; another request still fails busy. Cancel succeeds while the reservation remains held, with normal auth later tested in Task 7.
- [ ] Run tests against current unprotected service; demonstrate two admissions or finalization race as behavioral RED, not just absent-module failure. Add same-process independent-thread control; kernel lock must not become a reentrant shared instance. Task3 accepts the isolated reservation/protection and basic wrappers; final same-snapshot ordinary/marked launch/finalization integration is Task4 and must pass before branch enablement, no claim that intermediate scaffolding is a deployed guarantee.
- [ ] Implement selected canonical `.scieflow/turn-admission`/versioned canonical hash/Linux `flock(LOCK_EX|LOCK_NB)` with separate descriptor per entrant/thread and stable inode. Reject symlink/replaced file/root/replaceable ancestor, complete allowlist/coordinator-workspace grants/aliases and unconfined/unsafe protection (K never writable=None). Acquisition precedes config reads; typed validate_protection checks SAME independently frozen value and compare identities before launch or nonlaunch transaction, no semantic reload. Unsupported platform/backend selects documented ordinary compatibility ONLY before K admission; K refuses, and supported busy/unsafe NEVER falls back. Existing running-turn scan stays after lock-owner loss; no receipt/idempotency store or permission change.
- [ ] RED/GREEN R5 admission negatives: first/broken-current selection still validates full policy envelope; busy/surviving-job/stale/malformed revision or generation/unsafe allowlist/project/coordinator/alias/replaceable-ancestor paths refuse with byte-identical conversation. Barriers mutate reservation/path/policy/target-registry identity after capture and before transaction; invalidation refuses without second policy/registry load. Target/current protections never derive from different policy reads. Reuse concrete Task4 R5 matrix after integrated state recording; isolated admission tests are not final service proof.
- [ ] GREEN actual sandbox positive/negative control: host can write synthetic lock-parent sentinel; child writes granted run sentinel but cannot unlink/replace lock or rename/replace its parent. Include writable-ancestor aliases/project allowlist/workspace-coordinator cases; unsafe protection refuses before model launch. Provenance unchanged. Kill ONLY disposable lock owner; release plus surviving-job refusal. Test supported ordinary busy/unsafe refusal, unsupported K refusal and ordinary compatibility as distinct branches; no silent second inode.
- [ ] Independent specification THEN security/quality review must establish all effective grants/aliases/ancestors, platform compatibility, real child denial, finalization span/cancel/loss semantics. Missing proof blocks enablement, not mode-bit/trust fallback.

### Task 4: Synchronous service integration, durable audit and uncertainty

Depends on Tasks1–3 accepted. Files: service.py, run/conversation.py, jobs.py and existing focused tests. Interfaces: same-object _launch_prepared_turn, atomic finalize_turn, centralized generation mutations and schema_version=1 audit; old dispatch/ordinary records remain compatible. Until Task5 bounded completion is accepted, marked output uses only an injected inert completion test seam and production certification stays empty; no interim full-log K production path.

R5 concrete future OFFLINE RED/GREEN matrix (NOT executed now). Task2 owns pure construction/load contracts; Task3 admission barriers/protection; Task4 full service/state/start_run integration; Task7 repeats request-level positives/negatives with auth. Each nonlaunch test asserts zero prompt/human-turn/model-job/spend/launch artifacts and exact state/generation/history expectations; mocks of full resolve_turn/prepare_turn/charter.snapshot/binary/version/extension discovery/support_for/model/budget-spend/sandbox-execution launch probes raise if touched. Later send controls restore real full resolution and assert refusal before attempted artifacts where specified.

| Future test / owner | Concrete assertion |
|---|---|
| test_k_first_selection_without_current; test_k_start_run_selection_then_ordinary_send (T4/T7) | Empty/no-current conversation → eligible inert protocol manager succeeds under safe protection. Actual start_run pre-create invalid-target refusal leaves no run; valid create → selection → ordinary charter-first opener reaches real fixture reply/session/zero exit when authorized. Separate acquisitions, no recursive lock; post-create failure still RunStartedError. |
| test_k_switch_away_from_broken_current (T4/T7, parametrized) | A removed/disabled/invalid family/missing session_cmd/missing resume_cmd/missing executable → eligible B succeeds, history kept, session cleared/generation bumped; no A lookup/dispatch resolution. Full protection still mandatory. |
| test_k_nonlaunch_charter_contrast (T4/T7) | Eligible target plus malformed unrelated charter permits hand-over AND confirmed same-manager reset; subsequent ordinary/marked send refuses malformed charter before prompt/human-turn/model-job artifacts. Missing charter ordinary baseline remains compatible. |
| test_k_same_manager_revision_and_session (T4/T7) | Non-reset success increments generation but preserves current session/uncertainty; confirmed revision-checked empty-session reset increments and clears only recovery/current session. Invalid TARGET known/enabled/two-template/family check refuses with zero mutation, including same-manager reset. |
| test_k_selection_does_not_certify_missing_binary (T4/T7) | Eligible target with missing executable can be selected, with no existence/version/certification probe; subsequent send fails full dispatch validation safely. Selection is not command enablement. |
| test_k_nonlaunch_refusal_has_no_mutation (T3/T4/T7, parametrized) | Busy/surviving-job/stale or malformed required/supplied revision/malformed stored generation/malformed registry/unsafe allowlist/project/coordinator/alias/replaceable-ancestor grant/unconfined protection → exact conversation unchanged, supported Linux never ordinary fallback. |
| test_k_captured_identities_invalidate_before_selection_write (T3/T4/T7) | Barrier changes reservation/path/policy/registry after capture → explicit invalidation refusal, no state write and no new semantic resolution; never privileged-writer atomicity claim. |
| test_k_nonlaunch_single_load_no_launch (T2/T4/T7) | ONE policy load per operation and ONE target registry capture inside selection reservation, preserved wrapper's separate start_run pre-create load not miscounted. All nonlaunch launch/charter/capability/spend mocks raise if touched. Assert typed validate_protection receives same frozen independent value. |
| test_k_send_retains_protection_identity (T2/T4) | Full send resolves once inside reservation, policy/config/charter once; prepared.protection is resolved.protection and launch grants/masks derive from it. Full override/charter/binary/capability checks and pre-attempt refusal remain, no second loader or weakened send validation. |

- [ ] RED: guarded object vs actor='human' alone, forged stored mode/order/digest/reply/skill/prompt/env; no marked reconstruction. Use successful k_conversational_project/protocol first/resume, assert REAL zero job exit, nonempty 'K protocol reply', k-session-1/current resume id, exact launched argv operand/stdin bytes and raw/composed artifact/digest agreement. Legacy Job JSON loads with turn_audit=None; legacy conversation missing generation reads zero without write.
- [ ] RED R1: unknown/mismatched identity and malformed charter with NO prompt existing; refusal before prompt/turn/model-job/turn.sent. Barrier after resolve/preparation, mutate cfg/grant/template/charter/effect binary; launch identical immutable snapshot or abort, never second resolution/prepare. Assert call counts once, captured masks/coordinator=False grants/environment launch values/audit, single spend, budget/sandbox step5 evidence distinct from attempted step6. Full-chain prompt swap AFTER artifact write and before actual launch cannot change accepted bytes.
- [ ] RED R2: A→B→A before any session; same-manager hand-over; same-manager reset with session None; stale send/hand-over/reset forms; form rendered mid-turn invalidated by finalization. Provided revision malformed/stale rejected for ordinary too; absent ordinary legacy accepted, missing marked/reset/recovery refused. Stored bool/negative/string/noncanonical generation integrity error, never zero-reset. Generated default calls never gain authority. Central helpers must bump every selected mutation including ordinary session writes; audit-only annotate linkage need not bump.
- [ ] Implement exact R1 send AND R5 nonlaunch ordered contracts; reserve → conversation/revision/busy → target snapshot plus ONE independent protection → typed validation/identity revalidation → single state transaction → unlock. Preserve start_run pre-create wrapper/convenience/RunStartedError and unsupported ordinary branch, no recursive reserve. old dispatch_agent NOT called to prepare K again. annotate by stable turn id without text rewrite; jobs.start audit/captured mask defaults backward-compatible; atomic final K session/reply/recovery/pending/finalized/generation update before unlock. Existing events fixed bounded fields only, no raw message/charter/reply/log. Malformed generation raises visible integrity error under short transactions, no endpoint-only bump logic.
- [ ] GREEN boundary failure injection: pre-attempt refusal; attempt recorded/no known job; jobs.start uncertainty; wait/process exit; spend/parse/copy/final recording error; response loss. After attempt, missing job is NOT proof of no launch. Timeout/cancel/missing/contradictory/incomplete id requires recovery even with prior id in history; no ordinary automatic reuse. Complete bounded consistent valid id alone is usable. Actual job state/spend once; no detach/replay/fallback.
- [ ] GREEN explicit recovery uses reserved authenticated hand-over/reset_session=true, required expected_revision and explicit confirmation, same-manager allowed even empty session, TARGET eligibility/independent safe protection mandatory, busy refuses. No current-manager or unrelated charter/binary/certification prerequisite; subsequent send fully validates. Every successful same-manager hand-over bumps generation, session/uncertainty preservation separate. Clear only current session/recovery on confirmed reset, keep history/job diagnostics. Different-manager deliberate recovery uses same revision-checked path; arbitrary client replacement id refuses. Crash after launch/before finalization uses existing reconcile + pending evidence to show uncertainty; successful finalization/response loss reloads result, never resends. No protected receipt DB/exactly-once claim.
- [ ] Independent specification THEN security/quality review of R1/R2/snapshot/spend/error/recovery/legacy/defaults, event bounds and fixture zero-exit assertions. Any materially broader recovery scope returns a precise owner finding, not improvised code.

### Task 5: Bounded completed-turn parsing without whole-log reads

Depends on Task4 accepted. Files: NEW turn_artifacts.py/test_turn_artifacts.py plus turn_output.py/test_turn_output.py; service._launch_prepared_turn/K say completion; jobs.py trusted-location lookup helper limited to selected readers; tests/core/test_sessions.py compatibility only. Interfaces: pinned RegularArtifact/open_run_artifact/exclusive target and handle-consuming parser/copy, not Path readers; K selected by in-memory submission, never stored mode. Old dispatch_agent.finish/sessions.parse ordinary behavior stays.

- [ ] RED: a counted binary stream that rejects read(-1)/read_text, records every requested size and maximum retained accumulator. Test exact 2 MiB boundary and overflow, 64 KiB chunks, 16 KiB records and oversized record discard-until-newline; split UTF-8, partial JSON, deeply nested JSON, no terminal newline, noisy output, result-vs-assistant duplication, session id appearing late/beyond budget, contradictory/missing id and timeout/cancel. Whole job/log bytes must still exist. Ordinary chat uses existing sessions.parse with no new parse ceiling.

```python
import io

class CountedBytes(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.requests = []
    def read(self, size=-1):
        assert 0 <= size <= 65536, 'unbounded materialization'
        self.requests.append(size)
        return super().read(size)
```

Install this stream behind a test-owned RegularArtifact read seam; production handle still enforces fd/type/root identity. This is a bounded-read test double, not fabricated harness evidence. Use real synthetic files and compare stable raw/copy hashes. CountedBytes requires an explicit `import io` in its test module.

- [ ] Run new parser/service K nodes and ordinary test_sessions.py; current full-read path must trigger the sentinel RED. Test control deliberately calling read(-1) to prove the sentinel works.
- [ ] RED R4: derive canonical admitted run/jobs/valid basename, never Job.log/err/run_dir authority. Global record locator requires unique actual trusted root/id ownership; test forged/outside/duplicate records, unscoped ordinary trusted state/jobs and malformed generated-id rejection. Deterministic root/leaf/ancestor replacements between validation/acquisition; contained/outside/changed alias; multiply linked K log; FIFO/device/nonregular swap opened nonblocking and refused with bounded cleanup. No outside sentinel bytes read/copied. Exclusive target rejects preexisting symlink/file without truncation.
- [ ] Implement pinned no-follow dir_fd RegularArtifact and exclusive target; all completed parse/copy reads <=64 KiB, initial-size finite extent and fstat before/after. Growth/truncation/content metadata mutation is incomplete, not complete copy/usable session; no raw-byte sanitation/replacement. Claude records <=16 KiB and aggregate <=2 MiB before decode, never parse overflow prefix or guess session data. Catch deep JSON/partial UTF-8 honestly. Descriptor cleanup on exceptions. No unbounded read, process kill/state rewrite/disk cap.
- [ ] GREEN deterministic growth/truncate/metadata mutation DURING parse/copy; stable fixture hashes exact, raw retained on incomplete copy. Successful process remains done, real cancelled/timeout/lost unchanged; precise reason and safe handle-derived raw link, recovery-required, no prior-id guess. Complete resume works. Assert neither old service full-read site reached for K; ordinary parser has NO new cap. Track max buffers and descriptor cleanup, not only RSS/timing.
- [ ] Independent specification THEN security/quality review of handle authority/TOCTOU/type/link/size/cleanup, actual buffer bounds, raw preservation, session uncertainty and ordinary compatibility. No blanket filesystem/capture redesign hidden in this task.

### Task 6: Bounded sanitized diagnostics and raw artifact access

Depends on Task5 accepted. Files: turn_output.py/turn_artifacts.py shared display/acquisition helpers; web/pages.py/web/files.py/web/sse.py, run.html/job.html and existing file.html text rendering only if needed for bounded notices; tests/core/test_turn_output.py/test_turn_artifacts.py and web test_pages.py/test_files.py/test_sse.py. Interfaces: handle-consuming DisplayPanel/sanitizer plus narrow handle-owning response adapter; explicit shared file_view change is part of G3, not an optional unbounded branch. Metadata remains audit-only.

- [ ] RED: HTML/script/Markdown links; CSI and OSC with BEL/ST terminators split at every byte; unterminated escape across huge lines; residual C0/C1 controls and bidi formatting; newline/tab preservation and invalid UTF-8. Server panels and live DOM cap with explicit notices and retained log link; exactly-at-limit/over-limit/astral Unicode for owner-selected units. No line can inject event:/id:/data: framing or terminal cursor/hyperlink/clipboard actions.
- [ ] Run finite generator tests with a fake disconnected request plus real-socket tests using existing test_sse live fixture. Assert capped source reads/pending bytes/emitted UTF-8 wire line/DOM growth; TestClient is not a valid consumer of endless SSE. Current read-all pages and unbounded pending/textContent append supply REDs.
- [ ] RED response-consumption races: replace leaf/ancestor/root/alias after validation and AFTER endpoint returns but BEFORE consumption, FIFO/nonregular/hardlink and outside sentinel controls. HTTP adapter consumes SAME fd, never FileResponse of a checked path. Bound actual read requests and close all fds on normal completion/exception/disconnect/cancelled response. Characterize retained installed media/range/HEAD behavior and assert compatibility (do not invent current range support).
- [ ] Implement <=64 KiB fd reads, bounded decoder/escape/pending offsets; raw partial record/escape <=16 KiB, wire <=16 KiB UTF-8 including prefix/newlines AFTER sanitation; discard overlong line until newline with bounded notice. Rendered panels/live aggregate <=200,000 UTF-16 units including escaped expansions/notices, never split scalars. Apply sanitizer/caps to output/error/history/command/audit/reply/argv/error strings in selected run/job displays, not only out pre. Jinja autoescape/textContent, no HTML/Markdown/terminal actions. Keep session auth and O(1) trusted selected-record state polling; `_events` stays separate, no raw K payload or universal legacy event-history bound.
- [ ] GREEN file_view inline text and media/raw response consume validated handle throughout; raw logs attachment/octet-stream/nosniff; safe media allowlist and existing 400/404, ordinary stable/contained-symlink/special-name/media/range/HEAD/auth shapes retained. Generic inline text bounded too, no alternate unbounded K-log viewer. Streaming finite initial extent, growth/truncate/metadata change incomplete; after headers terminate safely, no appended prose/complete-transfer claim. Live replacement/type/size regression visibly stops only view. Stable raw hash exact, no outside bytes.
- [ ] GREEN non-K selected shared panels/SSE/history visibly truncate with raw link, no new parse/input/capture/kill limit. Disconnect closes viewer fd only, never job. Preserve small/partial-final lines and live socket responsiveness. Test exact/over caps, astral characters, split escapes and expansion notices using actual encoded units, not len(str) alone.
- [ ] Independent specification THEN security/quality review traces EVERY selected viewer/file response, alternate route and scope/unit/FD/response compatibility. Listing/export/archive/upload/global server hardening excluded; raw disk growth and retained Host/Origin risks explicit. A materially broader safe-access fix returns owner finding, not hidden expansion.

### Task 7: Authenticated composer, history and recovery UI

Depends on Tasks1–6 accepted. C1 human closure/D0–D5 are incorporated, not pending worker choices. Files: web/pages.py/api.py, run.html/job.html; tests/web/test_conversation_page.py/test_mutations.py/mutating_paths.py/test_read_only.py/test_async_routes.py/test_sse.py, new web-local adaptation of the inert fixture without importing core test modules. Existing unsafe endpoint inventory extended with both modes and hand-over/reset field variants, no new mutating route.

- [ ] RED: absent mode -> chat; invalid enum/duplicate/malformed fields controlled rejection; exact token/default unchecked/support reason; ordinary slash unchanged. Guarded page/API constructs object, client actor ignored. NEW send AND hand-over forms carry canonical decimal revision. Send field selection_revision maps to HumanSubmission; hand-over field expected_revision maps to selected service keyword. Marked/reset/recovery missing revision refuses; ordinary absent legacy okay, supplied malformed/stale refuses for both sends AND hand-overs. Explicit reset_session=true requires deliberate confirm_new_session=true in guarded controller and required expected_revision; no arbitrary replacement id. Same-manager empty reset bumps, stale reset/hand-over denied. Reset marker after send/hand-over, immediate repeat suppression, no network/SSE/reload auto retry.
- [ ] RED/GREEN R5 guarded page/API matrix from Task4: first/no-current selection, broken A → eligible B and malformed-charter hand-over/confirmed reset positives; subsequent send's malformed-charter/missing-binary refusals stay pre-attempt. Same-manager non-reset bumps but preserves uncertainty/session; invalid target/busy/stale/unsafe/mutation negatives leave state byte-identical. Instrument one selection target snapshot/ONE policy load and same typed protection; fail if controllers/service add launch/charter/certification/spend probes or skip protection. Retain session/CSRF/confirmation/revision guards on all positives/negatives; successful selection never enables K.

```python
import pytest

@pytest.mark.parametrize('path', ['/runs/r1/say', '/api/v1/runs/r1/conversation'])
def test_k_marked_form_needs_csrf(client, path):
    response = client.post(path, data={'message': '/scieflow-menu',
                                      'mode': 'harness-command',
                                      'selection_revision': '0'}, follow_redirects=False)
    assert response.status_code == 403
```

Use existing signed-in client fixture so this is a CSRF control, not accidental no-session rejection. Separate anonymous/wrong-session/bad-CSRF guards and valid cookie+header-CSRF script-client positives must also run for both modes. Request query token exchange retains redirect semantics, malformed multipart returns controlled error and SSE remains session-guarded without CSRF query tokens.

- [ ] Run web guard/conversation/async-route nodes. Add mode variants to SAMPLES-driven guard coverage without duplicating/removing route inventory keys; assert real OpenAPI inventory equality. Verify baseline valid no-Origin request remains accepted, not a new universal Origin requirement.
- [ ] Implement unchecked checkbox/history/job badge actual order/help and incomplete/recovery diagnostics; never enable without certification. Existing page action=agent and API `/runs/{slug}/conversation/agent` use R5 TARGET-only/independent-protection selection for guarded confirmed reset/recovery and compare expected revision INSIDE reservation; render current token for both form types. Same-manager non-reset increments generation but cannot silently clear K uncertainty/session. Malformed unrelated charter does not block selection; next send validates it fully. Display integrity error for malformed generation, not rewrite zero; old session/job history visible but never reusable authority. User chooses ordinary chat alternative, no auto-resend. No terminal pane/socket/login/credentials/role reassignment.
- [ ] GREEN actual socket cancellation/health/SSE remain responsive while a synthetic K turn holds admission and while cancellation polls; auth still enforced on bypass. DOM/behavior test must exercise immediate repeat suppression and reset, not merely search template strings. Prefer an already available browser or deterministic browser fixture; if unavailable, report the missing behavioral gate rather than inventing a screenshot/pass or installing dependencies without consent.
- [ ] Independent spec then security review: retained auth, request-origin derivation, stale state/recovery controls, escaped badge/error content, residual Host/Origin/rebinding/same-origin risk and absence of hidden permission escalation. If the retained guards make K unsafe, keep enablement blocked pending separately approved HTTP hardening; do not implement that hardening here.

### Task 8: Gated actual capability evidence, compatibility and whole-change acceptance

Depends on accepted Tasks1–7, incorporated human C1/owner D0–D5, G3 exact human plan approval and separate G4 live consent. Files: proposed RUN/tools/k_harness_capability_probe.py; separately approved synthetic fixture/identity evidence under scratch; reviewed finite policy entry ONLY after G5; docs/web.md and affected runs/sandbox explanations. Never edit/install/substitute production skill to force PASS. Missing future live evidence is NOT a prerequisite to independent PLAN readiness.

- [ ] Offline RED/GREEN first: test probe-report validation with absent/mismatched identity, zero-exit-but-no-interception, missing charter bytes, unexpected tools/model/permission changes, fork/background work, unknown command and incomplete output. Reject each before certification. Preserve a deliberate failing certification control; a fabricated/synthetic result must never populate production policy.
- [ ] G4 exact attachment: harness resolved binary/digest/version and effective templates/staffing; canonical cwd/discovery and finite safe effect-bearing extension/settings identity, immutable skill bytes, complete first/resume/argv/stdin/interception/ordinary/disabled/failure matrix, actual identities/storage paths and allowed filesystem effects/synthetic data. No remote/upload/credential inspection/permission change. Enumerate EXACT non-secret bounded local inputs checked on future GET/send; no arbitrary version command, unbounded discovery, dynamic uninspectable hooks or unpinnable writable effects. Separately establish version↔binary identity under this gate. Fixture installation/moved cwd certifies ONLY that fixture identity; production evidence must match intended deployment explicitly or support stays disabled there.
- [ ] Eight invocations/60 seconds each/12 minutes total remain PROPOSED MAXIMA, not approval or permission to omit matrix cases. G4 sets enforceable/observable provider-spend ceiling and actual identity/effect matrix BEFORE ANY call; no ceiling enforcement means no run. Approved synthetic timeout is probe-only, not new production capture/kill policy. Preserve staffing/permission argv; no login/template rewrite/bare mode workaround. If complete coverage cannot fit ceiling, stop for revised bounded consent, no silent ninth call or dropped case.
- [ ] Only after G4 scope/security consent run synthetic `/scieflow-k-probe` mechanism controls and separately exact raw no-argument `/scieflow-menu` deployment controls. C1 is already closed, no repeat candidate question. Actual expansion/interception evidence is not a model echo marker; inert k-protocol-fixture is OFFLINE plumbing only. Capture raw/expanded argument path, full charter exactly once, usable first/resumed session, forced-stdin equivalence and actual unchanged model/tools/permissions/effects; finite completion without user/terminal input, no unexpected fork/background/config/workflow effect outside consent. Ordinary/disabled and unknown-name/failure/partial/cancel controls all covered within explicitly approved matrix. A probe-only disabled flag difference requires G4 enumeration, never production-template rewrite.
- [ ] Review actual artifacts and resulting identity against every offline assumption. If full expansion visibility or effect identity is unavailable, capability remains unknown/disabled; success exit or text marker alone is insufficient. A probe refusal is a boundary, not a reason to trust-bypass, rerun until green or select another model/skill.
- [ ] G5 independent security/whole-change review accepts actual evidence before adding the one finite certification entry. Then source-selected full offline suite, legacy check, final strict docs, whitespace and real sandbox/cache/provenance/cancel positive controls, plus separate UI behavior evidence and gate reconciliation. Use a fresh Sol writer for any policy/evidence docs change, with independent review; no broad catalogue.

Update docs/web.md to distinguish ordinary chat/marking/final parsed reply/raw job-output JSON, empty/verified finite support, exact raw grammar, cap UNITS and selected shared display/file_view effects, separate run-event stream boundary, explicit reset/generation/no queue/replay and CLI-only rulings. Document Linux supported unsafe/busy refusal for ordinary operations and unsupported-platform ordinary compatibility WITHOUT K guarantees; K disabled there, no lock fallback. Chats/DVC/promotions/remote still separately gated. Explain current permission-skipping templates, filesystem/network/credential and Host/Origin/disk residuals. Human token does not consent to consequential workflow actions.

Final future verification recipe, after exact K source helper acceptance (not executed now):

```sh
cd "$K"
"$ROOT/.venv/bin/python" -B "$RUN/tools/run_check.py" k-final-offline-a1 -- \
  "$ROOT/.venv/bin/python" -B "$RUN/tools/with_project_fixture_mount.py" k-final-offline-a1 -- \
  "$ROOT/.venv/bin/python" -B "$RUN/tools/k_feature_pytest.py" --worktree "$K" \
  -o "pythonpath=$K/src $K/scripts" -p no:cacheprovider --basetemp @BASE@ -q \
  --junitxml "$RUN/scratch/k-final-offline-a1.xml"
"$ROOT/.venv/bin/python" -B "$RUN/tools/run_check.py" k-final-legacy-a1 -- \
  env UV_NO_SYNC=1 bash scripts/check_legacy.sh
"$ROOT/.venv/bin/python" -B "$RUN/tools/run_check.py" k-final-docs-a1 -- \
  "$ROOT/.venv/bin/python" -B -m mkdocs build --strict --site-dir "$RUN/scratch/k-final-site-a1"
git diff --check
```

Source guard must also verify CLI subprocess import origin or disclose the same shared-install legacy limitation; don't claim every legacy subprocess exercises K just because in-process pytest does. Default configured live/slow deselections remain explicit; separately consented K harness evidence is not a green run of every live test. Retain JUnit unique cases/counts, actual skip reasons, all command exits and mount cleanup; no fixed expected pass count copied from Ticket22. MkDocs excludes `superpowers/`, so strict build is project documentation verification, not semantic lint/rendering of this internal plan. Independently validate internal source ranges, signatures, decisions and acceptance rows.

## Complete accepted-design acceptance and gate mapping

Design references below point to immutable approved snapshot lines, not proposed API existence. Every row needs actual evidence before enablement/merge; authoring this matrix is not passing it.

| ID | Approved requirement / design lines | Owning tasks and required evidence / controls | Gate |
|---|---|---|---|
| A01 | Human command first/charter last once; slash chat/generated/merge first (17–19,250) | T2 exact argv/stdin; T4 full service; opening/merge/module ordinary regressions; T8 expanded charter evidence | G3,G5,G6 |
| A02 | Spoofed actor/marker/files/YAML/skill/reply cannot authorize; legacy chat (94–104,118–125,251) | T1/T2/T4 stored forgery and caller-default negatives; T7 guarded object creation; no replay reader | G3,G5 |
| A03 | Accepted bytes, first byte/NUL, prompt swap, literal token boundaries (103–106,252) | T1 exact grammar; T2 no-artifact immutable resolution/pure composition; T4 same PreparedTurn launch/config-grant-template-charter mutation controls/argv+stdin raw audit match | G3,G5 |
| A04 | 64 KiB marked input only; charter/argv separate (107–108,253) | T1 byte-limit utility; T2 expanded stdin; T7 no shared body/chat/draft ceiling, large-chat compatibility | G3,G6 |
| A05 | Durable mode/order/digests/job/session correlation incl reload/cancel/no id (110–125,254) | T4 schema v1/old Job defaults, turn/job linkage, atomic finalization+generation/pending errors; T5 incomplete id/recovery, no audit authority | G3,G5 |
| A06 | Finite code-owned named support, unknown/effective-identity mismatch disable (70–91,255) | C1 human-closed NOT certification; T1 empty exact non-secret finite policy; T7 disabled help; T8 actual deployment identity distinct from fixture | G2,G4,G5 |
| A07 | First/resumed argv/stdin usable session, expansion full charter, invariant staffing/tools/permissions (45–46,89–91,166–170,256–257) | T2 original safety regressions; T8 actual interception/ordinary/disabled controls and effects evidence; no Codex/agy fallback | G4,G5 |
| A08 | Protected nonqueueing send/hand-over reservation through finalization (172–189,258–259) | T2 ONE policy-derived independent TurnProtection/target-snapshot contracts; T3 reservation BEFORE resolution/Linux stable flock/process+thread/full grant-alias-ancestor-project-coordinator/sandbox denial and invalidation; T4 R5 first/broken-current/no-launch/load-count/start_run matrix plus same protection through send/finalization, R2 stale/A→B→A; T7 guarded matrix. Unsupported K-refusal/ordinary compatibility separate, no Linux fallback. | G2,G3,G5 |
| A09 | UI busy/repeat suppression and no replay/idempotency promise on disconnect/loss/restart (184–199,260) | T4 loss/ack failure scenarios; T7 real DOM behavior; T8 incomplete/cancel effect recording; explicit human recovery | G3,G5,G6 |
| A10 | Budget/gates/narrowed sandbox/provenance and unsandboxed refusal (159–170,261) | T3 real host/child positive-negative sentinel+provenance hash; T4 spend/budget/refusal; unchanged gate tests | G3,G5,G6 |
| A11 | Retained session/CSRF/SSE, both modes, malformed bodies, token exchange, script clients/inventory (128–144,262–264) | T7 concrete auth positives/negatives for two POSTs; no-Origin compatibility, real SSE auth, inventory equality, synchronous-route tests | G3,G5,G6 |
| A12 | Residual Host/Origin risk; separate hardening approval; required premerge security (134–155,273–275) | T7/T8 independent security verdict; retained risk explicit; unsafe feature blocks enablement, not unapproved shared hardening | G5,G6 |
| A13 | Escaped HTML, ANSI/OSC/bidi/control/split/huge line/DOM/display limits (201–213,265) | T5 buffers; T6 selected panels/history/argv/error/file inline/job-log SSE: UTF-16 display and post-sanitation framed UTF-8 wire caps including notices/astral tests; _events no raw K payload | G2,G3,G5 |
| A14 | Bounded reads before materialization, exact raw audit separate, incomplete parse honest (212–218,266–267) | T5 handle-bound regular-file/trusted naming/read sentinel/exclusive copy/hash/mutation; T6 SAME fd through inline/download/response consumption/range/HEAD/type-race/FD cleanup, precise incomplete notice | G3,G5 |
| A15 | Session usability/recovery, no silent restart/replay/fallback; honest errors (219–231) | T4/T5/T7 deliberate authenticated confirmed revision-checked reset/session None/A→B→A/response-loss/malformed generation; R5 first/broken-current recovery under full independent protection, TARGET-only validation, malformed-charter nonlaunch versus full-send refusal, same-manager generation/session distinction and invalid-target/unsafe/busy/stale zero mutation. No old-id guess, certification or skipped lock; actual process state preserved. | G2,G3,G5 |
| A16 | No whole-job capture/kill policy / hidden limits on other jobs; disk residual (224–228,267) | T5 ordinary parser unchanged; T6 agreed shared display scope documented, raw file retained, non-K job state/capture unchanged | G2,G3,G5,G6 |
| A17 | Each CLI-only item/exclusions independently preserved (233–244) | T7/T8 docs + unchanged remote/upload/chats/gate/tier regressions; no credential/permission controls or new pane/socket | G3,G5,G6 |
| A18 | Existing conversation/charter/merge green; full offline/legacy/strict docs and independent whole-change (248–275) | T8 actual source-selected executions, JUnit/positive controls, exact diff security/spec/quality review, direct internal-plan validation | G6 |

## Gates and handoff

| Gate | Authority / required state | What it does NOT authorize |
|---|---|---|
| G0 | Existing native human design approval at exact snapshot; satisfied, do not reask | This plan, implementation or live commands |
| G1 | Original t_70194de3 FINDINGS; completed round-1 independent t_8a14cb79 closes original R1–R4 at plan-contract level ONLY, returns R5/DOC1 FINDINGS. This R2 correction releases pre-created Astra/Ultra t_8f0cd369 to independently re-review R5/DOC1 and fix-caused consequences in actual full artifact/handoff | Author correction/structure/seal cannot establish semantic accuracy or readiness; reviewers cannot answer human gates; no feature/security acceptance from plan-level closures |
| G2 | Owner run32 D0–D5 and human-closed C1 retained; owner run35 selects precise independent TurnProtection/target-only R5 and new DOC1 erratum, incorporated here. Only independent re-review closes these refinements before exact-plan readiness | No six-question/general go-ahead repeat, self-approved technical closure, skipped protection/half-dispatch object, substitution/skill rewrite/live evidence prerequisite |
| G3 | Sole owner freezes independently READY exact revised plan through native human gate with usable answer route; human approval explicitly covers future K local test-base preparation plus selected shared file_view/display/platform scope and reconciled R5 selection-versus-send protection/compatibility. Accurate NEW R2 handoff accompanies it, not inaccurate sealed R1 rows | No present carry-forward, branch integration/commit/merge/relocation, live/effect/spend probe, upload or permission escalation |
| G4 | Separately human-approved exact future probe fixture/effects/budget and independent probe security scope | No production command certification or widening beyond stated eight-call ceiling |
| G5 | Independent security review of full actual capability/effect/output/admission/auth evidence before enablement | Process exit or synthetic marker alone cannot certify; hardening/capture policies remain separate |
| G6 | Owner full acceptance after separate task reviews, source-selected final executions and independent whole-change review; integration only under its own approval | No commit/push/merge/upload/relocation from local feature completion |

This correction completes ONLY revised proposal/direct documentation checks and releases pre-created independent t_8f0cd369. Original R1–R4 plan-level closures are preserved, not feature/security proof; R5/DOC1 independent closure and exact-plan readiness remain pending. No self-approved READY, K/Ticket16/owner completion, gate opening/answer, carry-forward/code/live certification or enablement is implied. Run31/event597 narrow recovery is resolved, not repeated as a blocker; each fresh worker still proves exact own runtime. Read current controls at safe boundaries, newer human pause/stop wins. Routine correction/dependency handoff stays INTERNAL; owner communicates the reviewed milestone. Genuine new blocker/usage pause uses unchanged explicit dual binding, with queued/Discord read-back/Desktop persisted/receiver-rendering UNVERIFIED separately stated. No Ticket22 completion/old blocker repeat, commit/push/merge/upload/backup/scientific work/fallback/policy change/relocation.
