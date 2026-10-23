# F + G — browser agent configuration and workspace health

Status: **PROPOSED — AWAITING USER APPROVAL**.
Design only; the recommendations below are not user-approved defaults or an implementation plan.
Evidence baseline: `0d7df13e5567ffba1bfe989765346079aff7de47`.
Sources: tickets 11 (design questions) and 12 (implementation invariants); neither ticket is closed.
Paths/line numbers below refer to the repository at that baseline.

## Recommended first scope

Extend the existing Agents page with structured supported settings, explicit scope, inherited values and preview/apply.
Provide a read-only workspace index and health findings; no repair, cleanup, migration, remote check or upload controls.
Keep support-as-primary promotion **and demotion** CLI-only initially; display inherited/local exceptions read-only.
Show `enabled` read-only too: project enabled editing is deliberately deferred in the browser, not removed from the CLI.
K is harness input, not `scieflow agent configure --promote`; it does not fill that configuration gap.
These choices need human approval before planning or code; ScieFlow staffing gates and configured harness permissions remain unchanged.

## Existing contracts and gaps

| Area | Evidence |
|---|---|
| CLI scope and operations | `src/scieflow/core/cli.py:211–247,266–285`: default, `--workspace`, `--news`; mutually exclusive news/workspace; assign, set, unset, promote, demote; diff and confirmation. |
| Settable fields | `src/scieflow/core/agent_config.py:53–55,78`: project fields `model`, `reasoning`, `timeout_min`, `cmd`, `stdin_cmd`, `enabled`; workspace excludes `enabled`; per-role overrides allow only model/reasoning. |
| Effective view | `src/scieflow/core/agent_config.py:107–122,157–237`: registry/defaults/run overlays, value/source, tiers, exceptions, role overrides, problems/warnings and legacy precedence. |
| Planning/writing | `src/scieflow/core/agent_configure.py:293–406`: round-trip YAML, effective validation, scoped deltas; `:275–277` writes each planned file separately. |
| Web today | `src/scieflow/core/service.py:838–839,857–888`; `src/scieflow/web/pages.py:182–219`: project/workspace role assignment preview/apply only. API `/agents` is read-only (`src/scieflow/web/api.py:86–96`). |
| Forms | `src/scieflow/web/templates/agents.html:7–22,37–60`: project/run selector, role-agent picker, GET preview, CSRF-protected POST apply; no news or field form. |
| Workspace | `src/scieflow/core/workspace.py:108–166,253–300,335–365`: list/aliases/lineage/moved entries, read-only doctor findings, CLI index writes `workspace/INDEX.md`. |
| Service/project seam | `src/scieflow/core/project.py:55–83` binds project paths; `src/scieflow/core/service.py:122–134` reads run detail, not doctor. No browser doctor wrapper exists today. |

The distinction between a role assignment and a conversation manager stays visible.
`service.conversational_agents`/`set_conversation_agent` (`service.py:842–854,1003–1018`) use registry session capabilities,
not a coordinator role in `agent_config.ROLES`; editing a role must not silently switch a run's conversation manager.

## Fields: editable, deliberately excluded, unsupported

