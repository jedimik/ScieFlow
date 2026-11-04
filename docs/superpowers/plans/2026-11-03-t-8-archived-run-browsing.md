# T-8 plan: archived-run browsing, the design (Ticket 8, programme item B2) (round 2)

Pipeline `t-8-plan`, step plan, round 2, epoch 1. Card `t_d5557e54`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261103_105928_64013b`, source kanban).
Round 1 card `t_4c70716d` (session `20261103_102816_06ca3e`). Written 2026-11-03. Previous review:
`reviews/t-8-plan-review-r1.md` (CHANGES, F1-F3).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt, no claude/codex/agy.

### Round 2: review r1 findings closed

- **F1 (who turns a dead project-level job into `lost`) — closed.** New subsection 5.6.1 *Job liveness*: a GET
  reads the one job record named in `<slug>.job` directly by path (never `jobs.find`, which loads every job record
  of the project, `jobs.py:321-337`), decides liveness read-only from the record state plus the existing pid test
  (`jobs._alive`, `jobs.py:346-353`) and the fetch subcommand's own outcome record (matched by pid), and writes
  nothing. `lost` is written only by the new single-record `archives.reconcile_archive_job`, called under the start
  lock on the POST/CLI start and cancel paths (5.8). 5.5, 5.6, 5.8, 5.10, 5.14 and 5.15 now say the same thing; the
  pid-reuse residual is R9.
- **F2 (`locked` is non-queuing but `store.locked` waits 30 s) — closed.** T8-D14 and 5.8 now name a new
  non-blocking helper `store.try_locked(path)` (`FileLock` with `timeout=0`, raising the new `store.Busy`), distinct
  from `store.locked` (`store.py:22`, `:39-44`, waits up to `LOCK_TIMEOUT = 30` s). The start lock and the operation
  lock both use it and map `Busy` to `locked` at once. All four `dvc_sync.py` subcommands that take the operation
  lock (`push`, `pull`, `fetch`, `restore`) accept `--state-dir DIR` and otherwise use
  `Project(<repo root>).state_dir` (`$SCIEFLOW_STATE_DIR` or `<root>/.scieflow`, `project.py:68-71`); a refused
  subcommand exits `LOCKED_EXIT = 3` and writes nothing. Both helpers are listed in 5.11; the two new numbers
  (`JOB_RECORD_MAX_BYTES`, `LOCKED_EXIT`) are in 5.13 (now 16 names) and the t1 docs test.
- **F3 (t0 re-verifies only section-4 cites) — closed.** Section 4 now carries every cite that Part A makes
  (`store.py:22,39-44`, `DVC_STORAGE.md:114-118`, `data-protection.md:216`, `levels.py:170-171`, the jobs lines of
  5.6.1), and t0 step 3 (plan and brief) re-verifies every `path:line` cite in section 4 **and** Part A, building
  the list with a grep over both and recording the list it used.
- Non-blocking notes: the symlinked-zip case (DVC `cache.type: symlink`) is now decided in 5.5; the manuscript
  sentence is unchanged and is listed for the human as H-T8-4.

## 0. Inputs and references used

Immutable inputs (sha256 recomputed on this card, all equal the card pins):

| Input | sha256 |
|---|---|
| `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | f97be2ce…e68a |
| `handoffs/gap-eval-20261023/DECISIONS.md` | 3f643c1b…7da |
| `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | 833f1f25…cc8d |
| `continuity/roadmap-20261024.md` | 193452d5…238 |
| `reviews/m-a-plan.md` (format example) | 0a2c6135…ae3f |
| `pipelines/ma-run.yml` (format example) | 1153c040…c85c |

Scope sources (read in full):

- `handoffs/pilot-roadmap-20261024/pilot-roadmap-20261011.md` section 3 (sha256 2ffee5f3…319c): Ticket 8 must
  give "explicit choices for remote ZIP access/extraction, numeric download consent thresholds, disk/cache behavior,
  missing credentials/remotes, interrupted transfers and older archive schemas"; Ticket 9 implements it.
- Ticket 8: `/home/ubuntu/ScieFlow-claude-f2976f52/.scratch/scieflow-remaining-scope/issues/08-design-spec-archived-run-browsing.md`
  (sha256 c799e33d…dae9). Its six questions and five definition-of-done items are answered point by point in
  Part A section 5.1.
- Ticket 9 invariants (read for consistency, not built here):
  `/home/ubuntu/ScieFlow-claude-f2976f52/.scratch/scieflow-remaining-scope/issues/09-implement-archived-run-browsing.md`
  (sha256 82b87b62…44ac).
- Background specs on main: `docs/superpowers/specs/2026-10-03-b-run-explorer-design.md` (the local explorer, "B";
  B2 out of scope at `:268-271`), `docs/superpowers/specs/2026-10-03-manuscript-provenance-state-of-play.md`
  (decision 1 at `:282`, decision 4 at `:260-262`, the B2 note at `:317-322`),
  `docs/superpowers/specs/2026-09-14-dvc-workspace-archives-design.md`, `docs/DVC_STORAGE.md`.
- Queue `continuity/milestones.yml`: `t-8` is `after: [f-2]`, design-only; `t-9` is `after: [t-8, t-12]`.
- DECISIONS D7 (CLI parity: the web app is only an interface) and D9-D11/D23 (data protection) apply. No other
  D-number bears on archives; D17's "no-export invariant for archives" (PDFs cached under `scratch/`, which archives
  skip) is unchanged by this design.

Prerequisite plans read (approved, not merged yet):

- `reviews/f-2-plan.md` (sha256 6f32e994…b55b, approved `approvals/f-2-plan-approved-human-20261102.md`). F-2's
  complete file list (its section 3) touches none of the archive code (`scripts/dvc_sync.py`,
  `scripts/sflib/archive.py`, `core/workspace.py`, `core/jobs.py`) and none of the dashboard; it shifts line numbers
  in `core/service.py`, `web/pages.py` and `web/api.py`. Nothing in this design depends on F-2's behaviour; F-2 is
  a queue prerequisite only.
- `reviews/t-12-plan.md` (sha256 5910047d…64f8, approved `approvals/t-12-plan-approved-human-20261103.md`, a
  parallel lane, **not** a prerequisite of T-8 but one of T-9). T-12 adds `core/control_read.py`
  (descriptor-anchored bounded reads), `workspace.describe_from(...)` extracted from `describe` (T-12 plan 5.7), the
  read-only `/workspace` index and run-health pages, and the human UI rule (no browser dialogs; new controls match
  their nearest sibling). This design names those seams for Ticket 9 and adopts the UI rule.

Code read read-only from git objects (`git archive <sha> | tar -x` into the profile's cache scratch, and
`git show <sha>:<path>`); no checkout was touched.

- `M` = remote `main` = `97d4bd07f9edf1a1996fe5f72d69653ec4105317` (`git ls-remote` on this card; objects present
  locally). It contains M-G2 (PR #28). M-H, M-I and F-2 are **not** merged yet (no `RUN/scratch/{m-h,m-i,f-2}/publish.json`).
- Every `path:line` below is on `M`. t0 re-verifies every cite on the real base after F-2 merges.
- `dvc` is **not installed** on this host (`which dvc` and the project venv: none). Statements about DVC's own
  behaviour below (cache layout, default `cache.type`, error wording) come from DVC's documentation and the existing
  code that drives it; they are marked *(DVC behaviour, not exercised here)*. Ticket 9 tests use a fake `dvc`
  executable, as `tests/test_dvc_sync.py` already fakes `run_cmd`.

## 1. For the human: what Ticket 8 delivers, in plain words

Ticket 8 is a **design document only**. It decides how ScieFlow will let you see and open runs that exist only as a
zip on your DVC remote (S3), so that Ticket 9 can build it without guessing. This run commits the design to the
repository, adds a test that keeps the design document complete, and adds a few tests that pin the facts about
today's code the design relies on. It changes no ScieFlow behaviour.

What the design says (Part A):

1. **Archived runs are listed without any network call.** The list comes from the small pointer files that git
   already carries (`workspace/_archives/<slug>.zip.dvc`, with the archive's size and checksum). The dashboard gets a
   section *Archived runs* below your local runs. Opening a page never contacts S3 and never runs `dvc`.
2. **Download first, then read in place; never stream from S3, never unpack by surprise.** You press *Download*;
   ScieFlow fetches the one zip (as `dvc_sync.py pull` does today, but without unpacking it), checks its size and
   checksum against the pointer, and then lets you browse its folders and open its files straight out of the zip.
   Unpacking into `workspace/<slug>` is a separate, explicit *Restore* button.
3. **A number decides when you are asked twice: 1 GiB.** Below 1 GiB (1,073,741,824 bytes) the *Download* button
   (which shows the size) is the consent. At 1 GiB or more, the page first shows a confirmation panel with the exact
   size, free disk and where it goes, and only its *Confirm* button starts the download. The same rule applies to
   *Restore* (bytes unpacked). From the terminal, 1 GiB or more needs `--yes`. An archive whose pointer has no size
   can only be downloaded from the terminal with `--yes`.
4. **Not enough disk is a refusal, not a question.** Each filesystem that receives bytes must keep 1 GiB free after
   the transfer (with the same 5% headroom `push` uses). Because DVC may keep a copy in its cache and another in
   `workspace/_archives/`, a download on one filesystem counts the archive twice.
5. **Failures are named in plain words**: dvc not installed, no remote configured, no or refused credentials,
   remote unreachable, archive missing on the remote, disk full, interrupted, wrong size or checksum, another
   operation running. Raw dvc output never appears on the page; the page names the log file on this machine.
6. **Older archives degrade field by field.** Nothing inside an archive is required. A missing or unreadable
   `status.yml`, `config.yml`, `budget.yml` or `data.yml` shows as "not in this archive" or "unreadable", never as a
   default that looks real. Old per-file DVC pointers (`workspace/<slug>.dvc`, like the two on main) are listed but
   pulled from the terminal only.
7. **Manuscript history** is shown as one sentence, never as an empty history: "Manuscript history is not kept in
   archives. It is rebuilt from the run's drafts after you restore the run: open its drafts page, or complete a merge
   round."
8. **Browsing is read-only; downloading and restoring change local state**, so they are POST forms with the
   session and CSRF checks, listed in the app's mutating-route inventory, and refused inside an agent dispatch.
   Everything has a terminal command too (`scieflow workspace archives`, `scieflow workspace archive …`).
9. **Nothing gets stuck and nothing waits.** If the web server restarts or a download is killed, the page shows
   "interrupted" (worked out by looking, without writing anything) and the next Download, Restore or Cancel tidies
   the old job record and carries on. A second click while an operation runs is answered at once with "another
   archive operation on this run is in progress", never queued.

**Decisions taken under Rule D that you may veto at the approval gate** (the full list is Part A 5.2):

- **T8-D1 Download-then-read-in-place** (not streaming entries from S3, not pull-and-extract). Alternative:
  streaming with S3 range reads; rejected for v1 (section 5.3).
- **T8-D5 The web app may start a DVC download** using your own DVC configuration and credentials, as a tracked,
  cancellable job, from a session-authenticated, CSRF-protected POST. This is a new capability of the loopback web
  app (today DVC is CLI-only, `docs/web.md:538-540` covers uploads). Alternative: the browser only shows the terminal
  command; say so and Ticket 9 builds the read-only half plus the CLI.
- **T8-D3 The numbers**: 1 GiB consent threshold, 1 GiB disk reserve, 5% headroom; the other bounds in 5.13.
- **T8-D17 This run adds tests but no production code**: one test that pins the design document's completeness and
  its constants table, and premise tests for facts the design relies on that no existing test pins yet.

**Items for you (not in this run):** H-T8-1 a remote-listing view (what is on S3 but has no pointer in this
checkout) is out of scope; H-T8-2 deleting a downloaded copy from the browser is out of scope (terminal: delete the
zip, then `dvc gc`); H-T8-3 Ticket 9 will propose one protected `AGENTS.md` rule-15 sentence for downloads (5.11);
it is listed in Ticket 9's plan, not applied here; H-T8-4 the manuscript sentence (5.7.4) says history is "rebuilt
from the run's drafts after you restore the run", where Ticket 9 and the pilot roadmap say "regenerates after pull
and sync"; same fact, but it is an exact, tested sentence, so if you want the word "regenerates" say so at the gate
and it is changed in Part A before t1.

**Your approval of this plan is the approval of the design** that Ticket 8's definition of done asks for ("Spec
written and explicitly approved by the user before any plan or code"). Ticket 9 plans against the committed copy.

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head `dev/t-8` → base
`main`, one PR, merge with a merge commit. Title: "Ticket 8: archived-run browsing design (B2), its docs test and
premise tests". The body (written by the branch step) is plain English: what the design decides, the decisions
T8-D1..D18, absolute test counts, the residuals of section 8.

## 2. Base, dependencies and worktree

T-8 starts only after F-2 is merged (queue `after: [f-2]`; F-2 itself waits for M-H, M-I and M-D3). t0 checks:

1. `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`; object-only fetch
   `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags --no-write-fetch-head git@github.com:jedimik/ScieFlow.git refs/heads/main`.
2. `git merge-base --is-ancestor 97d4bd07f9edf1a1996fe5f72d69653ec4105317 <base>` (`M`).
3. `RUN/scratch/f-2/publish.json` exists with `"merged": true` and its `pushed_head` is an ancestor of `base`.
4. Otherwise `BASE_RED` with the reason (the supervisor escalates; no polling).

Worktree: `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/t-8 RUN/scratch/t-8-wt <base>` (on a
retry reuse it at `<base>` or `<base>` plus t0's docs commit; never reset). RUN =
`/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in `RUN/scratch/t-8/`
(junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs under `~/.cache/scieflow-t-8-<step>/`. No step
touches `/home/ubuntu/Github/ScieFlow` or the checked-out tree of `/home/ubuntu/ScieFlow-claude-f2976f52`. Commits
are local on `dev/t-8`; review fixes are new commits.

Parallel lane: T-12 may run or merge at the same time. T-8 writes two documents and two test files that T-12 does
not touch; the installer's refresh step handles main moving.

## 3. In-flight, never-edited, protected and changed files

**INFLIGHT** (the same 9 paths as every milestone since M-A; the installer's standing rule guards them). T-8 edits
none of them:

```
config/agents.yml
docs/runs.md
schemas/status.yml
src/scieflow/core/run/actions.py
src/scieflow/core/run/checkpoint.py
src/scieflow/core/run/cli.py
src/scieflow/core/run/init.py
src/scieflow/core/run/status.py
src/scieflow/core/run/validate.py
```

Guard (must exit 0): `git -C WT diff --exit-code <base> -- <INFLIGHT>` and, on a dirty tree,
`git -C WT diff --exit-code -- <INFLIGHT>`.

**Never edited** (T-8 changes no production file at all): everything under `src/`, `scripts/`, `config/`,
`schemas/`, `skills/`, `setup/`, `.github/`, `.claude/`, every `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/` outside
`docs/superpowers/`, `mkdocs.yml`, `pyproject.toml`, `uv.lock`, the DD1-sealed files
(`tests/probes/k_task8_binding.py`, `tests/probes/test_k_task8_binding.py`, `tests/fixtures/k_task8_stream_fixture.py`)
and every existing test file.

**Protected edits:** none. No `t-8-protected-edits.json`.

**Scope guard** (must exit 0, run by t1, t1r, branch and final):
`test -z "$(git -C WT diff --name-only <base>..HEAD | grep -v -x -F -f RUN/scratch/t-8/allowed-paths.txt)"`, where
t0 writes `RUN/scratch/t-8/allowed-paths.txt` with exactly the four paths of the file list below.

**Complete file list** (nothing else changes):

| File | Change | Step |
|---|---|---|
| `docs/superpowers/plans/2026-11-03-t-8-archived-run-browsing.md` | byte copy of this plan | t0 |
| `docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md` | header line + Part A of this plan + the cite appendix | t1 |
| `tests/test_docs_b2_spec.py` | new: the design document is complete and its constants table is exact | t1 |
| `tests/test_b2_premises.py` | new: premise tests for the facts of section 4.9 not pinned by an existing test | t1 |

## 4. Current code, verified (on M = 97d4bd0)

### 4.1 Archive files and pointers

- `scripts/sflib/archive.py:17` `ARCHIVE_DIR = "_archives"`; `:43-44` `archive_path` = `workspace/_archives/<slug>.zip`;
  `:47-49` `pointer_path` = `<slug>.zip.dvc`; `:52-57` `archived_slugs` = names of `*.zip.dvc` in `_archives/`.
- `:121-152` `build_zip`: `ZIP_STORED` by default (`:124`), Zip64, written to `<zip>.partial` (`:135`) and
  `replace`d into place (`:149`); on an exception the partial is removed (`:150-152`). A process killed hard (SIGKILL,
  power loss) can leave `<slug>.zip.partial`; no code removes or reads it.
- `:26-30` `_SKIP_DIRS` includes `scratch` and `provenance.git` (`:29`, added by commit 11025f3, 2026-10-03, ticket
  01). Archives built before that commit may contain `provenance.git/`.
- `:155-165` `verify_zip` = `testzip()` over every member (a full read). `:172-182` `ensure_space`: needs
  `workspace_size × 1.05` free (`headroom` `:173`).
- `:185-190` `_check_members` refuses absolute, drive-letter and escaping names; `:213-241` `extract_zip` refuses a
  non-empty destination without `force` (`:216-217`), extracts into a staging dir `.<name>.extract-*` beside the
  destination (`:219`), renames it into place; with `force` the old directory is moved to `.<name>.replaced-<pid>`
  first (`:232`).
- `:244-268` `link_to_cache` replaces the checked-out zip by a hard link to the DVC 3 cache object
  `.dvc/cache/files/md5/<2>/<30>` (`:256`); it returns `False` (copy kept) for a missing md5, a `.dir` hash, a missing
  object, a size mismatch or a cross-device link.
- `.gitignore:10-12`: `workspace/_archives/*.dvc` is tracked by git, everything else in `_archives/` (zips,
  partials) is ignored; `.gitignore:24` `.dvc/config` is ignored (machine-local remote; `docs/DVC_STORAGE.md:101-103`);
  `.gitignore:40` `.scieflow/` is ignored.
- Main carries **no** `_archives/*.zip.dvc` pointer today. It carries two legacy per-file directory pointers:
  `workspace/2026-07-segsnake-thalamus-mrtrix-validation.dvc` (`md5 … .dir`, `size: 211669085563`, `nfiles: 22059`)
  and `workspace/2026-09-segsnake-paper1-technical-reproducibility.dvc` (`size: 295318`, `nfiles: 21`), plus a chat
  bundle pointer under `workspace/chats/`. Ticket 9 therefore works on synthetic fixtures.

### 4.2 `scripts/dvc_sync.py`

- `:35-48` `load_env_into_environ` loads `.env` into the process environment (called from `main`, `:386-388`).
- `:51-62` `find_workspaces` skips `.`/`_` names and symlinks; `:70-87` `resolve_slugs` accepts pointer-only slugs.
- `:114-137` `use_archive`: for `pull`, the archive pointer alone decides (`:128-129`).
- `:194-219` `pull_archive`: `dvc pull <pointer>` (`:204`), `verify_zip` + `extract_zip` (`:208-209`),
  `link_to_cache` (`:214`); the zip is kept (`DVC_STORAGE.md:155-158`).
- `:329-343` `workspace_summary` already distinguishes an archive pointer with zip "present" / "not downloaded"
  (`:335-337`).
- `:393-395` push/pull take named runs only (no `--all`).
- `run_cmd` (`:90-93`) inherits stdout/stderr; nothing classifies a dvc failure today.
- `:31` imports `scieflow.core.config` (so the script can use `scieflow.core.project`/`store`); `:346-378`
  `build_parser`: `push` (`:357-367`) and `pull` (`:370-374`) have no state-directory option today; `cmd_push`
  (`:247`) and `cmd_pull` (`:300`) call `push_archive` (`:273`) / `pull_archive` (`:312`) once per slug and use its
  return code.
- `:144-191` `push_archive` builds `<slug>.zip` (and its `.partial`) in `_archives/` — the same file a fetch writes,
  hence the shared per-slug lock of T8-D14.

### 4.3 `core/workspace.py`

- `:134-154` `list_runs` skips `.`/`_` names, `news`, `chats` (`:143`): a pointer-only slug is not a run today, and
  `_archives` is never listed. `:108-131` `describe(path)` derives kind, state, phase, iteration, stopped reason and
  lineage from `status.yml` and `config.yml`; T-12 extracts `describe_from(slug, status, config, updated,
  updated_at)` (T-12 plan 5.7), which Ticket 9 reuses for archive members.
- `:173-179` `_pointer_for` knows both pointer kinds; `:182-226` `sync_status`; `:299` `doctor` reports
  `per_file_dvc_pointer`.
- `:320-322` the `workspace` click group (`scieflow workspace list|index|sync-status|doctor`).

### 4.4 Service, jobs, project

- `core/project.py:19` `SLUG_RE = ^[A-Za-z0-9][A-Za-z0-9._ -]*$`, `:26` `clean_slug`; `:68-71` `state_dir` =
  `.scieflow/` or `$SCIEFLOW_STATE_DIR` ("jobs outside any run"); `tests/conftest.py` isolates it per test.
- `core/jobs.py:1-7` every long-running thing is a tracked, cancellable job; `:277-278` a job with `run_dir=None`
  lives under `state_dir/jobs`; its record is `<id>.json` beside `<id>.log` (`:281-282`, `:370`); `:289-290`
  `load(path)` reads one record; `:300-318` `peek_state(log)` reads one record's state in O(1); `:321-333`
  `list_jobs(project)` with no run dir loads every record of `state_dir/jobs` **and** every `workspace/*/jobs`, and
  `:336-337` `find` is a scan over it; `:346-353` `_alive(pid)` is `os.kill(pid, 0)` (pid only, no start-time
  check); `:356-408` `start` launches `list(argv)` directly when not sandboxed (`:384`) in its own session
  (`start_new_session=True`, `:388`), so the child is the recorded pid and survives the process that started it;
  `:435-445` `wait` writes the final state; `:463-474` `cancel` marks `cancelled` and kills the recorded pid's group;
  `:518-527` `reconcile` marks a running job whose pid is gone `lost` (a write); `STATES`/`FINAL` `:27-28`.
- `core/service.py` calls `jobs.reconcile` only with a run directory (`:1787`, `:1919`); nothing reconciles
  `state_dir/jobs` today. A web-started job is waited on by a daemon thread of the web process (`:258`, `:279`), so
  after a server restart nobody writes its final state.
- `core/store.py:22` `LOCK_TIMEOUT = 30`; `:39-44` `locked(path)` takes `FileLock(<path>.lock, timeout=30)`, i.e.
  it **waits** up to 30 s, then raises; `:60-63` `write_text` = `locked` + atomic replace. There is no
  non-blocking variant today.
- `core/service.py:80-87` `_ws` requires the run directory; `:104-126` `list_runs_with_budget` (the dashboard's whole
  read cost, pinned by `tests/web/test_dashboard_reads.py`); `:284-291` `cancel_job(project, job_id)` works for any
  job; `:1036-1073` `manuscript_history` calls `provenance.sync` (`:1064`), and `merge_round` syncs (`:1022`), so a
  run without `provenance.git` gets its history back on the drafts page or after a merge round; `run_overview`
  (`:2404-2425`) never syncs. `core/provenance.py:44` `REPO_DIR = "provenance.git"`, `:320` `ensure_repo`, `:547`
  `sync`, `:788` `DIFF_LIMIT = 200_000`.
- `core/data/levels.py:19` `DATA_FILE = "data.yml"`, `:138-...` `read` (invalid → `Invalid`), `:167-175`
  `level_info` (invalid → `L2`, fail-closed, the two lines `:170-171`); `service._data_panel` `:2366-2397`.
- `integrations/scite/mcp.py:160-166` `in_dispatch` (env `SCIEFLOW_DISPATCH=1` or the sandbox cache-dir heuristic).

### 4.5 Web

- `web/pages.py:33` router requires the session; `:35` `MUTATE = [Depends(auth.csrf_protect)]`; `:187-200`
  `dashboard` renders `list_runs_with_budget` and open gates; `:844-858` `files_page`, `:861-893` `file_view`.
- `web/files.py:14` `MAX_INLINE = 200_000`; `:15-18` `TEXT_SUFFIXES`; `:23-25` `INLINE_SAFE_TYPES` (png, jpeg, gif,
  webp, pdf; not SVG); `:81-94` `download_type` (everything else downloads as `application/octet-stream`); `:117`
  `.lock` names are hidden from listings.
- `web/artifact_response.py:32` sends `X-Content-Type-Options: nosniff`.
- `core/turn_output.py:475` `display_chunks(chunks)` — the public bounded sanitizer adapter for raw chunks (K-6),
  `:15` `CHUNK_BYTES = 65536`.
- `tests/web/mutating_paths.py:18` `MUTATING_PATHS`, `:52` `SAMPLES` (asserted equal by `test_mutations.py`);
  `tests/web/test_async_routes.py` enforces `def` handlers.
- `docs/web.md:530-549` "What stays CLI-only" lists **DVC uploads** (`:538-540`); downloads are not mentioned.

### 4.6 Rules

- `AGENTS.md:150-160` rule 15 (uploads: ask at a file ≥ 1 GB, > ~500 new files, > ~5 GB total, or never synced) —
  it binds uploads, not downloads; `skills/workspace-sync/SKILL.md:48-53` the same table.
- Rule 14 gates are run-scoped (`scieflow gate open <slug>`); an archived-only run has no local run directory, so a
  download confirmation cannot be a gate (5.8).
- `docs/DVC_STORAGE.md:101-103` `.dvc/config` is machine-local; `:114-118` per-file tracking turns a run into tens
  of thousands of objects (why T8-D9 keeps legacy pulls in the terminal); `:155-158` pull keeps the hard-linked zip.
- `docs/data-protection.md:216` DVC push is "not refused by M-B (M-B-D13)" (T8-D18: downloads are not refused by
  level either).

Every `path:line` cite made in Part A also appears in this section 4 (F3); t0 nevertheless re-verifies the union of
the cites of section 4 and Part A (section 6, t0 step 3).

### 4.7 The Ticket 8 premise list (what the design relies on)

| # | Premise | Source | Pinned today by |
|---|---|---|---|
| P1 | pointer path `_archives/<slug>.zip.dvc`; `archived_slugs` lists pointers only | archive.py:43-57 | `tests/test_archive.py::test_archive_and_pointer_paths`, `::test_archived_slugs_lists_pointers_only` |
| P2 | workspace archives are `ZIP_STORED` | archive.py:124 | `tests/test_archive.py::test_build_zip_members_are_stored_uncompressed` |
| P3 | `provenance.git` and `scratch/` are never packed | archive.py:26-30 | `tests/test_archive.py::test_rebuildable_dirs_are_skipped` |
| P4 | a failed build leaves no zip and no `.partial` | archive.py:135-152 | `tests/test_archive.py::test_build_zip_failure_leaves_no_zip_and_no_partial` |
| P5 | `extract_zip` refuses a non-empty destination without force | archive.py:216-217 | `tests/test_archive.py::test_extract_zip_refuses_non_empty_dest_without_force` |
| P6 | `extract_zip` stages in `.<name>.extract-*` beside the destination and leaves nothing there on failure | archive.py:219-229 | unpinned (t0 confirms) |
| P7 | reading a stored member whose bytes were altered raises `zipfile.BadZipFile` (CRC) at the end of the read | stdlib `zipfile` | unpinned |
| P8 | a pointer-only slug is not in `workspace.list_runs` nor `service.list_runs_with_budget` | workspace.py:143 | unpinned (t0 confirms) |
| P9 | a job with `run_dir=None` goes to `state_dir/jobs`; a dead running job becomes `lost` | jobs.py:277-278, 518-527 | `tests/core/test_jobs.py::test_jobs_outside_a_run_go_to_the_state_dir`, `::test_reconcile_marks_dead_running_jobs_lost` |
| P10 | `pull_archive` keeps the zip; `link_to_cache` hard-links it to the DVC 3 object | dvc_sync.py:208-218, archive.py:244-268 | `tests/test_dvc_sync.py::test_pull_archive_extracts_and_keeps_zip`, `tests/test_archive.py::test_link_to_cache_hardlinks_zip_to_cache_object` |
| P11 | a run rebuilt from an archive (drafts present, no `provenance.git`) gets manuscript history from `service.manuscript_history` | service.py:1036-1073, provenance.py:320,547 | unpinned as a round trip (t0 confirms) |
| P12 | `.gitignore` tracks `workspace/_archives/*.dvc` and ignores `.dvc/config` and `.scieflow/` | .gitignore:10-12,24,40 | unpinned |
| P13 | push and pull require explicit slugs | dvc_sync.py:393-395 | `tests/test_dvc_sync.py::test_push_and_pull_require_explicit_slugs` |

t1 adds one test to `tests/test_b2_premises.py` for each premise t0 marks unpinned (P6, P7, P8, P11, P12 on `M`).

## Part A — Spec

### 5.1 The Ticket 8 questions, answered

| Ticket 8 question | Answer | Where |
|---|---|---|
| Pull-and-extract versus streaming entries out of the zip | Neither: **download the one zip, verify it, read entries in place** from the local `ZIP_STORED` file; extraction is a separate *Restore*. Streaming from S3 is out of scope for v1. | 5.3, 5.6, 5.7 |
| Consent: threshold and who is asked | **1 GiB = 1,073,741,824 bytes** of download (pointer size) or of unpacked bytes. Below: one click on a button showing the size. At or above: a two-step in-page confirmation bound to the exact byte count. Unknown size: terminal only, `--yes`. Asked: the human in the browser session or at the terminal; an agent never answers. | 5.4, 5.8 |
| Missing remote, remote needing credentials, half-pulled archive, `.partial` from an interrupted build | Each has its own state and fixed sentence; nothing is retried or deleted silently. | 5.9, 5.10 |
| Schema drift: required fields, how an old archive degrades | **No field is required.** Each derived field degrades on its own ("not in this archive", "unreadable", "too large to read here"). Old per-file pointers are listed but terminal-only. DVC 2 pointers are read. | 5.5, 5.10 |
| Manuscript history absent from archives | The archive page shows exactly the sentence of 5.7.4, never an empty history panel. | 5.7.4 |
| Read-only, or is pulling a mutation needing POST and CSRF | Browsing is read-only GET (nothing written, not even a cache file). Download, restore and cancel are mutations: POST, session, CSRF, in `MUTATING_PATHS` and `SAMPLES`. | 5.6, 5.8, 5.12 |
| Definition of done: a number, not a sentiment | The constants table 5.13 (pinned by `tests/test_docs_b2_spec.py`). | 5.13 |
| Definition of done: failure and degradation section | 5.10. | 5.10 |
| Definition of done: `mkdocs build --strict` clean | `docs/superpowers/` is excluded from the site (`mkdocs.yml` `exclude_docs`); the build stays clean (branch step). | 6 |

### 5.2 Decisions

- **T8-D1 Download-then-read-in-place.** A download writes the zip to `workspace/_archives/<slug>.zip` exactly where
  `dvc_sync.py pull` puts it today, verified, hard-linked to the DVC cache when possible, and never unpacked by the
  download. Browsing opens that local file read-only with `zipfile`; a stored member is a seek plus one bounded read.
- **T8-D2 Offline discovery.** Archived runs are found from git-tracked pointers only: `_archives/*.zip.dvc` (zip
  archives) and legacy `workspace/<slug>.dvc` whose `workspace/<slug>` does not exist. No GET in the app and no list
  command contacts the remote or starts `dvc`. Whether the remote really holds the object is learned by downloading.
- **T8-D3 Numbers.** Consent threshold 1 GiB; disk headroom 1.05 (as `ensure_space`); disk reserve 1 GiB; every other
  bound in 5.13.
- **T8-D4 Disk is a refusal.** For each distinct filesystem that receives bytes (the DVC cache and
  `workspace/_archives/` for a download; `workspace/` for a restore), free space must be at least
  `received × 1.05 + 1 GiB`. A download whose cache and `_archives/` share a filesystem receives `2 × size`
  (cache object plus checkout copy before `link_to_cache`; DVC's default `cache.type` is `reflink,copy` *(DVC
  behaviour, not exercised here)*, and a copy is the conservative case). A restore receives the sum of the members'
  uncompressed sizes. Below the requirement the action is refused with both figures; there is no override in the
  browser, and the CLI has none either (free disk first).
- **T8-D5 Downloads may start from the browser.** A human in an authenticated session may start a download as a
  project-level job; the web process launches `scripts/dvc_sync.py fetch`, which uses the user's own DVC
  configuration and loads `.env` as `dvc_sync.py` does today. The web process itself never opens `.env`,
  `.dvc/config.local`, `~/.aws/*` or any credential file. This is a new capability of the loopback app and is called
  out for the human at the gate.
- **T8-D6 Integrity before browsing.** A downloaded zip is browsable only after its size equals the pointer's `size`
  and its MD5 equals the pointer's `md5`, recorded in a verification record keyed to the file's
  `(st_dev, st_ino, st_size, st_mtime_ns)`; any change of the file invalidates the record. Each member read also
  checks the member CRC (premise P7, plan 4.7). `verify_zip`'s full `testzip` pass is not repeated on download (the MD5 already proves
  the bytes are the pushed bytes); *Restore* keeps it, because `extract_zip` writes every member.
