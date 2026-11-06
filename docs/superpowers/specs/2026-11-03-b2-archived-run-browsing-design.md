# B2 archived-run browsing — design (Ticket 8; Part A of docs/superpowers/plans/2026-11-03-t-8-archived-run-browsing.md)

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

## Appendix — line references on base 63a836b9d7d1d0bf401235213f5089696c5fb2b4

- `src/scieflow/core/workspace.py:173-179` → `src/scieflow/core/workspace.py:174-180`
- `src/scieflow/core/workspace.py:182-226` → `src/scieflow/core/workspace.py:183-227`
- `src/scieflow/core/workspace.py:299` → `src/scieflow/core/workspace.py:302`
- `src/scieflow/core/workspace.py:320-322` → `src/scieflow/core/workspace.py:327-329`
- `src/scieflow/core/service.py:2366-2397` → `src/scieflow/core/service.py:2367-2398`
- `src/scieflow/core/jobs.py:289-290` → `src/scieflow/core/jobs.py:291-292`
- `src/scieflow/core/jobs.py:300-318` → `src/scieflow/core/jobs.py:302-320`
- `src/scieflow/core/jobs.py:321-333` → `src/scieflow/core/jobs.py:323-335`
- `src/scieflow/core/jobs.py:321-337` → `src/scieflow/core/jobs.py:323-339`
- `src/scieflow/core/jobs.py:336-337` → `src/scieflow/core/jobs.py:338-339`
- `src/scieflow/core/jobs.py:346-353` → `src/scieflow/core/jobs.py:348-355`
- `src/scieflow/core/jobs.py:356-408` → `src/scieflow/core/jobs.py:358-410`
- `src/scieflow/core/jobs.py:370` → `src/scieflow/core/jobs.py:372`
- `src/scieflow/core/jobs.py:384` → `src/scieflow/core/jobs.py:386`
- `src/scieflow/core/jobs.py:388` → `src/scieflow/core/jobs.py:390`
- `src/scieflow/core/jobs.py:435-445` → `src/scieflow/core/jobs.py:437-447`
- `src/scieflow/core/jobs.py:463-474` → `src/scieflow/core/jobs.py:465-476`
- `src/scieflow/core/jobs.py:518-527` → `src/scieflow/core/jobs.py:520-529`
- `src/scieflow/core/service.py:1787` → `src/scieflow/core/service.py:1788`
- `src/scieflow/core/service.py:1919` → `src/scieflow/core/service.py:1920`
- `src/scieflow/core/workspace.py:108-131` → `src/scieflow/core/workspace.py:109-132`
- `src/scieflow/core/workspace.py:134-154` → `src/scieflow/core/workspace.py:135-155`
- `src/scieflow/core/workspace.py:143` → `src/scieflow/core/workspace.py:144`
- `docs/web.md:530-549` → `docs/web.md:531-550`
- `docs/web.md:538-540` → `docs/web.md:539-541`
- `src/scieflow/core/service.py:1036-1073` → `src/scieflow/core/service.py:1037-1074`
- `src/scieflow/core/service.py:1022` → `src/scieflow/core/service.py:1023`
- `src/scieflow/core/service.py:1064` → `src/scieflow/core/service.py:1065`
- `src/scieflow/core/service.py:2404-2425` → `src/scieflow/core/service.py:2405-2426`
- `src/scieflow/web/pages.py:187-200` → `src/scieflow/web/pages.py:207-220`
- `src/scieflow/web/pages.py:844-858` → `src/scieflow/web/pages.py:867-881`
- `src/scieflow/web/pages.py:861-893` → `src/scieflow/web/pages.py:884-916`