| Field/operation | Proposed browser treatment | CLI semantics/evidence |
|---|---|---|
| Project model/reasoning | Edit known agents; menu suggestions plus custom value where CLI permits; preserve warnings. | `agent_configure.py:310–326`; model validation and reasoning/menu warnings at `agent_config.py:358–379`. |
| Workspace model/reasoning | Scoped override or inherit/reset. | `agent_configure.py:370–397`; supported, stores differences only. |
| `timeout_min` | Positive finite number, minutes, project/workspace. | `agent_configure.py:63–75`; `agent_config.py:351–357`. |
| `enabled` | Read-only status; project editing deliberately CLI-only in this first slice. | Project setting is CLI-supported; workspace setting was already unsupported (`agent_configure.py:378–382`). Disabling a project-assigned agent fails project validation (`agent_config.py:293–295`), not a scan of all workspaces. |
| Role assignment | Project/workspace single or fan-out chooser according to `ROLES`; per-entry optional model/reasoning. | `agent_config.py:37–51,138–154`; `agent_configure.py:78–105`. |
| Role-level model/reasoning | Structured representation of existing assignment syntax, not separate invented role settings. | `AGENT@MODEL/EFFORT` and `AGENT@/EFFORT`; `agent_config.py:240–257,316–326`. |
| News agent/model/reasoning/timeout | Separate news form, with news validation and timeout in **seconds**. | `agent_configure.py:409–468`; `news/config.py:91–107,158–166`. |
| `cmd`, `stdin_cmd` | Deliberately CLI-only although configurable in project/workspace. | They are executable templates, not harmless preferences; accepting arbitrary templates would enlarge web authority. |
| Promote/demote | Deliberately CLI-only although the CLI supports both. | Explicit tier exceptions with inherited scope rules; see decision below. |
| New agent, tier, menu, family, session/resume command | Unsupported by `agent configure` field editing, not falsely described as CLI-settable fields. | New agents require registry editing by hand (`agent_configure.py:313`); these fields are absent from `AGENT_FIELDS`. |
| Raw environment, secrets, login/permission flags | No browser editor. General environment/credential editing is not an operation of this CLI configurator. | Environment behavior is outside these structured fields; existing raw templates may encode it, which is deliberately excluded. |
| News roles, enabled, timeout_min, cmd/stdin_cmd | Unsupported in news scope; reject forged submissions, do not silently translate. | `NEWS_FIELDS` and role refusal (`agent_configure.py:409,427–430`). |

Unsupported is different from deliberately excluded: show that distinction in help/errors and server validation.
Use a strict browser operation allowlist; disabling a form control is not sufficient to constrain requests.
Display menus as suggestions, not verified live model availability; do not call model discovery or launch quota-consuming probes.
General reasoning is sometimes only a label: CLI warns when templates lack a `{reasoning}` placeholder;
role-level Claude `extended-thinking` has its own command-prefix semantics (`agent_config.py:240–257,364–379`).
Do not write templates/environment to “make reasoning work” from the browser; retain that warning and direct users to the CLI.
Timeout/model/menu validation does not certify provider access or selected-harness capabilities.

### Enabled editing deferral

Browser set/unset of project `enabled` is excluded server-side, including forged operations; workspace `enabled`
remains unsupported by the core configurator. The project exclusion is a deliberate first-slice deferral,
avoiding a new project-wide workspace-membership/read-set/disablement policy, not a new CLI restriction.
No browser disable action scans or promises validation of every run. Existing CLI project disablement validates
registry/default assignments (`agent_configure.py:325–326`); it does not stop an in-flight job, does not govern
the separate news adapter, and existing `say` does not enforce enabled for an already-selected manager
(`service.py:353–360`). Show those limits and never silently switch the conversation manager.
Deferring the browser field does not invent stronger disablement or hot-reconfiguration semantics elsewhere.

## Scope, effective values and reset semantics

Always show the bound project root and one explicit scope: Project defaults, Workspace `<slug>`, or News settings.
Workspace requires a validated selected run; news and workspace together are refused, never coerced to project defaults.
Proposed URLs extend `/agents` and `GET /api/v1/agents` with an explicit scope discriminator;
retain existing no-scope/default and `?slug=` read links as unambiguous compatibility aliases.
Writes must carry explicit scope and, for workspace only, canonical slug; missing/ambiguous write scope is an error.
Project changes affect inheriting runs: preview states that clearly and does not imply existing overrides are overwritten.

Read views show stored local value, inherited value, effective value, source and validation warnings beside each setting.
For dispatch-specific role choices show role override precedence separately from the per-agent value.
Project unset means removing an optional registry field, not “restore shipped defaults”; required assignments cannot be unset.
Offer removal only where the existing planner validates the resulting configuration; do not cache factory defaults.
Workspace reset uses `--unset` semantics, removing only the selected assignment/field override.
Setting a workspace field equal to its registry default removes the delta (`agent_configure.py:389–394`);
role equality pruning has a legacy-shadow exception (`:363–369`), so show planner results rather than promise automatic removal.
A role reset can expose legacy run keys again (`agent_config.py:169–194`); preview the actual effective result and label its source.
Do not edit/delete legacy keys as a hidden repair, promote run edits into defaults, or normalize unrelated YAML.