- **T8-D7 Restore is explicit and never replaces.** *Restore* unpacks a verified zip into `workspace/<slug>` only when
  that directory is absent or empty (no `force` from the browser or the new CLI command; `dvc_sync.py pull --force`
  stays the terminal way to replace a run). The zip stays (hard-linked).
- **T8-D8 Failures are classified, raw output is not shown.** The fetch job classifies dvc's exit and its last
  64 KiB of output into a closed set of reasons (5.9). The page and API show the reason, its fixed sentence, the
  exit code and the job log's path; never dvc's text.
- **T8-D9 Legacy per-file pointers are terminal-only.** A `workspace/<slug>.dvc` with a `.dir` hash is listed with
  its size and file count and the command `uv run scripts/dvc_sync.py pull <slug>`; no browser download, because a
  per-file pull can be tens of thousands of objects (`DVC_STORAGE.md:114-118`).
- **T8-D10 No required archive field.** 5.5 and 5.10.
- **T8-D11 The manuscript-history sentence** of 5.7.4, exact.
- **T8-D12 Bounds.** 5.13; every list is capped and says so when the cap is hit (the `DIFF_LIMIT` pattern).
- **T8-D13 Names fixed now** (CLI commands, service functions, routes, files, reason codes) so Ticket 9 does not
  choose them: 5.6, 5.8, 5.9, 5.11, 5.12.