News is **not** a run overlay on project agent settings: it uses its own restricted adapter.
`news_settings` defaults agent to Claude and leaves unset model/reasoning/timeout as None (`agent_configure.py:460–468`).
Removing news `timeout` selects per-interest template timeout (`src/scieflow/news/runner.py:60–75`), not project `timeout_min`.
Removing model/reasoning delegates to the news adapter/harness default, not an invented registry-inherited value.
The news adapter shares model suggestions only, not command templates (`src/scieflow/news/agents.py:21–36,47–72`).
No global `enabled` semantics are promised for news; core registry disablement must not be claimed to disable its adapter.
Missing `config/news.yml` shows “initialize news through its CLI” and a disabled form, not automatic creation.
News support-tier agy selection remains an explicit user choice with its existing warning;
recommend a separate confirmation of that choice, never a default or automatic fallback.

## Promotion decision and human alternative

Recommended first slice: read-only exception badges and CLI help, no promote/demote buttons or hidden operation payloads.
Reason: promotion changes the tier safety contract, not merely a field; inherited exceptions add to local ones,
and a workspace cannot demote an inherited project exception (`agent_configure.py:347–361`).
Removing an exception can invalidate staffing; demotion needs full revalidation too, not a cosmetic toggle.
This resolves ticket 11's “typed confirmation or K” question without falsely presenting K as CLI execution.
Update `docs/web.md:506–514` to say supported structured field edits are available;
exceptions, executable templates and project `enabled` editing remain deliberately CLI-only in this slice.

Alternative requiring a separate explicit user decision: scoped typed-confirmation exception editing.
It would show affected role/agent/tier and inherited scope, require the exact displayed role plus a written reason,
retain normal human promotion approval/gate and run-log requirements, preview the effective result and revalidate at apply.
Project demotion would show inheriting runs; no workspace negative override is invented to cancel a project exception.
Do not implement or automatically suggest promotion to rescue an invalid assignment; surface the refusal instead.

## Shared service, routes and forms (proposed)

Extend shared core service planning/apply/read functions over `agent_configure` operations, not subprocess CLI calls.
No new service symbol is asserted as existing; use `plan_defaults`, `plan_workspace`, `plan_news` for the corresponding scope.
Use request-bound `Project`, never process cwd, arbitrary request file paths or ambient `config.repo_root()`.
Validate workspace slug via `Project.run_dir` and containment before reaching `acf.workspace_dir`, which simply joins paths.
Resolve symlinks; refuse control/config paths escaping the bound project/run or non-regular control files for mutation.
The new index/health reads have the same open-time safety requirement and explicit byte bounds described below;
path resolution before open is not sufficient protection against replacement races.
Keep the news config path server-selected; do not accept CLI `config_path` override from HTTP.

Recommended form flow: GET read view; guarded POST preview; guarded POST apply with the explicit scope and revision.
Preview writes no configuration and shows changes, notes, warnings, inheritance impact and excluded-operation errors.
Prefer POST preview for new free-text settings so values do not go into query strings/history/access logs;
retain existing GET assignment previews without putting CSRF tokens in GET forms.
The apply control confirms the displayed structured diff; do not accept a browser-supplied YAML diff as authority.
On success use 303 to a fresh scoped read; validation failures retain submitted safe fields and explain the refusal.
A JSON counterpart for structured preview/apply under `/api/v1/agents` is proposed, not present today.
Health page/API route names below are also proposals, not claims about existing endpoints.

New handlers are plain `def`; preserve the declared async SSE allowlist (`tests/web/test_async_routes.py:57–87`).
All unsafe routes, including preview/check POSTs, carry the session/CSRF dependency and belong to both
`MUTATING_PATHS` and `SAMPLES` (`tests/web/mutating_paths.py:18–70`), even when their business operation is read-only.
Every POST form carries `name="csrf_token"`; never put it in a query string (`tests/web/test_agents_page.py:19–28`).
Continue central middleware body handling and no CORS (`src/scieflow/web/auth.py:80–126,147–181`).
The first slice preserves today's authentication/CSRF and SSE session contract, with no universal Origin/Referer
requirement or Host/tunnel migration. `web/auth.py:133–181` and `web/app.py:27–85` contain no explicit Host/Origin
checks; loopback binding, SameSite and no CORS do not solve DNS rebinding. That residual risk remains documented.
Malicious cross-site/session/CSRF/SSE tests exercise the retained guards, not an invented absent-Origin rejection.