- **T8-D14 One lock per archive, never waited on.** The operation lock is the file `state_dir/archives/<slug>.lock`,
  taken with a new non-blocking helper `store.try_locked(path)` (a context manager around
  `FileLock(str(path) + ".lock", timeout=0)` that raises the new `store.Busy` instead of waiting; the existing
  `store.locked`, `core/store.py:39-44`, waits up to `LOCK_TIMEOUT = 30` s, `:22`, and is not used for these locks).
  It is held by `dvc_sync.py push`/`pull` around each archive-mode slug's `push_archive`/`pull_archive` call, and by
  the new `fetch` and `restore` for their whole duration. A second operation on the same slug gets `store.Busy` at
  once and is refused (`locked`), never queued: the `dvc_sync.py` subcommand prints the `locked` sentence for that
  slug, writes nothing, and that slug's return code is `LOCKED_EXIT` (3) (for `push`/`pull` it then counts as a
  failed slug in the existing per-slug loop, `dvc_sync.py:273`, `:312`). Every one of the four subcommands finds the
  state directory the same way: `--state-dir DIR` when given (the service always passes it), otherwise
  `Project(<repo root>).state_dir` (`$SCIEFLOW_STATE_DIR` or `<root>/.scieflow`, `core/project.py:68-71`). A
  `.partial` with the lock free is therefore an interrupted build, not a running one.
- **T8-D15 Placement.** The list is a section *Archived runs* on the dashboard `/` (the explorer's list page, ticket
  08's "shares the explorer's page"); each archived run has its own page `/archives/<slug>`. T-12's `/workspace`
  index and health pages stay local-only (unchanged).
- **T8-D16 Agents never consent.** The CLI refuses `fetch`/`restore` inside a dispatch (`in_dispatch`) and, outside
  one, requires `--yes` at or above the threshold; the skill text (Ticket 9) tells a coordinator to ask the user
  before passing `--yes`. No gate is opened (no run directory exists to hold one).
- **T8-D17 This run adds tests, not code** (plan section 3).
- **T8-D18 Data protection.** Archive pages are read by the human only: nothing from an archive is sent to a model,
  a remote or an agent; no dispatch, conversation, scan or release is offered on an archive page. When a verified
  archive contains `data.yml`, its declared level is shown with the M-B wording (invalid → `L2`, fail-closed, as
  `level_info`); an `L2` archive shows the banner of 5.7.2. Downloading is not refused for any level (the remote is
  the user's own store, as for pushes, `docs/data-protection.md:216`).

### 5.3 Why not streaming, why not pull-and-extract

- *Streaming entries from S3* (HTTP range reads into the `ZIP_STORED` zip) would need the web process to hold S3
  credentials or a DVC filesystem object, would turn every page view into a network round trip with its own failure
  modes, and depends on DVC internals for random access. It is technically possible for stored members and stays a
  possible later optimisation; v1 does not do it.
- *Pull-and-extract* (today's `dvc_sync.py pull`) doubles the disk (zip plus tree) to read one file and writes a run
  directory the user did not ask for; it also makes the run look local to every other tool. It stays available as
  the explicit *Restore* and as the existing terminal command.

### 5.4 Consent and who is asked

- The **download size** is the pointer's `outs[0].size` (the zip's bytes). The **restore size** is the sum of
  `ZipInfo.file_size` over all members of the verified zip.
- Size `< CONSENT_BYTES`: the action button shows the size (`Download 312.4 MiB`) and pressing it is the consent.
- Size `≥ CONSENT_BYTES`: pressing the button re-renders the archive page with an in-page confirmation panel (no
  browser dialog, T-12 UI rule) stating the exact bytes, the human size, free disk per receiving filesystem, the
  destination path and that the download runs until done or cancelled. Its *Confirm* button POSTs the same action
  with `confirm_size=<exact bytes>`. The server refuses (`confirm-mismatch`, nothing started) when `confirm_size`
  is absent or differs from the size it computes at that moment (a changed pointer or a forged form).
- Unknown download size (pointer without `size`): the browser refuses (`size-unknown`) and shows
  `scieflow workspace archive fetch <slug> --yes`; the CLI requires `--yes`; the disk rule cannot be checked and the
  page says so.
- Replacing an existing local zip that is not the verified copy (`incomplete`, `different`, `outdated`, 5.5) always
  needs the confirmation panel (or `--replace --yes` on the CLI), whatever the size, and says the existing file will
  be overwritten.
- At the terminal, `--yes` is required at or above the threshold and for any replace; without it the command prints
  the size and the exact command with `--yes`, and exits 1 having started nothing.
- Who is asked: the human operating the browser session or the terminal. An agent never passes `--yes` on its own
  (T8-D16), and inside a dispatch the commands refuse.

### 5.5 Pointer and local-copy states

**Pointer kinds** (read bounded, `POINTER_MAX_BYTES`, `yaml.safe_load`, no symlink followed):

- `zip`: `_archives/<slug>.zip.dvc` with exactly one `outs` entry, `md5` = 32 lowercase hex characters,
  `path == "<slug>.zip"`, optional integer `size ≥ 0`, optional `hash: md5` (absent = a DVC 2 pointer; read the same
  way; its cache layout is not DVC 3's, so `link_to_cache` keeps a copy *(DVC behaviour, not exercised here)*).
- `directory`: legacy `workspace/<slug>.dvc` with `md5` ending `.dir` (T8-D9).
- `unreadable`: anything else (YAML error, not a mapping, no or several `outs`, bad md5, path mismatch, `.dir` hash in
  an `_archives` pointer, over the size bound, a symlink). Listed with its reason; no action offered.
- A pointer whose file name does not pass `project.clean_slug` is not listed; the list reports how many were
  skipped.

**Local states of a `zip` archive** (`state` field in page, API and CLI JSON):

| State | Condition | Actions |
|---|---|---|
| `not-downloaded` | no `<slug>.zip` | Download |
| `downloading` | the slug's fetch job is effectively running (5.6.1) | Cancel |
| `incomplete` | zip present, size ≠ pointer size | Download again (replace) |
| `unverified` | size equal, no verification record matching the current file stat | Verify (no network) |
| `verifying` | the slug's verify-only fetch job is effectively running (5.6.1) | Cancel |
| `different` | verified MD5 ≠ pointer MD5 | Download again (replace) |
| `outdated` | a verification record exists for an older pointer MD5 (git brought a new pointer) | Download again (replace) |
| `ready` | record matches file stat and pointer MD5 | Browse, Restore (if the run is not local) |
| `restoring` | the slug's restore job is effectively running (5.6.1) | Cancel |

The last finished job's outcome (`failed:<reason>`, 5.9) is shown beside the state; it never replaces the file state
(a failed re-download over a `ready` copy that `dvc` did not touch still shows `ready` if the stat still matches).

**Symlinked zip** (DVC `cache.type: symlink` *(DVC behaviour, not exercised here)*): when `lstat` shows
`<slug>.zip` is a symlink, it is resolved once (`os.path.realpath`) and accepted only if the target lies inside
`<root>/.dvc/cache/`; then the target's `stat` is the size and the record key, every read opens the target, and
`link_to_cache` is not attempted. A symlink pointing anywhere else is never opened: the state is `incomplete`
(replace needed) and the page says "the local zip is a link outside the DVC cache".

**Run relation:** `archived-only` (no `workspace/<slug>`), `also-local` (a run directory exists; the dashboard's
local row gets an *archived* badge linking to the archive page; Restore is not offered), `alias` (a symlink of that
name exists; the archive page says so and offers nothing but browsing).

### 5.6 What is read, and how (GET only, nothing written)

- **List** (`service.archived_runs(project)`): one listing of `workspace/_archives/`, one of `workspace/` (already
  done by `list_runs`), then per pointer: one bounded pointer read, one `lstat` of `<slug>.zip`, one bounded read of
  `state_dir/archives/<slug>.json` if present, one `lstat` of `workspace/<slug>`, and the effective job state of
  the slug's archive job (5.6.1: one bounded read of `<slug>.job` and one bounded read of the job record it names;
  never a scan of all jobs).
  No zip is opened, no subprocess, no socket. Sorted by slug; at most `LIST_CAP` rows, then "N more not shown (cap
  500)".
- **Archive page** (`service.archive_detail(project, slug)`): the list's per-pointer reads for one slug, the
  environment pre-check (5.9: `shutil.which("dvc")`; existence only of `.dvc/`, `.dvc/config`, `.dvc/config.local`,
  never their content), the free space per receiving filesystem (`shutil.disk_usage`), and, only in state `ready`:
  - the end-of-central-directory pre-check: read at most the last 65,557 bytes (and the Zip64 locator/record when
    present) and refuse to parse (`too-many-entries`) when the archive claims more than `MAX_ARCHIVE_ENTRIES`
    entries or a central directory larger than `MAX_CENTRAL_DIRECTORY_BYTES`;
  - one `zipfile.ZipFile` open, reused through an in-process LRU of `INDEX_CACHE` parsed indexes keyed by
    `(st_dev, st_ino, st_size, st_mtime_ns)` (memory only; no cache file);
  - at most five control members, each only when its `file_size ≤ CONTROL_MAX_BYTES`: `status.yml`, `config.yml`,
    `budget.yml`, `data.yml`, and the date of `status.yml`/`log.md`/`notebook.md`/`events.jsonl` from their
    `ZipInfo.date_time` (no read). The run summary is `workspace.describe_from` (T-12) fed from those bytes, budget
    remaining from `budget.remaining_fraction`, the data level from the M-B parser applied to the member bytes;
  - totals: member count, uncompressed bytes, and counts of unsafe, encrypted, unsupported-compression and link
    members.
- **Entries** (`service.archive_entries(project, slug, path="", page=1)`): one directory level of the verified zip,
  derived from member names (explicit `dir/` entries and implied parents), directories first then files, each by
  name; names ending `.lock` hidden (as `web/files.py:117`); `PAGE_ENTRIES` per page with "entries 501-1000 of
  2,345"; each entry `{name, path, kind: dir|file|link|unsafe|encrypted|unsupported, size}`. A link member shows its
  target text and is never followed. Unsafe names (the `_check_members` rule) are counted, not listed.
- **Member** (`service.archive_member(project, slug, path)`): `path` must equal a member name of the central
  directory exactly (no filesystem resolution, so no traversal is possible). Allowed only for `file` entries.
  Text (suffix in `files.TEXT_SUFFIXES`, `file_size ≤ INLINE_MAX_BYTES`): read in `CHUNK_BYTES` chunks through
  `turn_output.display_chunks` and rendered autoescaped, like `file_view`. Everything else, and any `download=1`:
  streamed in `CHUNK_BYTES` chunks with `files.download_type` (inline only for `INLINE_SAFE_TYPES`, otherwise
  `application/octet-stream` and an attachment name) and `X-Content-Type-Options: nosniff`; no range support. A CRC
  failure mid-stream ends the response and is logged; an inline view shows `corrupt-member`.
- `ZipInfo.date_time` is the packing machine's local time without a zone; it is shown as a date only, labelled
  "packed (approximate, the packing machine's local date)".

#### 5.6.1 Job liveness (read-only on every GET; `lost` written only on POST/CLI)

The service function `archives.effective_job(project, slug)` (new, pure read) answers "is an archive job of this
slug running, and what was the last outcome", without writing anything:

1. Read `<state_dir>/archives/<slug>.job` (bounded by `JOB_RECORD_MAX_BYTES`). Its content must be one job id
   matching `^[0-9A-HJKMNP-TV-Z]{26}$` (a `store.new_id` ULID); anything else → "no job" with the reason
   `job-pointer-unreadable` shown on the page.
2. Read exactly `<state_dir>/jobs/<id>.json` (the record path `jobs.start` uses for `run_dir=None`,
   `core/jobs.py:277-282`, `:370`), bounded by `JOB_RECORD_MAX_BYTES`, parsed like `jobs.load`
   (`:289-290`). Never `jobs.find` or `jobs.list_jobs`: with no run directory they load every job record of the
   project and of every run (`:321-337`). A missing or unparsable record → "no job".
3. The record must have `kind` in {`archive-fetch`, `archive-restore`} and `label == slug`; otherwise "no job".
4. Effective state:
   - record state `running` and `jobs._alive(pid)` true (`:346-353`) → **running** (the slug is `downloading`,
     `verifying` or `restoring` by kind and argv);
   - record state `running` and the pid is gone (the server that waited on it restarted, or the process was
     killed) → **not running**; the last outcome is the fetch/restore subcommand's own record `<slug>.json` when
     its `pid` equals the job's `pid` and its `outcome` is set (the child survives its starter, `:388`, and may have
     finished normally), else `interrupted`;
   - any `FINAL` state (`:28`): `done` → the outcome in `<slug>.json` (pid-matched) or none; `cancelled`, `lost`,
     `timeout` → `interrupted`; `failed` → the reason in `<slug>.json` (pid-matched) or `dvc-failed`.