### Separately bounded shared HTTP-hardening follow-up

K and F+G share a follow-up, not a dependency on implementing all of K first. Before separately approving
Host/Origin policy, define a trusted configured public-origin/allowed-authority seam (not present in
`create_app(project, token)`), decide different-port tunnel and cookie-plus-CSRF script-client compatibility,
and specify migration tests for token exchange, redirects, forms/API and SSE. Never derive trust from request
or forwarded headers. This first slice neither implements nor silently adopts that migration.
K's required pre-merge security review evaluates residual risk; if existing guards make new feature enablement
unsafe, block it pending separately approved hardening rather than weakening guards. F+G must likewise retain
its guards and review its added mutation surface without treating K's entire implementation as a prerequisite.

## Validation, stale edits and concurrency

Reuse CLI parsing/coercion and effective validation; preserve “warning” versus blocking “problem”.
Reject unknown agent/role/field/operation, forbidden scope, non-numeric/nonpositive timeout and invalid booleans;
also reject nonfinite timeouts at the shared validation seam, a gap in today's `<= 0` validation.
Reject empty model strings; retain menu warnings for otherwise CLI-permitted custom model/reasoning values.
News positive integer seconds and its restricted reasoning set use its own validator, not core minute conversion.
Validate fan-out nonempty/unique assignments, enabled agents, tier pairing and different reviewer/submitter
with `agent_config.validate` (`agent_config.py:265–379`); never promote automatically on refusal.
Do not offer arbitrary role timeout fields: the existing role override schema only supports model/reasoning.

Current `apply_staffing` replans, but does not compare against the preview (`service.py:880–888`).
`acf.write` locks each file's replacement, not the planning snapshot or the entire multi-file change;
`store.update_yaml`'s safe transaction is not currently used by that planner (`store.py:60–84`).

### Authoritative configurator compare/replan/apply owner

Propose one shared apply seam in `core/agent_configure.py`, reached by the browser service, CLI configure
and both TUI save paths. This is a new contract, not an existing symbol: today's `Plan` has only changed-file
before/after strings, notes and warnings (`agent_configure.py:40–58`); CLI and TUI hold it across confirmation
(`cli.py:266–286`; `menu.py:488–491,638–645`). Adding a writer lock around that old blind write is insufficient.

The planned change retains canonical bound project/scope, workspace slug or CLI news config path where supported,
canonical operations, approved targets and complete expected dependency contents or digests, including explicit absence.
The browser's revision binds scope/operations and this dependency snapshot; submitted YAML/before/after is not authority.
Trace every real planning/validation read, not just changed targets. Baseline examples are registry plus defaults for
project planning, those plus run `config.yml` and `status.yml` (whose presence affects notes) for workspace planning, and the full selected
news document for news validation (including unchanged interests/groups). Registry-backed model suggestions also
belong in the expected set when they contribute to preview validation/warnings. Code-owned schema/template changes
invalidate the preview revision too. Implementation must prove its actual read-set and absence checks with tests;
these examples are not permission to omit further dependencies discovered in the real validation path.
Capture complete contents/digests even for dependencies consulted only for presence, not just an mtime or changed-target revision.

Proposed cross-process protocol: one owner in the configurator acquires the canonical project-scoped
`config/.agent-configure.lock` first, shared by every scope and all three frontends, then existing sibling
`<path>.lock` locks for the union of dependencies/targets once in canonical absolute-path order.
Preview captures/plans under that protocol and releases it before human confirmation. Apply under the same
protocol rereads and compares every dependency (including absence), then replans/validates from canonical operations
and that snapshot, and replaces only the approved targets if the approved result still matches.
Unchanged dependencies matter even when they are not targets. Newly discovered dependencies, changed contents,
operations, scope, target set or result refuse as stale/re-preview before any replacement, including `--yes` calls.
There is no blind stale before/after write or automatic apply of a new diff.

The owner passes already-held file locks to an already-locked atomic replacement path; it must not nest a fresh
incompatible `FileLock` via `store.write_text`. `store._atomic_write` is the current lock-free replacement primitive
(`store.py:47–64`); the new boundary must make its ownership explicit rather than expose an unlocked public shortcut.
Acquire each lock once; release all locks before waiting for human confirmation or model execution. Contention
refuses with retry/re-preview guidance. This protects cooperating configurator/store writers using that protocol,
not arbitrary manual/agent filesystem edits, which can still race after comparison and must not be called locked out.

The one-target-file-family restriction is **browser-only**: project registry fields or project role assignments
per apply, with both files still read/validated. Workspace/news browser applies each target one file.
Preserve the CLI/TUI's supported mixed registry/default changes through the same shared apply boundary, serializing
with browser writes. Those replacements are individually atomic but a mixed multi-file apply is not crash-atomic;
if a later replacement fails, report written/unchanged/uncertain targets and require reread/re-preview, not rollback promises.
Preserve comments and unrelated keys via the existing round-trip planner. Stale/invalid/pre-write refused changes
leave all target bytes unchanged; partial replacement failures are a separate honest outcome, covered by read-back tests.

An edit does not rewrite an in-flight job's argv/session or stop it. No coherent multi-file dispatch snapshot is
promised: current dispatch reads do not participate in this configurator boundary. Such a guarantee requires
explicit participation by the relevant read boundary in separately reviewed work. No hot-reconfiguration or
silent conversation-manager switch is introduced, and browser enabled editing remains deferred as specified above.

## Workspace index and doctor: cost matters

Recommended `GET /workspace`: in-memory index using the semantics of `workspace.list_runs` and `moved_entries`, showing kind/state,
last activity, aliases, lineage and links to safe canonical run pages. It neither invokes CLI `index` nor writes `INDEX.md`.
The CLI's index explicitly writes a generated file (`workspace.py:335–365`); browser rendering is deliberately read-only.
Respect existing exclusions of module/hidden/underscore directories and alias grouping (`workspace.py:134–166`).
Never follow an external alias into a health scan; present safe alias information or a containment refusal.

### Safe control reads for index and health

New index/health views must use contained, regular-file, byte-bounded control reads, not call today's whole-file
`describe/_load` unchanged (`workspace.py:50–55,108–130`). Proposed per-control cap: 256 KiB of raw bytes,
reading at most cap plus one byte to detect oversize before decoding/YAML parsing. Refuse oversized controls
rather than parse a truncated document; report the cap and refused/incomplete state. A fixed count of YAML reads
is not a byte or latency bound. Control presence/metadata checks and DVC pointers follow the same safe-path rules.

At open/read time, anchor path traversal to the bound project/run directory using directory handles and
no-follow component opens (or an equivalently verified containment primitive), open nonblocking where needed
so a substituted FIFO cannot block, verify the opened descriptor is a regular file, then perform bounded reads.
The initial browser slice may refuse control-file symlinks entirely; external symlinks and FIFOs/non-regular controls
are always refused/reported. A prior `resolve`/`is_file` check alone cannot authorize the later read.
Directory/alias handling must also preserve containment through open; do not follow traversal symlinks for scan work.
Detect/report replacement or change observed during reading; no coherent snapshot across arbitrary concurrent edits is promised.
Malformed, invalid-shape, unreadable, missing and oversized controls produce distinct safe findings, not silent `{}`.
Keep raw control content, YAML source snippets and secret-adjacent values out of diagnostics; use relative path,
category and safe reason only. Apply the same discipline to new scoped config read/preview inputs.

Initial index enumeration is top-level only, streamed and proposed bounded to 10,000 inspected entries;
sort only the bounded collected results and mark omissions as partial. No eager unlimited `sorted(iterdir())`,
glob/list/count or recursive scan on GET. Alias grouping/lineage can be partial and must be labelled accordingly.

Recommended `GET /runs/{slug}/health` (plus read API): cheap findings only, from shared core read primitives.
Proposed factoring of doctor separates cheap checks from traversal without copying divergent web business logic.
Cheap: kind/state, expected control-file presence, legacy config keys, old per-file DVC pointer, alias identity;
these are a fixed set of local reads/stats with the byte/open-time bounds above (`workspace.py:253–267,286–300`).
`scripts_in_logs` enumerates a directory; junk sizes/self-links traverse the run, so they are explicit scan findings.
Show these as “not checked” on initial GET, not healthy/zero. No recursive scan, repair, provider/model call,
sandbox probe, git sync or remote health test runs. Local filesystem calls can still block; no guaranteed instant response is claimed.