5. Nothing is written: the stale `running` record is left as it is. The display never depends on that write.

`archives.reconcile_archive_job(project, slug)` (new) is the only writer of `lost` for archive jobs: it does steps
1-3 and, when the record is `running` and the pid is gone, applies exactly `jobs.reconcile`'s transition to that
one record (`state = "lost"`, `finished = now`, `jobs.save`; no event, the job has no run directory;
`core/jobs.py:518-527`). It is called only under the start lock, at the beginning of `fetch_archive`,
`restore_archive` and `cancel_archive_job` (5.8), i.e. on POST routes and CLI commands. `jobs.reconcile(project)`
without a run directory is not called (it would load and possibly rewrite every run's jobs).

Consequence: a server restart or a killed job never leaves the slug stuck. The page shows the state from the files
plus `interrupted`, and the next Download/Restore/Cancel first reconciles the record and then proceeds.

### 5.7 The archive page (`GET /archives/{slug}`)

1. **Header:** slug, pointer kind, archive size (bytes and human), MD5 (first 12 characters), relation (5.5), local
   state, last job outcome.
2. **Data level** (state `ready` only): the declared level from the archive's `data.yml` ("no data.yml in this
   archive: undeclared" when absent); for `L2`: "This archive is declared L2 (patient-level data). It is shown to you
   only; ScieFlow sends nothing from an archive to a model or a remote."
3. **Run summary** (state `ready` only): kind, state, phase, iteration, stopped reason, lineage, budget remaining,
   packed date, member count and unpacked size; each missing source reads "not in this archive", each oversized
   one "too large to read here", each unparsable one "unreadable"; never a default value.
4. **Manuscript history:** always exactly this sentence (T8-D11), and no history list, diff picker or empty panel:
   "Manuscript history is not kept in archives. It is rebuilt from the run's drafts after you restore the run: open
   its drafts page, or complete a merge round."
5. **Actions** per state (5.5), as forms; the confirmation panel of 5.4 when needed; the environment pre-check lines
   ("dvc: found / not installed (docs/DVC_STORAGE.md §1)", "DVC remote configuration in this checkout: found / not
   found (.dvc/config is machine-local; docs/DVC_STORAGE.md §2)"). Download is disabled with the reason only when
   `dvc` is missing; the configuration line is advisory, because DVC can also take its remote from its global or
   system configuration, which ScieFlow does not inspect (credentials and remotes are learned only by trying).
6. **Leftovers** (stat only, never deleted): `<slug>.zip.partial` ("an interrupted archive build left this file; it
   is not an archive and nothing reads it; delete it when no push is running"), `<slug>.zip.link` (an interrupted
   cache link), `workspace/.<slug>.extract-*` and `workspace/.<slug>.replaced-*` (an interrupted restore or forced
   pull; "not the run; delete it").
7. **Browse** link to `/archives/{slug}/files` (state `ready`).

UI rule (T-12, binding for Ticket 9): no browser dialogs of any kind; every new control reuses the classes of its
nearest existing sibling (dashboard rows, the run page's action forms, the files page table).

### 5.8 Actions (mutations)

| Action | Service function | Page route | API route | CLI |
|---|---|---|---|---|
| download / verify | `fetch_archive(project, slug, *, confirm_size=None, replace=False)` | `POST /archives/{slug}/fetch` | `POST /api/v1/archives/{slug}/fetch` | `scieflow workspace archive fetch SLUG [--yes] [--replace]` |
| restore | `restore_archive(project, slug, *, confirm_size=None)` | `POST /archives/{slug}/restore` | `POST /api/v1/archives/{slug}/restore` | `scieflow workspace archive restore SLUG [--yes]` |
| cancel | `cancel_archive_job(project, slug)` | `POST /archives/{slug}/cancel` | `POST /api/v1/archives/{slug}/cancel` | `scieflow workspace archive cancel SLUG` |

- `fetch_archive`: refuses (with reason, nothing started) for a non-`zip` pointer, `locked`, `size-unknown` (browser),
  `confirm-required`/`confirm-mismatch`, `replace-required` (a local zip exists that is not `ready` and `replace` was
  not given), `no-space`, and, when a download is needed, `dvc-missing`; in state `ready` it is a no-op returning
  `ready`; in state `unverified` it starts a verify-only job (no network, no consent); otherwise it starts the job
  `jobs.start(project, [sys.executable, "-B", "<root>/scripts/dvc_sync.py", "fetch", slug, "--state-dir", <state_dir>
  (, "--replace")], kind="archive-fetch", cwd=<root>, run_dir=None, label=slug)`.
- **Starting a job** (fetch and restore alike): the service enters `store.try_locked(<state_dir>/archives/<slug>.start)`
  (lock file `<slug>.start.lock`; `store.Busy` → `locked` at once, so a second concurrent POST is answered
  immediately, never held for `LOCK_TIMEOUT`). Under it: `archives.reconcile_archive_job(project, slug)` (5.6.1),
  then, if the job named in `<slug>.job` is still effectively running, `locked`; otherwise the refusal checks, then
  `jobs.start(...)` and an atomic write of the new job id to `<state_dir>/archives/<slug>.job`; then the lock is
  released. The web process waits on the job in a daemon thread (as `service.py:258-279` does for agent jobs); the
  CLI waits in the foreground. Concurrent POSTs therefore start at most one job; the job itself then holds the
  operation lock of T8-D14.
- `dvc_sync.py fetch SLUG [--replace] [--remote NAME] [--state-dir DIR]` (new subcommand): enters
  `store.try_locked(<state_dir>/archives/<slug>)` (busy → prints the `locked` sentence, writes nothing, exits
  `LOCKED_EXIT`); `dvc pull [-r NAME] [--force when --replace] <pointer>`, skipped (verify only) when the zip's size
  already matches and `--replace` is not given; size check, streamed MD5 (`MD5_CHUNK_BYTES` reads) against the
  pointer; `link_to_cache`; writes the verification record atomically with its own `pid` (`os.getpid()`, equal to
  the job's recorded pid because `jobs.start` launches the argv directly, `core/jobs.py:384`); on any failure writes
  `{"outcome": "failed", "reason": <code>, "exit_code": n, "pid": …}`; never extracts, never deletes a zip.
- `restore_archive`: state must be `ready`, relation `archived-only`, `workspace/<slug>` absent or empty, disk rule
  and consent on the unpacked size; job `kind="archive-restore"` running `dvc_sync.py restore SLUG --state-dir DIR`
  (new subcommand: operation lock via `store.try_locked` as for `fetch`, `verify_zip`, `extract_zip` without force,
  outcome written to `<slug>.json` with its pid, release). After it the run is local; the
  dashboard shows it as a local run with the *archived* badge.
- `cancel_archive_job`: under the start lock (busy → `locked`), `archives.reconcile_archive_job` first; if the
  slug's job is then still running, `jobs.cancel` on the record read by path (5.6.1 step 2; not `jobs.find` and not
  `service.cancel_job`, which uses it, `service.py:284-291`) kills its process group; the outcome becomes
  `interrupted`. A job that was already dead is now `lost` and nothing is killed (so a dead pid is never signalled).
- Every POST: `def` handler, session, CSRF (`MUTATE`), listed in `MUTATING_PATHS` and `SAMPLES`; a refusal re-renders
  the archive page with the reason (page) or answers 409 with `{reason, message}` (API); the CLI exits 1 with the
  message. Inside a dispatch (`in_dispatch`) the CLI and service refuse `fetch`, `restore` and `cancel`
  (`in-dispatch`).
- The CLI's `fetch`/`restore` start the same job and wait for it (`jobs.wait`), printing the outcome sentence.

### 5.9 Failure reasons (closed set, first match wins, case-insensitive on the last `DVC_OUTPUT_TAIL` bytes of the job's stdout+stderr)

| Code | Sentence shown | Matched by |
|---|---|---|
| `dvc-missing` | "dvc is not installed on this machine (docs/DVC_STORAGE.md §1)." | launch `FileNotFoundError` / exit 127 / pre-check |
| `no-remote` | "DVC has no usable remote for this archive (see docs/DVC_STORAGE.md §2)." | `no remote specified`, `doesn't exist` (remote name), `config file error` |
| `no-credentials` | "The DVC remote needs credentials and none were found." | `unable to locate credentials`, `nocredentialserror`, `no credentials` |
| `credentials-refused` | "The DVC remote refused the credentials." | `invalidaccesskeyid`, `signaturedoesnotmatch`, `accessdenied`, `access denied`, `forbidden`, `403`, `expiredtoken` |
| `remote-unreachable` | "The DVC remote could not be reached." | `could not connect to the endpoint url`, `name or service not known`, `temporary failure in name resolution`, `connection refused`, `network is unreachable`, `timed out` |
| `missing-on-remote` | "The remote does not have this archive (pointer md5 <12 chars>)." | `missing cache files`, `do not exist neither locally nor on remote`, `not found on remote` |
| `disk-full` | "The disk filled up during the transfer." | `no space left on device` |
| `interrupted` | "The transfer was interrupted (cancelled or the process stopped)." | job `cancelled`, `lost`, `timeout`, or killed by a signal |
| `verify-size` | "The downloaded file has the wrong size." | after dvc exit 0 |
| `verify-md5` | "The downloaded file does not match the pointer's checksum." | after dvc exit 0 |
| `locked` | "Another archive operation on this run is in progress." | `store.Busy` on the start or operation lock; a `dvc_sync.py` exit of `LOCKED_EXIT` |
| `dvc-failed` | "dvc failed (exit <n>); its output is in <log path> on this machine." | anything else |

The match strings are *(DVC/botocore wording, not exercised here)*; Ticket 9 pins each row with a fake `dvc` that
prints a fixture line, and adds a row only with a fixture. A pre-check refusal starts no job. The job log stays in
`state_dir/jobs/<id>.log|.err` (local, gitignored); the page shows its project-relative path, not its content.

**Refusal and browse codes** (nothing is started or read; fixed names for Ticket 9): `confirm-required`,
`confirm-mismatch`, `size-unknown`, `replace-required`, `no-space`, `in-dispatch`, `not-zip-pointer` (directory or
unreadable pointer), `not-ready` (browse or restore before `ready`), `run-exists` (restore onto a non-empty
`workspace/<slug>` or an alias), `too-many-entries`, `unreadable-zip`, `corrupt-member`, `no-such-member`,
`job-pointer-unreadable` (5.6.1; shown, never blocking: the next start overwrites `<slug>.job`); plus
`locked` and `dvc-missing` from the table above.

### 5.10 Failure and degradation

- **Missing remote:** the advisory pre-check line says when this checkout has no `.dvc/config` or
  `.dvc/config.local`; a download then fails as `no-remote` (no remote anywhere DVC looks) or proceeds (a global
  remote). Nothing is retried.
- **Remote needs credentials:** ScieFlow never inspects credentials; the first attempt fails as `no-credentials` or
  `credentials-refused`, the page links `docs/DVC_STORAGE.md` §2. Browsing a `ready` copy still works offline.
- **Half-pulled archive:** a zip whose size differs from the pointer is `incomplete`; a full-size zip without a
  matching record is `unverified` (verify needs no network); a mismatching MD5 is `different`. None is browsable;
  replacing needs the confirmation (5.4). An interrupted job is `interrupted`. A job whose process vanished (server
  restart, kill) is shown as not running with outcome `interrupted` by the read-only rule of 5.6.1 on every GET,
  without a write; its record is turned into `lost` by `archives.reconcile_archive_job` on the next Download,
  Restore or Cancel (POST or CLI), never by a GET.
- **`.partial` from an interrupted build:** reported as a leftover (5.7.6), never read, deleted or uploaded; it does
  not block a download (dvc writes `<slug>.zip`, not the partial). With T8-D14 a `.partial` while the lock is held
  means a push is running, and fetch/restore refuse with `locked`.
- **Interrupted restore:** `extract_zip`'s staging directory makes the restore all-or-nothing (premise P6, plan
  4.7); a leftover
  staging directory is reported (5.7.6); `workspace/<slug>` stays absent, so Restore can be pressed again.
- **Schema drift (pointer):** DVC 2 pointers are read (5.5); unreadable pointers are listed with their reason.
- **Schema drift (archive content):** no member is required. A zip without `status.yml` describes as kind `none`
  ("no status.yml", as `workspace._state`), others as in 5.7.3. An old archive that contains `provenance.git/` lists
  it as ordinary entries; it is never opened as a repository, and the manuscript sentence still applies (after a
  restore the extracted repository is probed and rebuilt by `provenance.ensure_repo` if needed; premise P11, plan
  4.7). An invalid
  `data.yml` reads as `L2` with its reason (fail-closed, `levels.py:170-171`).
- **Hostile or odd zips:** unsafe member names are counted and never served (restore refuses the whole archive, as
  `extract_zip` does); encrypted members and compression other than stored/deflate are listed, not read; a deflated
  member is read only up to its declared size (zipfile enforces it) and its inline view only when the declared size
  is within `INLINE_MAX_BYTES`; more than `MAX_ARCHIVE_ENTRIES` entries or a central directory over
  `MAX_CENTRAL_DIRECTORY_BYTES` → `too-many-entries` ("restore it from the terminal to look inside"); a zip that
  `zipfile` cannot open → `unreadable-zip` (the file state stays as verified; it means the pushed archive itself is
  broken).
- **Every GET degrades, never raises,** for any state above: the service returns reasons, the template renders them
  (no `StrictUndefined` exists; tests assert rendered text, as the B spec requires).
- **Concurrent change:** a pointer or zip that changes between the check and the action is caught by the
  `confirm_size` binding and by the verification record's stat key; the job re-checks everything after taking the
  lock.

### 5.11 CLI parity (D7) and docs for Ticket 9

- New commands under `scieflow workspace`: `archives [--json]`; `archive show SLUG [--json]`;
  `archive ls SLUG [DIR] [--page N] [--json]`; `archive cat SLUG MEMBER [--out FILE]` (raw bytes; to stdout only when
  stdout is not a terminal or the member is text); `archive fetch SLUG [--yes] [--replace]`; `archive restore SLUG
  [--yes]`; `archive cancel SLUG`. Existing `workspace list` is unchanged (local runs only).
- New public service functions, each with a `cli:` entry in `tests/core/cli_parity.yml`: `archived_runs`,
  `archive_detail`, `archive_entries`, `archive_member`, `fetch_archive`, `restore_archive`, `cancel_archive_job`.
- New `dvc_sync.py` subcommands `fetch` and `restore`; `push`/`pull` take the archive lock (T8-D14); all four accept
  `--state-dir DIR` (default `Project(<repo root>).state_dir`) and exit `LOCKED_EXIT` for a busy slug.
- New in `core/store.py`: `try_locked(path)` (non-blocking `FileLock`, `timeout=0`) and the exception `Busy`; the
  existing `locked` is unchanged.
- New module `src/scieflow/core/archives.py` holds pointer parsing, states, the classifier, bounds, the zip
  index, `effective_job` and `reconcile_archive_job` (5.6.1); `scripts/sflib/archive.py` gains only what
  `dvc_sync.py` needs (lock path, MD5 streaming, record writer) —
  "prefer extending the existing helpers over a parallel reader" (Ticket 9).
- Docs Ticket 9 writes: `docs/web.md` (Archived runs section, page and API tables; "What stays CLI-only" keeps DVC
  **uploads** and adds legacy per-file pulls), `docs/DVC_STORAGE.md` (a §3.6 "Browse and download from the web app"
  with the 1 GiB rule), `docs/cli.md`, `skills/workspace-sync/SKILL.md` (downloads: ask before `--yes`).
- **Proposed protected edit for Ticket 9 (H-T8-3, not applied here):** one sentence appended to `AGENTS.md` rule 15:
  "A download of 1 GiB or more (`scieflow workspace archive fetch|restore … --yes`) is the user's decision too: ask
  before passing `--yes`." Ticket 9's plan lists it exactly for the human.

### 5.12 Routes (all handlers `def`)

| Method | Path | Notes |
|---|---|---|
| GET | `/` | dashboard gains the *Archived runs* section (5.6 list) and the *archived* badge on local rows |
| GET | `/archives/{slug}` | 5.7 |
| GET | `/archives/{slug}/files?path=&page=` | 5.6 entries |
| GET | `/archives/{slug}/file?path=&download=` | 5.6 member |
| POST | `/archives/{slug}/fetch`, `/archives/{slug}/restore`, `/archives/{slug}/cancel` | 5.8; CSRF; `MUTATING_PATHS` + `SAMPLES` |
| GET | `/api/v1/archives`, `/api/v1/archives/{slug}`, `/api/v1/archives/{slug}/entries?path=&page=` | JSON equal to the service results |
| POST | `/api/v1/archives/{slug}/fetch`, `/restore`, `/cancel` | as the page routes; 409 `{reason, message}` on refusal |

`{slug}` passes `clean_slug` and must have a pointer (else 404). The CSRF token never appears in a query string.

### 5.13 Constants (exact; `tests/test_docs_b2_spec.py` parses this table, Ticket 9's code must equal it)

| Name | Value | Meaning |
|---|---|---|
| `CONSENT_BYTES` | 1073741824 | at or above: two-step confirmation (browser) / `--yes` (CLI) |
| `DISK_HEADROOM` | 1.05 | multiplier on received bytes, as `ensure_space` |
| `DISK_RESERVE_BYTES` | 1073741824 | left free on each receiving filesystem |
| `LIST_CAP` | 500 | archived rows listed before "N more not shown" |
| `PAGE_ENTRIES` | 500 | entries per page in the archive file browser |
| `MAX_ARCHIVE_ENTRIES` | 100000 | above: not browsed (`too-many-entries`) |
| `MAX_CENTRAL_DIRECTORY_BYTES` | 67108864 | above: not browsed (`too-many-entries`) |
| `POINTER_MAX_BYTES` | 65536 | bound on a pointer file read |
| `RECORD_MAX_BYTES` | 4096 | bound on a verification record read |
| `CONTROL_MAX_BYTES` | 262144 | bound on a control member read (`status.yml`, …) |
| `INLINE_MAX_BYTES` | 200000 | equals `web/files.py` `MAX_INLINE` |
| `INDEX_CACHE` | 4 | parsed central directories kept in memory per process |
| `DVC_OUTPUT_TAIL` | 65536 | bytes of dvc output the classifier reads |
| `MD5_CHUNK_BYTES` | 1048576 | read size of the streamed MD5 check |
| `JOB_RECORD_MAX_BYTES` | 65536 | bound on the `<slug>.job` read and the one job record read (5.6.1) |
| `LOCKED_EXIT` | 3 | `dvc_sync.py` exit status for a slug whose archive lock is held (T8-D14) |

### 5.14 Files Ticket 9 will write (state paths, all gitignored)

- `workspace/_archives/<slug>.zip` — the download (as `dvc_sync.py pull` today).
- `<state_dir>/archives/<slug>.json` — verification record and last outcome:
  `{"pointer_md5", "size", "st_dev", "st_ino", "st_mtime_ns", "verified_at", "linked", "outcome", "reason",
  "exit_code", "pid"}` (`pid` = the writing subcommand's process id, matched against the job record in 5.6.1);
  written atomically by `dvc_sync.py fetch|restore` only, while holding the operation lock.
- `<state_dir>/archives/<slug>.job` — the id of the slug's current or last archive job; written atomically by the
  service only, under `<slug>.start.lock` (5.8).
- `<state_dir>/archives/<slug>.lock` — the operation lock of T8-D14; `<slug>.start.lock` — the start lock of 5.8;
  both taken only through `store.try_locked`.
- `<state_dir>/jobs/<id>.{json,log,err}` — the job (existing layout); its `.json` becomes `lost` only through
  `archives.reconcile_archive_job` on a POST/CLI path (5.6.1).
- Nothing is written by any GET (not a job record, not `lost`, not a lock file); nothing is written inside
  `workspace/<slug>` except by Restore.

### 5.15 Security and data properties (Ticket 9's reviews check each)

1. No network, no subprocess and no write on any GET or list command (counted by tests: `subprocess.Popen` and
   `socket.socket.connect` patched to fail; the state directory's tree compared before and after). Job liveness on a
   GET is the read-only rule of 5.6.1; no GET calls `jobs.reconcile`, `jobs.find`, `jobs.list_jobs` or takes a lock.
2. Credentials: the web process never opens `.env`, `.dvc/config.local`, `~/.aws/*`; `.dvc/config` and
   `.dvc/config.local` are only `stat`ed; dvc output is never rendered.
3. Member access by exact central-directory name only; no extraction on browse; no symlink followed; unsafe names
   never served; text through `display_chunks` and autoescaping; downloads through `download_type` with `nosniff`;
   no `|safe`.
4. Every mutation: POST, session, CSRF, `MUTATING_PATHS`/`SAMPLES`, refused inside a dispatch, consent bound to
   exact bytes, disk rule, per-slug non-blocking start and operation locks (`store.try_locked`).
5. Bounded work per GET (5.13); no unbounded read; caps stated to the user.
6. Data protection as T8-D18; nothing from an archive reaches a model, a remote or an agent.

### 5.16 What Ticket 8 and this design do not do

No streaming from the remote; no remote listing (H-T8-1); no deletion of downloaded copies or leftovers (H-T8-2);
no browser upload or push (uploads stay CLI-only); no browser pull of legacy per-file pointers; no replace of a
local run from the browser; no progress percentage for a transfer (the page shows state and elapsed time); no change
to T-12's `/workspace` index; no protected edit (H-T8-3 is Ticket 9's).

## Part B — Implementation plan

## 6. Steps

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`); Sonnet 5.5
@ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is a checkpointed
pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network (git ls-remote/fetch in t0 only), no push/PR, no
credentials or token files, no patient data (synthetic fixtures only), no production file changed (section 3),
runs the INFLIGHT guard and the scope guard and reports both exit codes. Tests are hermetic: temporary directories
only, no read of the real home or the RUN layout.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/t-8-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-t-8-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/t-8/junit G6_CACHE_DIR=~/.cache/scieflow-t-8 RUN/tools/g6_pytest.sh WT <head-sha> t-8-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing rules
  (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with completion
  notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> t-8-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- **mkdocs:** `cd WT && /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/mkdocs build --strict -d RUN/scratch/t-8/site`
  (memory-capped).
- **INFLIGHT guard** and **scope guard**: section 3.
- **Load-flake nodes:** the four named in the installer's standing rules, one rerun, both results recorded. No
  rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/t-8/executions.jsonl` and states RED and GREEN evidence in its result.
- **Focused set** (clean-tree runner): `tests/test_docs_b2_spec.py tests/test_b2_premises.py tests/test_archive.py
  tests/test_dvc_sync.py tests/core/test_jobs.py tests/core/test_workspace.py`.

### t0 — worktree, plan copy, green base, re-verification (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the ancestor check and the F-2 publish receipt; otherwise
   `BASE_RED` with the reason. `worktree add -b dev/t-8 RUN/scratch/t-8-wt <base>` (on a retry reuse it; never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed probes 0
   failed (directory run and the explicit sealed file). No expected counts are given; t0 records what it measures.
   Only the load-flake rule may clear a failure; otherwise `BASE_RED` with node ids and junit paths.
3. Re-verify on `base` every `path:line` cite of section 4 **and** of Part A (F3): build the list from the
   committed plan copy with a grep over both blocks (section 4 up to `## Part A — Spec`, and Part A up to
   `## Part B — Implementation plan`), resolve a bare `:N` cite to the file named last before it, de-duplicate, and
   record the list used in the baseline file. Write `reviews/t-8-t0-baseline.md` (per cite: still present / moved
   (new line) / changed), with inventories: (a) the merges between `M` and `base` and which section-4
   files they changed; (b) the premise table 4.7 on base: for each premise the existing test node id that pins it,
   or "unpinned" (run each named node once to prove it exists and passes); (c) whether T-12 has merged (if so,
   whether `workspace.describe_from` and `core/control_read.py` exist on base, as the T-12 plan names them);
   (d) `tests/web/mutating_paths.py` and `tests/core/cli_parity.yml` on base (names only, for the spec appendix);
   (e) `git --version` and whether `dvc` is on PATH (recorded, not required).
4. Write `RUN/scratch/t-8/allowed-paths.txt` with the four paths of section 3 (one per line).
5. Copy `reviews/t-8-plan.md` byte for byte to `docs/superpowers/plans/2026-11-03-t-8-archived-run-browsing.md` and
   commit it alone ("docs(plans): Ticket 8 archived-run browsing design plan (T-8 t0)").

Verdicts `DONE` | `BASE_RED`. Result keys: `verdict, summary, head` (the docs commit; on BASE_RED the base),
`base, baseline {default, sealed}, inventories {a..e}, inflight_guard_exit`.

### t1 — the design document, its docs test and the premise tests (Haiku) → t1r (Sonnet)

Files: the t1 rows of section 3, nothing else.

RED first: write `tests/test_docs_b2_spec.py` and run it before the spec file exists (it must fail on the missing
file; record the failing node ids). Then write the spec and turn it GREEN.

`docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md` is, in this order:
- the first line `# B2 archived-run browsing — design (Ticket 8; Part A of docs/superpowers/plans/2026-11-03-t-8-archived-run-browsing.md)`
  and one blank line;
- Part A of the committed plan copy, byte for byte: from the line `## Part A — Spec` up to, not including, the line
  `## Part B — Implementation plan`;
- the line `## Appendix — line references on base <full base sha>` and one bullet per cite t0 found moved or changed
  (`old cite → new cite`, or `<cite>: changed — <one line>`), or the single bullet `- every cite holds on base`.
  The appendix corrects line numbers only; a cite whose content changed is reported in the t1 result as
  `cite_changes` (a design consequence goes back to the human through the reviewer, it is never rewritten here).

`tests/test_docs_b2_spec.py` (hermetic, reads only the two repository files):
1. The spec exists; line 1 is exactly the header above; line 2 is empty.
2. The spec's Part A block is byte-equal to the plan copy's Part A block (both cut at the same two marker lines).
3. The spec contains every section heading `### 5.1` … `### 5.16` of Part A, in order, and the appendix heading.
4. The constants table of 5.13 parses into exactly the 16 names with exactly these values:
   `CONSENT_BYTES 1073741824, DISK_HEADROOM 1.05, DISK_RESERVE_BYTES 1073741824, LIST_CAP 500, PAGE_ENTRIES 500,
   MAX_ARCHIVE_ENTRIES 100000, MAX_CENTRAL_DIRECTORY_BYTES 67108864, POINTER_MAX_BYTES 65536, RECORD_MAX_BYTES 4096,
   CONTROL_MAX_BYTES 262144, INLINE_MAX_BYTES 200000, INDEX_CACHE 4, DVC_OUTPUT_TAIL 65536, MD5_CHUNK_BYTES 1048576,
   JOB_RECORD_MAX_BYTES 65536, LOCKED_EXIT 3`
   (the dict is written in the test, so a changed table fails).
5. `INLINE_MAX_BYTES` equals `scieflow.web.files.MAX_INLINE` (imported; the one existing constant the table names).
6. The exact manuscript sentence of 5.7.4 occurs in the spec.
7. The failure-reason table of 5.9 lists exactly the 12 codes `dvc-missing, no-remote, no-credentials,
   credentials-refused, remote-unreachable, missing-on-remote, disk-full, interrupted, verify-size, verify-md5,
   locked, dvc-failed`, each once.
8. The spec contains no `TBD`, `TODO` or `FIXME`.

`tests/test_b2_premises.py`: one test per premise that t0 marked unpinned (expected P6, P7, P8, P11, P12 on `M`;
follow the baseline file), each named `test_premise_p<N>_<short>` with a docstring naming the spec section that
depends on it:
- P6: an `extract_zip` into a missing destination stages beside it (a `.<name>.extract-` prefix seen through a
  patched `os.rename`/`Path.rename` spy) and, when made to fail during extraction, leaves no destination and no
  staging directory.
- P7: a `build_zip` archive with one byte of a stored member's data altered on disk: `ZipFile.read(member)` raises
  `zipfile.BadZipFile`; the other members still read.
- P8: a project with `workspace/_archives/<slug>.zip.dvc` and no `workspace/<slug>`: the slug is in neither
  `workspace.list_runs(root)` nor `service.list_runs_with_budget(project)`.
- P11: a run fixture with drafts (reuse the fixtures of `tests/core/test_service.py` that make a round), packed with
  `build_zip` and unpacked with `extract_zip` into a fresh project: no `provenance.git` after unpacking, and
  `service.manuscript_history` then returns `available: True` with at least one point.
- P12: the repository `.gitignore` contains the lines `!workspace/_archives/`, `workspace/_archives/*`,
  `!workspace/_archives/*.dvc`, `.dvc/config` and `.scieflow/`.
These are characterisation tests: they are expected to pass on base. Their RED evidence is a mutation run recorded
in the result for each (the smallest edit of the premise in a throwaway copy under `~/.cache/scieflow-t-8-t1/` — e.g.
P8: drop `"_"` from the skip tuple — turns that test red); the mutation is never committed.

Commit (one or more new commits, messages ending "(T-8 t1)"). Then the focused set on the clean tip, the INFLIGHT
and scope guards, and `mkdocs build --strict`.

Result keys: `verdict DONE, summary, head, base, inflight_guard_exit, scope_guard_exit, commits, red, green,
mutations, cite_changes`.

**t1r checklist (Sonnet):** every Ticket 8 question and definition-of-done item is answered in the committed spec
(5.1 mapping checked against the ticket file); spot-check at least ten cites of section 4 and Part A (at least two
from 5.6.1 and T8-D14) and the appendix on base;
the Part A block is byte-equal to the plan (run test 2); the constants and reason tables equal the plan; each premise
test asserts exactly the cited behaviour, is hermetic, and its mutation evidence is plausible (re-run at least two
mutations in a throwaway copy); no production file in the diff (scope guard); RED evidence in git history (test
commit before spec commit, or the recorded failing run); focused set re-run on the clean HEAD; mkdocs strict exit 0.
Output `reviews/t-8-t1-review-r<N>.md` (line 1 `VERDICT: PASS|CHANGES|FAIL`). CHANGES → t1f (new commits) → t1r
(max 3 rounds); FAIL only for a design problem the plan cannot absorb.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip of `dev/t-8`: full default suite 0 failed / 0 errors (absolute counts and the delta
against t0, explained by the two new test files); sealed probes 0 failed (directory and explicit file); the two new
test files once more under `env -i HOME=<empty dir under ~/.cache/scieflow-t-8-branch> PATH=/usr/bin:/bin`
(hermeticity); `mkdocs build --strict` into `RUN/scratch/t-8/site`; INFLIGHT and scope guards; linear history (no
merge commits, at least one commit for t0 and for t1); `git diff base..tip -- tests/` adds the two files and changes
no existing test. Writes `RUN/scratch/t-8/pr-body.md` (plain English: what the design decides (section 1), the
decisions T8-D1..D18, items H-T8-1..4, that no behaviour changes, absolute test counts, the residuals of section 8,
"please merge with a merge commit"; no credentials, prompts or patient data) and `reviews/t-8-branch-report.md`.
Any failure → verdict `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: (1) spec: every Ticket 8 question and definition-of-done item answered, numbers stated as
numbers, the failure section complete, the decisions consistent with each other and with DECISIONS D7 and D9-D11/D23,
the Ticket 9 invariants (ticket 09 file) all implementable from the spec without a new decision; (2) evidence: re-run
the focused set and the explicit sealed file on the tip, confirm the branch result's counts, re-run both guards and
mkdocs; (3) scope: only the four files; `pr-body.md` accurate. Output `reviews/t-8-final-review-r<N>.md`. PASS → the
installer's publish step; CHANGES → final_fix (new commits, the full default suite and sealed probes re-run) →
final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/t-8-run.yml`, linear)

```
t0        Haiku   worktree, plan copy, green base        DONE→t1, BASE_RED→escalate
t1        Haiku   spec document, docs test, premises     DONE→t1r
t1r       Sonnet  review                                 PASS→branch, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                      DONE→t1r
branch    Haiku   full suites + PR body                  DONE→final, RED→escalate
final     Sonnet  whole-branch review                    PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                      DONE→final
(publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install; no protected edits)
```

Caps `{max_cards: 25, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/t-8-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix, branch and final cards also report `inflight_guard_exit` and `scope_guard_exit`. Inputs:
handoff, decisions, gap, `pilot` (`handoffs/pilot-roadmap-20261024/pilot-roadmap-20261011.md`), `f2plan`
(`reviews/f-2-plan.md`), `t12plan` (`reviews/t-12-plan.md`); the installer adds `plan`. The ticket 08/09 files live
outside RUN; the briefs name them by absolute path and sha256.

## 8. Risks and residuals (stated, not hidden)

- **R1 DVC behaviour unverified here.** `dvc` is not installed on this host; cache layout, default `cache.type` and
  error wording come from documentation and existing code. Ticket 9 pins its classifier with fixtures; real wording
  that differs falls to `dvc-failed` with the log path, never to a wrong specific reason.
- **R2 Peak disk is pessimistic.** Counting the archive twice on one filesystem may refuse a download that a reflink
  or hard-link checkout would have fitted; the refusal states both figures.
- **R3 MD5 verify reads the whole file once** (minutes for hundreds of GB); it runs as a cancellable job.
- **R4 The pointer is only as current as your git checkout.** A run pushed elsewhere appears after `git pull`;
  `outdated` covers a pointer that moved under a verified copy.
- **R5 The browser starts a network transfer with your credentials** (T8-D5); bounded by session, CSRF, exact-byte
  consent, the disk rule, the lock and the no-dispatch rule; the existing DNS-rebinding residual of the loopback app
  applies (T-12 H-T12-3).
- **R6 Downloaded copies accumulate** in `workspace/_archives/` and the DVC cache until you delete them (H-T8-2).
- **R7 Premise tests are characterisation tests**: they prove today's facts, not Ticket 9's code; their RED evidence
  is a recorded mutation.
- **R8 No real archive on main** (only legacy per-file pointers); Ticket 9 builds and tests on synthetic archives.
- **R9 Pid reuse.** Liveness is the existing pid-only test (`jobs._alive`, `core/jobs.py:346-353`), as for every
  ScieFlow job. If a dead archive job's pid is reused by another process, the slug shows `downloading` until that
  process ends, and Cancel would signal its process group (only if it is a group leader with that same id). The same
  residual exists for every job today; a start-time check is a `jobs` change outside Ticket 9's scope.

## 9. After T-8

Ticket 9 (`t-9`, `after: [t-8, t-12]`) plans from the committed spec
`docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md` on a main that contains T-12 (its
`describe_from`, control reads and UI rule). Ticket 9's plan carries the proposed protected edit H-T8-3 for the
human. Then Tickets 13-15 (design-only browser modules).