Existing doctor is local/deterministic but not necessarily cheap: it walks to depth four, measures skipped dirs recursively,
and `_size` silently stops after 200,000 files per directory (`workspace.py:239–250,268–284`).
Depth limits do not cap breadth or total work; do not run today's entire doctor on every dashboard/page GET.
Recommended first scan action: guarded synchronous `def` POST `/runs/{slug}/health/check`, explicit user initiation,
using a bounded shared doctor reader; no job/SSE daemon, external process or model dispatch for this local check.
Proposed aggregate streaming entry budget: 10,000 entries across ordinary traversal, nested junk-size work
and logs enumeration together, with a cooperative two-second elapsed-time target checked between operations.
Count all yielded files/directories/symlinks, even skipped entries; repeated enumeration also consumes budget.
Use incremental directory iteration rather than eager glob/list/count or an unlimited nested `_size`/walk that
bypasses the shared budget. Stop before the next operation once a limit is observed; one blocking filesystem
call can exceed two seconds, so this is not a guaranteed wall-clock ceiling or response-time promise.
Expose complete/partial/not-checked status per check, partial size estimates/lower bounds, observed entry count,
stop reason and observation timestamp. Never report partial bytes or counts as final totals or unchecked checks as passed.
These defaults need approval; they are not measured performance results. Use controlled fixtures and a monotonic
clock for cooperative-limit tests. Preserve CLI doctor categories, depth-four ordinary traversal and existing
default behavior without web limits (including its historical `_size` cap); web budgets are explicitly supplied,
not silently installed as CLI defaults. Bounded scans retain the same finding semantics when fully checked,
while byte-safety refusals and budget stops add honest incomplete states.
No stored findings file is required initially; display an observation timestamp and that results may change during a run.
If later measurements show full useful scans exceed the agreed request budget, a separately approved full-scan job
can reuse existing jobs/cancel/SSE. The current subprocess-job primitive is not an existing in-process doctor scheduler.
Do not add background complexity on speculation or make a GET enqueue work.

Render report categories with observed values and severity: missing required files, legacy/honored keys,
rebuildable directories/sizes, absolute self-links, scripts in logs, per-file pointer and aliases.
“Rebuildable” is advisory, not permission to delete; legacy keys are honored, not necessarily failures.
Provide documentation guidance, not repair buttons or commands executed through K.
Health says nothing about scientific validity, model access, remote readiness or archive completeness.
The workspace loader can hide malformed/unreadable YAML as `{}` (`workspace.py:50–55`);
the safe-read contract above replaces that behavior for these new web views, never a falsely reassuring empty “all passed” report.

## Failure, degradation and safe rendering

Config parse/permission errors render scoped read-only feedback; never replace malformed files with defaults.
Missing workspace/news config, optional dependency, unavailable menus, stale revisions and lock contention have separate explanations.
Missing news leaves project/workspace editing usable; invalid existing effective config shows blocking diagnostics and a valid correction preview.
A filesystem failure leaves the previous file intact where atomic replacement permits; report any uncertain outcome and reread before retry.
No automatic reroute to project defaults, model fallback, live model query or repair-on-GET.
Doctor scan budgets, unsafe/oversized controls, unreadable directories and change-during-scan produce refused/partial/
warning state, not completion or an all-clear; blocking filesystem calls may overrun the cooperative target.

Names, model strings, diff text, paths, findings and errors remain autoescaped; no template `|safe` or HTML interpretation.
Use DOM `textContent` if enhancing the page; malicious names and newline/control text must not create attributes/scripts.
Display only structured safe settings; command templates/environment/secrets are not new form fields or copyable hidden payloads.
Do not dump full config documents into error pages or preview context; filter unrelated command-bearing/secret-adjacent diff context.
Existing effective API can include cmd/stdin_cmd (`agent_config.py:111–115`); do not falsely claim it is already a secret-safe projection.
Any response-schema/redaction change to that existing API needs compatibility/security review, not a silent break here.

## Acceptance and documentation corrections

- Field matrix and scope rules above are enforced server-side for page/API, including forged unsupported/excluded fields.
- Negative, zero, nonnumeric, nonfinite timeout and invalid model/news values explain refusal; read back every target unchanged.
- Enabled status is read-only; forged browser project enabled set/unset is deliberately refused, workspace enabled stays
  unsupported, and CLI enabled semantics remain unchanged without whole-workspace scans or silent manager switching.
- Model/reasoning menus/custom warnings, role overrides, fan-out/tier pairing and reviewer/submitter separation match CLI semantics.
- Workspace edits/reset never leak to other runs, defaults or news; news seconds/defaults never masquerade as run inheritance.
- Legacy-shadow reset/equality cases preview the actual effective value; default required-role unset refuses.
- Promotion/demotion remain read-only/CLI-only; K cannot substitute for either, and inherited exception sources are visible.
- Preview writes no config; the shared configurator owner covers browser, CLI and both TUI paths, comparing the complete
  real dependency read-set/absence plus canonical scope/operations before replan/write. Stale plans refuse even with `--yes`.
- Cooperating concurrent writers serialize with canonical single-owner locking/already-locked replacement, without nested
  FileLocks or locks across confirmation/model execution. Arbitrary filesystem edits are explicitly outside that guarantee.
- Browser mixed-file batches refuse, while existing CLI/TUI mixed registry/default applies remain supported. Mid-write
  failure reports non-crash-atomic partial state; already-running jobs remain unchanged and dispatch coherence is not invented.
- Malicious cross-site forms, bad/missing session or CSRF, malformed forms and unauthenticated SSE exercise actual retained
  guards; absent-Origin rejection/Host validation is not assumed. Valid script clients keep the cookie-plus-CSRF contract;
  all new unsafe routes have both inventories/sample cases and all POST forms have CSRF fields.
- Index/health GETs do not write INDEX/state, scan recursively, repair, start jobs or touch remote/model services.
- Open-time adversarial controls (external/replaced symlinks, FIFOs/non-regular files, malformed/unreadable/oversized data)
  are contained/refused with bounded reads and safe diagnostics; byte limits are tested before parse, not after whole-file loading.
- Index enumeration is streamed/bounded; explicit scans preserve CLI category/depth semantics and leave CLI defaults
  unchanged. Aggregate entry limits include nested junk and logs work; no eager enumeration bypasses them. Cooperative
  time checks, blocking-operation overruns and partial sizes/checks/reasons/timestamps are tested and reported honestly.
- Adversarial agent/model/path/error text renders escaped; no `|safe`; existing async-handler invariant remains green.
- Extend `tests/core/test_service.py`, `tests/core/test_agent_configure.py`, `tests/core/test_workspace.py` and `tests/web/`;
  preserve existing scope/preview/doctor tests (`test_agents_page.py:31–69`, `test_workspace.py:36–69`).
- Implementation acceptance requires the full offline `uv run pytest -q` suite and `uv run mkdocs build --strict`; neither is claimed here.

Update `docs/web.md:147,464,495–514` and `templates/agents.html:10–11` when implementation lands:
supported structured field edits cease to be blanket CLI-only; promotion/demotion/raw executable configuration and
project enabled editing remain excluded; workspace enabled remains unsupported, not a deferred supported field;
add exact scope/reset/news-unit/health-scan behavior and new routes without overstating CLI index/doctor equivalence.
Update the Agents guide with inherited versus role-effective values and default reset behavior;
keep news integration (item J), health repairs, archive browsing and remote checks outside this proposal.

High-level slices: structured scoped read/preview/apply over existing planners; shared validation/concurrency safeguards;
read-only workspace index and cheap health; explicit bounded scan plus templates/help and compatibility coverage.
These are review boundaries, not ordered implementation tasks or an execution plan.
User choices needing approval: field/scope matrix including read-only enabled, custom-value warnings, CLI-only exceptions
versus separately approved typed-confirmation alternative, news agy confirmation, browser-only file-family restriction,
shared compare/replan/apply protocol and control-byte/index/scan defaults. Shared HTTP hardening is a separate future
approval with its own configuration/compatibility decisions, not an implicit K prerequisite or adopted migration here.
Ticket 11's field/promotion/scope/repair/cost questions are answered above, with a failure section;
ticket 12's service/routes/forms/CSRF/invalid-value/scope/escaping/docs/full-suite invariants are acceptance requirements.
Explicit user design approval and strict MkDocs verification remain outstanding; tickets 11 and 12 stay open.
