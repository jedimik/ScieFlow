# T-9 archived-run browsing — implementation design (Part A of docs/superpowers/plans/2026-11-04-t-9-archived-run-browsing.md; the binding design is docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md)

## Part A — Spec

### 5.1 Relation to the approved design

The B2 design (T-8 Part A, committed as `docs/superpowers/specs/2026-11-03-b2-archived-run-browsing-design.md`) is
binding: its decisions T8-D1..D18, states (B2:5.5), read rules (B2:5.6, 5.6.1), page (B2:5.7), actions (B2:5.8),
failure codes and sentences (B2:5.9), degradation (B2:5.10), CLI and names (B2:5.11, 5.12), constants (B2:5.13),
state files (B2:5.14) and security properties (B2:5.15). This Part A adds only what the design left to Ticket 9:
module layout, function signatures, return shapes, template names and the test matrix. Where anything below seems to
contradict B2, B2 wins and the reviewer reports it as a finding. Line corrections in the B2 spec's appendix apply.

### 5.2 Decisions (T9-D1..D16)

- **T9-D1 One core module.** `src/scieflow/core/archives.py` holds everything of B2:5.11's "pointer parsing, states,
  the classifier, bounds, the zip index, `effective_job` and `reconcile_archive_job`"; it imports neither `web` nor
  `service`. `scripts/sflib/archive.py` gains only `md5_file` and `write_record` (used by `dvc_sync.py`).
  `dvc_sync.py` imports `scieflow.core.archives` (it already imports `scieflow.core.config`, `dvc_sync.py:31`) for
  constants, paths, the classifier and the record shape, so one definition serves both processes.
- **T9-D2 Service signatures and refusals.** The design's signatures (B2:5.8) gain keyword-only `yes: bool = False`
  and `wait: bool = False` on `fetch_archive` and `restore_archive`. `yes=True` is the terminal's `--yes`: it stands
  for consent at any size, for an unknown size and for a replace (B2:5.4); the web never passes it. `wait=True` (the
  terminal) waits for the job in the foreground and returns its outcome; `wait=False` (the web) starts a daemon
  thread that waits, as `service.py:279` does. Every refusal is `ArchiveRefused(ServiceError)` with `reason` (a code of
  B2:5.9), `message` (its fixed sentence) and `details` (numbers only: sizes, free bytes, the exact `--yes` command).
  Pages catch it and re-render the archive page; the API returns `409 {"reason", "message"}`; the CLI prints the
  message and exits 1. An unknown slug or a slug without a pointer is a plain `ServiceError` (404, `app.py:55-57`).
- **T9-D3 Verify-only is explicit.** For state `unverified` the service starts `dvc_sync.py fetch SLUG --verify-only
  --state-dir DIR`; with `--verify-only` the subcommand never invokes `dvc` (it fails `verify-size` instead). The
  liveness rule (B2:5.6.1 step 4) tells `verifying` from `downloading` by that argv flag.
- **T9-D4 Captured dvc output.** `fetch` runs `dvc` through the new `run_cmd_tail(cmd, cwd, tail=DVC_OUTPUT_TAIL)`:
  stdout and stderr merged, copied line by line to the subcommand's stdout (the job log keeps everything), the last
  `DVC_OUTPUT_TAIL` bytes kept in memory; `archives.classify(exit_code, tail: bytes) -> str` is pure and is the only
  classifier (B2:5.9 order, case-insensitive). Launch `FileNotFoundError` → `dvc-missing`. `push`/`pull` keep
  `run_cmd`.
- **T9-D5 `locked` from the job.** A finished archive job with exit code `LOCKED_EXIT` reads as `locked`. Before
  starting a job the service also probes the operation lock (`store.try_locked(<state_dir>/archives/<slug>)`, entered
  and left at once) and refuses `locked` without starting anything when a `push`/`pull` holds it; the job re-checks
  under its own lock (the probe is advisory, the job's lock decides).
- **T9-D6 Data level from bytes.** `levels.parse_text(text: str) -> DataFile | Invalid` (YAML parse + `_check`, the
  same `Invalid` reasons as `read`) and `levels.level_of(got) -> LevelInfo` (the body of `level_info`) are extracted;
  `read` and `level_info` call them and keep their behaviour (all existing `tests/core/data/test_levels.py` green
  unchanged). A `data.yml` member is decoded as UTF-8 (`Invalid("data.yml: cannot be read")` on error) and parsed
  with `parse_text`; an oversized member is `too large to read here` and its level reads `L2` (fail-closed, as
  an unreadable file in `read`).
- **T9-D7 CLI module.** `src/scieflow/core/archives_cli.py` defines `archives_cmd` and the `archive` group; the end
  of `core/workspace.py` registers both on the `workspace` group. `archives_cli.py` imports `service` inside each
  command only (no import cycle: `service` imports `workspace`). Every command prints `--json` output as the service
  dict, otherwise a sentence built with `.get` so a parity recorder dict exits 0 (section 4.3).
- **T9-D8 Templates.** `archive.html` (the archive page with its panels), `archive_files.html` (entries),
  `archive_file.html` (one text member); the dashboard section is in `dashboard.html`. No new CSS class, no
  JavaScript, no `|safe`, no browser dialog; controls copy their siblings (section 4.3).
- **T9-D9 Listing.** All names in `workspace/_archives/` are enumerated (names only, streamed, no stat) so the list
  sorts by slug and the "N more not shown" count is exact; per-pointer reads happen only for the first `LIST_CAP`
  names after sorting. Legacy `workspace/<slug>.dvc` pointers come from the same single listing of `workspace/` and are
  read only when `workspace/<slug>` does not exist (T8-D2).
- **T9-D10 Not on the MCP server.** No MCP tool is added, and `list_runs_with_budget` (the MCP runs tool's source) is
  unchanged, so archived-only runs and archive contents never reach an agent (T8-D18). A test pins it.
- **T9-D11 Member responses.** Text members are rendered from `display_chunks` over `CHUNK_BYTES` reads; other
  members are a Starlette `StreamingResponse` over a sync generator (run in the threadpool, plain `def` handler) of
  `CHUNK_BYTES` reads with `download_type` of the member's `PurePosixPath` name, `X-Content-Type-Options: nosniff` and,
  for non-inline types and `download=1`, `Content-Disposition: attachment; filename="<basename>"` (quoted, control
  characters and quotes replaced by `_`). No range support.
- **T9-D12 Bounded file reads.** Pointers, verification records, `<slug>.job` and the one job record are read through
  `control_read.open_dir` + a bounded byte read (`read_bytes`, or T-12's equivalent if t0 finds one public),
  no-follow, regular files only, `cap + 1` bytes; JSON and YAML are parsed only after the bound holds.
- **T9-D13 Disk rule.** Receiving locations: the DVC cache `<root>/.dvc/cache` (or its nearest existing ancestor) and
  `workspace/_archives/` for a download, `workspace/` for a restore; grouped by `os.stat(...).st_dev`; requirement per
  group `ceil(received × DISK_HEADROOM) + DISK_RESERVE_BYTES`, free from `shutil.disk_usage`. A custom DVC
  `cache.dir` is not read (ScieFlow reads no DVC configuration content, B2:5.6); residual R5.
- **T9-D14 Record after link.** `fetch` writes the verification record after `link_to_cache`, with the final
  `os.stat` of `<slug>.zip` (a hard link changes the inode), so a `ready` copy stays `ready`.
- **T9-D15 Restore re-checks.** `dvc_sync.py restore` re-reads pointer, zip stat and record under the operation lock
  and refuses `not-ready` unless they match; then `verify_zip` (full CRC pass), then `extract_zip` without `force`.
- **T9-D16 Names.** Everything named in B2 keeps its name; the additions are only: `ArchiveRefused`, `yes`/`wait`
  keywords, `--verify-only`, `run_cmd_tail`, `md5_file`, `write_record`, `levels.parse_text`/`level_of`,
  `control_read.read_bytes` (conditional), `archives_cli.py`, and the template names of T9-D8.

### 5.3 Module map

```
browser (/ , /archives/<slug>, …/files, …/file)          CLI scieflow workspace archives | archive …
   │ pages.py / api.py (def handlers; POST under MUTATE)        │ archives_cli.py (thin)
   ▼                                                            ▼
service.archived_runs / archive_detail / archive_entries / archive_member / fetch_archive / restore_archive / cancel_archive_job
   │ (refusals: ArchiveRefused; start lock; jobs.start; daemon wait or foreground wait)
   ▼
core/archives.py ── pointers, states, effective_job (read-only), reconcile_archive_job, classify, disk, zip index
   │                    uses control_read (bounded no-follow reads), store.try_locked, levels.parse_text,
   │                    workspace.describe_from, budget.remaining_fraction
   ▼
jobs.start(argv=[python, -B, scripts/dvc_sync.py, fetch|restore, SLUG, …, --state-dir, DIR], run_dir=None)
   ▼
scripts/dvc_sync.py fetch|restore ── operation lock, dvc pull (run_cmd_tail), size + MD5, link_to_cache,
                                     verification record / outcome (sflib.archive.write_record)
```

### 5.4 `core/archives.py` interfaces

Constants: exactly the 16 names and values of B2:5.13 as module attributes (`CONSENT_BYTES` … `LOCKED_EXIT`), plus
`ARCHIVE_STATE = "archives"` (the state subdirectory) and `JOB_KINDS = ("archive-fetch", "archive-restore")`.
`tests/core/test_b2_constants.py` parses the committed B2 spec's 5.13 table and asserts every value equals the module
attribute, and `INLINE_MAX_BYTES == web.files.MAX_INLINE`.

Paths (pure): `state_dir_for(project) -> Path` (= `project.state_dir / "archives"`); `lock_path(state, slug)`
(`<slug>`, so `FileLock` uses `<slug>.lock`), `start_lock_path` (`<slug>.start`), `record_path` (`<slug>.json`),
`job_pointer_path` (`<slug>.job`).

Pointers:
- `Pointer(slug, kind: "zip"|"directory"|"unreadable", md5, size: int|None, dvc2: bool, reason: str|None, path)`.
- `read_pointer(dir_fd, name, slug, *, legacy=False) -> Pointer`: bounded by `POINTER_MAX_BYTES`, `yaml.safe_load`;
  B2:5.5 rules (one `outs` entry, 32 lowercase hex `md5`, `path == "<slug>.zip"`, optional int `size >= 0`, optional
  `hash: md5`; a `.dir` md5 in an `_archives` pointer, a symlink, oversize or any other shape → `unreadable` with the
  reason word; legacy pointers need a `.dir` md5 → `directory` with `size` and `nfiles`).
- `discover(project) -> Discovery(rows: list[Pointer], total, shown, skipped_names, cap_note)`: T9-D9; names not
  passing `project.clean_slug` are counted in `skipped_names`, never listed.

Local state (B2:5.5): `local_state(project, ptr) -> LocalState(state, relation, zip_stat, record, job, leftovers,
symlink_note)` with `state` ∈ `not-downloaded|downloading|incomplete|unverified|verifying|different|outdated|ready|
restoring`, `relation` ∈ `archived-only|also-local|alias`. Inputs only: `lstat` of `_archives/<slug>.zip` (and the
B2:5.5 symlink rule: realpath inside `<root>/.dvc/cache/` else `incomplete` with the note), bounded read of the record
(`RECORD_MAX_BYTES`), `effective_job`, `lstat` of `workspace/<slug>`, `lstat` of the four leftover patterns of
B2:5.7.6 (the two `workspace/.<slug>.*` patterns by a name filter over the `workspace/` listing already made). Order of
precedence: a running job's state first, then the file states in the B2 table order.

Liveness (B2:5.6.1, exact): `effective_job(project, slug) -> JobView(id, kind, running: bool, verify_only: bool,
outcome: str|None, exit_code)` — pure read; `reconcile_archive_job(project, slug) -> bool` — the only writer of
`lost` for archive jobs, `state="lost"`, `finished=jobs._now()`, `jobs.save` (so `records.check_job` holds), no event;
called only under the start lock by the service's fetch/restore/cancel.

Classifier and disk: `classify(exit_code: int|None, tail: bytes) -> str` (B2:5.9, first match wins; `None`/signal →
`interrupted`; exit 127 → `dvc-missing`; anything unmatched → `dvc-failed`); `SENTENCES: dict[code, str]` with the
exact B2:5.9 and refusal sentences (`<n>`, `<log path>`, `<12 chars>` filled by `sentence(code, **values)`);
`disk_check(root, receivers: list[tuple[Path, int]]) -> DiskCheck(ok, groups=[{path, received, needed, free}])` (T9-D13).

Zip reading (state `ready` only; each opens the zip read-only with `O_NOFOLLOW`, except the accepted cache-symlink
case of B2:5.5):
- `precheck_central_directory(fd, size) -> (entries, cd_bytes)`: reads at most the last 65,557 bytes, finds the
  end-of-central-directory record, follows the Zip64 locator/record when present; refuses `too-many-entries` above
  `MAX_ARCHIVE_ENTRIES` or `MAX_CENTRAL_DIRECTORY_BYTES`, `unreadable-zip` when no record is found. Runs before
  `zipfile.ZipFile` parses anything.
- `index(project, slug) -> ZipIndex`: one `zipfile.ZipFile` open per `(st_dev, st_ino, st_size, st_mtime_ns)` key,
  kept in a process-wide LRU of `INDEX_CACHE` entries guarded by a `threading.Lock`; `ZipIndex` holds the member list
  classified `file|dir|link|unsafe|encrypted|unsupported` (unsafe = the `_check_members` rule applied to the name
  alone, without touching the filesystem; encrypted = flag bit 0; unsupported = compression not stored/deflated), the
  implied directories, totals and counts. Nothing is cached on disk.
- `entries(idx, path, page) -> {path, page, pages, total, entries: [{name, path, kind, size, target?}], hidden_locks,
  unsafe_count, note}`: B2:5.6 (directories first, then files, by name; `.lock` hidden; `PAGE_ENTRIES` per page with
  "entries 501-1000 of 2,345"; a link shows its target text from the member bytes, bounded by 4096, never followed).
- `open_member(idx, path) -> (ZipInfo, reader)`: `path` must equal a central-directory name exactly and be a `file`
  entry, else `no-such-member`; the reader yields `CHUNK_BYTES` blocks; `zipfile.BadZipFile`/CRC error →
  `corrupt-member` (inline) or the stream ends and is logged (download).
- `summary(idx) -> dict`: the control members of B2:5.6 (each only when `file_size <= CONTROL_MAX_BYTES`, else
  "too large to read here"; unparsable → "unreadable"; absent → "not in this archive"); `workspace.describe_from(slug,
  status, config, updated, updated_at)` with `updated`/`updated_at` from the members' `ZipInfo.date_time` labelled as
  the packing machine's local date (B2:5.6 last bullet); budget remaining via `budget.remaining_fraction` (errors →
  "unreadable"); data level via T9-D6; totals.

### 5.5 Small additions outside `archives.py`

- `store.Busy(Exception)`; `store.try_locked(path)`: a context manager over `FileLock(str(path) + ".lock",
  timeout=0)`; `filelock.Timeout` → `Busy`; the parent directory is created only by callers on write paths (a GET never
  calls it). `store.locked` and `LOCK_TIMEOUT` unchanged.
- `control_read.read_bytes(dir_fd, name, cap) -> ControlRead` (data = bytes): the open/`fstat`/`S_ISREG`/`cap + 1`/
  re-`fstat` rules of T-12 5.6 without parsing; added only if T-12's module has no public equivalent (t0 inventory (c));
  `read_yaml` unchanged either way.
- `levels.parse_text`, `levels.level_of` (T9-D6).
- `sflib.archive.md5_file(path, chunk) -> str` (streamed); `sflib.archive.write_record(path, data: dict)` (JSON,
  atomic: temp file in the same directory, `fsync`, `os.replace`; creates the parent).

### 5.6 `scripts/dvc_sync.py`

- All four of `push`, `pull`, `fetch`, `restore` accept `--state-dir DIR` (default `Project(<root>).state_dir`).
  `cmd_push`/`cmd_pull` gain keyword `state_dir: Path | None = None` (same default), and wrap each archive-mode slug's
  `push_archive`/`pull_archive` call in `store.try_locked(<state>/archives/<slug>)`; `Busy` → print
  `archives.sentence("locked")` with the slug, rc `LOCKED_EXIT` for that slug (counted by the existing loop).
  Directory-mode slugs take no lock (they never touch `_archives/`).
- `fetch SLUG [--replace] [--verify-only] [--remote NAME] [--state-dir DIR]` (B2:5.8 plus T9-D3): operation lock
  (busy → `locked` sentence, exit `LOCKED_EXIT`, nothing written); pointer must be `zip`; unless `--verify-only` or
  (size matches and not `--replace`): `run_cmd_tail(["dvc", "pull", *_remote_args(remote), *(["--force"] if replace
  else []), <pointer rel>])`; non-zero → outcome `failed` with `classify(rc, tail)`; then size check (`verify-size`),
  `md5_file` with `MD5_CHUNK_BYTES` (`verify-md5`), `link_to_cache` (T9-D14), record `{pointer_md5, size, st_dev,
  st_ino, st_mtime_ns, verified_at, linked, outcome: "ready", reason: null, exit_code: 0, pid}`; every failure writes
  `{outcome: "failed", reason, exit_code, pid}` (keeping a previous verification only when the file stat still
  matches it, so a failed re-download over an untouched `ready` copy stays `ready`, B2:5.5); exits 0 on `ready`, 1 on
  a failure. Never extracts, never deletes a zip.
- `restore SLUG [--state-dir DIR]`: operation lock; T9-D15; `extract_zip(zip, workspace/<slug>)` without force;
  outcome `{outcome: "restored"|"failed", reason, exit_code, pid}` merged into the record; exit 0/1.
- `main()` dispatches the two new subcommands like `push`/`pull`; `.env` loading unchanged (`:387-388`).

### 5.7 Service (`src/scieflow/core/service.py`)

New exception `ArchiveRefused(ServiceError)` (`reason`, `message`, `details`). New public functions (each also in
`tests/core/cli_parity.yml`, E2):

| Function | Returns | CLI (parity, `new: true`, args) |
|---|---|---|
| `archived_runs(project)` | `{rows: [{slug, kind, size, md5_12, state, relation, outcome, reason, command?}], total, shown, cap, cap_note, skipped_names}` | `workspace archives` `[]` |
| `archive_detail(project, slug)` | header, `environment {dvc, remote_config}`, `disk [...]`, `state`, `relation`, `outcome`, `actions [...]`, `leftovers [...]`, `manuscript` (the B2:5.7.4 sentence), and in state `ready`: `data_level {level, reason, banner}`, `summary {…}`, `totals {…}` or `browse_refusal` | `workspace archive show` `["r1"]` |
| `archive_entries(project, slug, path="", page=1)` | 5.4 `entries` | `workspace archive ls` `["r1"]` |
| `archive_member(project, slug, path, *, mode="view")` | `view`: `{path, size, text, complete, reason}`; `raw`: `{path, size, media_type, attachment, filename, stream}` | `workspace archive cat` `["r1", "status.yml"]` |
| `fetch_archive(project, slug, *, confirm_size=None, replace=False, yes=False, wait=False)` | `{slug, state, started, job_id, message}` (+ `outcome`, `reason` when `wait`) | `workspace archive fetch` `["r1"]` |
| `restore_archive(project, slug, *, confirm_size=None, yes=False, wait=False)` | same shape | `workspace archive restore` `["r1"]` |
| `cancel_archive_job(project, slug)` | `{slug, cancelled, state, message}` | `workspace archive cancel` `["r1"]` |

Rules (order matters; every refusal starts nothing and writes nothing):
1. `slug` through `project.clean_slug`; no pointer for it → `ServiceError` (404).
2. `fetch`/`restore`/`cancel` inside a dispatch (`in_dispatch()`) → `in-dispatch`.
3. Enter `store.try_locked(start_lock_path)` (creating `<state_dir>/archives/` first; `Busy` → `locked`); under it:
   `reconcile_archive_job`; a still-running job → `locked`; the operation-lock probe (T9-D5).
4. `fetch`: non-`zip` pointer → `not-zip-pointer`; state `ready` → no-op `{started: false, state: "ready"}`; state
   `unverified` → verify-only job (no consent, no disk check, no dvc check); a local zip that is not `ready` and
   `replace` false → `replace-required`; `dvc` not on `PATH` → `dvc-missing`; unknown size and not `yes` →
   `size-unknown` (details carry `scieflow workspace archive fetch <slug> --yes`); disk rule (known size) →
   `no-space` with the groups; consent: (`size >= CONSENT_BYTES` or `replace`) and not `yes` → `confirm_size` absent →
   `confirm-required`, differs from the size computed now → `confirm-mismatch`.
5. `restore`: state not `ready` → `not-ready`; relation not `archived-only` or `workspace/<slug>` non-empty →
   `run-exists`; unpacked size (sum of `file_size`) through the disk rule and the consent rule (no `replace` here).
6. Start: `jobs.start(project, argv, kind=…, cwd=project.root, run_dir=None, label=slug)` with argv
   `[sys.executable, "-B", str(project.root / "scripts" / "dvc_sync.py"), "fetch"|"restore", slug, (--replace),
   (--verify-only), "--state-dir", str(project.state_dir)]`; launch `FileNotFoundError` → `dvc-missing`; atomic write
   of the job id to `<slug>.job`; release the start lock; then `wait` → `jobs.wait` in the foreground and the outcome
   from `effective_job`; else a daemon thread runs `jobs.wait`.
7. `cancel`: under the start lock, reconcile; a running job → `jobs.cancel` on the record read by path (never
   `jobs.find`), `{cancelled: true, state: <file state>}`; none → `{cancelled: false}`.
8. GET functions (`archived_runs`, `archive_detail`, `archive_entries`, `archive_member`) never write, lock, start a
   process or open a socket; any unexpected read error degrades to a reason (B2:5.10 "Every GET degrades, never
   raises"); only an unknown slug is an error.

### 5.8 CLI (`scieflow workspace …`)

`archives [--json]`; `archive show SLUG [--json]`; `archive ls SLUG [DIR] [--page N] [--json]`; `archive cat SLUG
MEMBER [--out FILE]` (raw bytes; to stdout only when stdout is not a terminal or the member is text, else exit 1 with
"use --out FILE"); `archive fetch SLUG [--yes] [--replace]`; `archive restore SLUG [--yes]`; `archive cancel SLUG`.
`fetch`/`restore` call the service with `yes=<flag>`, `wait=True` and print the outcome sentence; a refusal prints
its message (for `confirm-required` the size and the exact `--yes` command) and exits 1. `workspace list` is
unchanged.

### 5.9 Web

Routes (all plain `def`, under the session router; POSTs under `MUTATE`; `{slug}` through `clean_slug`, unknown → 404):

| Method | Path | Handler | Renders / returns |
|---|---|---|---|
| GET | `/` | `dashboard` (edited) | adds `archives = service.archived_runs(project)` and `archived_local = {slugs with relation also-local}`; section *Archived runs* after the runs table, before *Open gates*; badge `<span class="dim">archived</span>` linking to `/archives/<slug>` on local rows |
| GET | `/archives/{slug}` | `archive_page` | `archive.html` from `archive_detail` |
| GET | `/archives/{slug}/files?path=&page=` | `archive_files` | `archive_files.html` from `archive_entries` |
| GET | `/archives/{slug}/file?path=&download=` | `archive_file` | `archive_file.html` (text) or the stream (T9-D11) |
| POST | `/archives/{slug}/fetch` | `archive_fetch` | form `confirm_size`, `replace`; success → 303 to `/archives/{slug}`; `ArchiveRefused` → `archive.html` re-rendered (200) with `refusal`; `confirm-required` renders the confirmation panel |
| POST | `/archives/{slug}/restore` | `archive_restore` | form `confirm_size`; as above |
| POST | `/archives/{slug}/cancel` | `archive_cancel` | 303 to `/archives/{slug}` |
| GET | `/api/v1/archives`, `/api/v1/archives/{slug}`, `/api/v1/archives/{slug}/entries?path=&page=` | | the service dicts as JSON |
| POST | `/api/v1/archives/{slug}/fetch`, `/restore`, `/cancel` | | the service dict; `ArchiveRefused` → `409 {"reason", "message"}` |

E1 adds the six POST paths to `MUTATING_PATHS` and `SAMPLES` (`{}` for cancel, `{"confirm_size": "1"}` for fetch and
restore; the guard tests are refused before routing).

The confirmation panel (B2:5.4): exact bytes and the human size, free and needed bytes per receiving filesystem, the
destination path, "runs until done or cancelled", a hidden `confirm_size` equal to the computed bytes (and `replace=1`
when replacing), a *Confirm* button and a *Cancel* link back to the page; all in the page, no dialog. Refusals render
as `<p class="state-failed" role="alert">` with the fixed sentence; the job log is named by its project-relative
path, never shown. The L2 banner and the manuscript sentence are exactly B2:5.7.2 and 5.7.4. Every value is
autoescaped; the CSRF token is only in POST bodies.

### 5.10 Data written (B2:5.14, unchanged)

`workspace/_archives/<slug>.zip` (download), `<state_dir>/archives/<slug>.json` (record and last outcome, written only
by `dvc_sync.py fetch|restore` under the operation lock), `<slug>.job` (service, under the start lock), the lock
files `<slug>.lock`, `<slug>.start.lock` (and the sibling `.lock` files `store.write_text` may leave for `.job`),
`<state_dir>/jobs/<id>.{json,log,err}`; `workspace/<slug>` only by Restore. No GET writes anything, including no
directory creation.

### 5.11 Security and data properties (each has a test; Ticket 9's reviews check each)

1. GETs (dashboard, archive page, entries, member, the three GET APIs, `workspace archives`/`archive show|ls|cat`):
   `subprocess.Popen` and `socket.socket.connect` patched to raise; the state directory and `workspace/` trees (names,
   bytes, mtimes) identical before and after; `jobs.reconcile`, `jobs.find`, `jobs.list_jobs` and `store.try_locked`
   patched to raise.
2. Credentials: the web process never opens `.env`, `.dvc/config`, `.dvc/config.local` or anything under `~/.aws`
   (an `io.open`/`os.open` spy over the GET and POST paths); `.dvc/config*` are only `stat`ed; dvc output never
   rendered (a fake dvc printing a marker string; the marker never appears in any page or API body).
3. Members: exact central-directory names only; `../x`, absolute and drive names never served; no extraction on
   browse; links never followed; text autoescaped (`<script>` in a member renders as text); SVG and HTML members are
   attachments with `nosniff`.
4. Mutations: POST + session + CSRF (`test_mutations.py`), in `MUTATING_PATHS`/`SAMPLES`, refused in a dispatch,
   consent bound to exact bytes (`confirm-mismatch`), disk rule, non-blocking locks (two concurrent POSTs start one
   job; a held start lock answers `locked` in under one second).
5. Bounds: every B2:5.13 bound has a boundary test (cap, cap + 1).
6. Data protection: archive content reaches no model, remote or agent; no dispatch/conversation/scan/release control
   on archive pages; the MCP server lists no archive tool and its runs tool omits archived-only slugs (T9-D10); an
   `L2` archive shows the banner; downloads are not refused by level.
7. UI rule: no `alert(`, `confirm(`, `prompt(` or `<dialog` in the new templates; every new control uses an existing
   sibling's element and classes (the reviewers check visually from the template diff).

### 5.12 Docs (exact sentences, pinned by `tests/test_b2_docs.py`)

- **W1** `docs/web.md`, new section "### Archived runs" under "## Pages": "Archived runs are listed from the pointer
  files git already carries; opening a page never contacts the DVC remote or starts `dvc`."
- **W2** same section: "Download fetches the one zip and checks its size and checksum; it never unpacks it. Restore
  is a separate button and unpacks only into an absent or empty `workspace/<slug>`."
- **W3** same section: "At 1 GiB (1,073,741,824 bytes) or more the page asks you to confirm the exact size first; from
  the terminal the same needs `--yes`."
- **W4** same section: the B2:5.7.4 manuscript sentence, verbatim.
- **W5** `docs/web.md` "What stays CLI-only": the **DVC uploads** bullet stays; a new bullet: "**Pulls of old
  per-file DVC pointers** (`workspace/<slug>.dvc`) — one such pull can be tens of thousands of objects; use `uv run
  scripts/dvc_sync.py pull <slug>`."; the API table gains the six archive rows.
- **D1** `docs/DVC_STORAGE.md`, new "### 3.6 Browse and download from the web app": "Downloads from the web app or
  `scieflow workspace archive fetch` use this checkout's own DVC configuration and credentials, as `dvc_sync.py pull`
  does; ScieFlow never reads the credentials itself."
- **D2** same section: "Each filesystem that receives a download must keep 1 GiB free afterwards, with 5% headroom;
  otherwise the download is refused."
- **C1** `docs/cli.md` `## scieflow workspace`: the line "Read-only views over `workspace/`." becomes "Read-only views
  over `workspace/`, plus downloading and restoring archived runs (`archive fetch|restore`)." and the seven commands of
  5.8 are documented.
- **S1** `skills/workspace-sync/SKILL.md`, after the upload table: "A download of 1 GiB or more (`scieflow workspace
  archive fetch|restore … --yes`) is the user's decision: ask before passing `--yes`; inside a dispatch the commands
  refuse."
- **A1** root `AGENTS.md` rule 15: the protected edit of section 3 (H-T9-1), byte for byte.
The docs test also checks that no page claims a browser upload, a browser delete or a remote listing, and that
`mkdocs build --strict` stays clean (branch step).

### 5.13 Ticket 9 definition of done → tests

| Ticket 9 item | Test |
|---|---|
| Behaviour matches ticket 08's approved spec, point by point | t1-t6 tests; the final review maps every B2 section to a test node (table in `reviews/t-9-final-review-r<N>.md`) |
| A download that crosses the consent threshold asks before starting | `tests/web/test_b2_actions.py::test_fetch_at_threshold_renders_confirmation_and_starts_nothing` (pointer `size = CONSENT_BYTES`; no job record, no `<slug>.job`), `::test_fetch_below_threshold_starts_on_one_click` (`CONSENT_BYTES - 1`), `tests/core/test_b2_cli.py::test_fetch_at_threshold_needs_yes` |
| Every failure named in ticket 08 reaches the page as a stated reason, not a traceback: missing remote, missing credentials, interrupted pull, `.partial` present, older schema | `tests/web/test_b2_failures.py`: one test each with the fake dvc (`no-remote`, `no-credentials`, `credentials-refused`, `remote-unreachable`, `missing-on-remote`, `disk-full`), a killed job (`interrupted`, both on GET without a write and after the next POST as `lost`), a `<slug>.zip.partial` leftover, a DVC 2 pointer, an `unreadable` pointer, an archive without `status.yml`/`budget.yml`/`data.yml`, an old archive with `provenance.git/`; each asserts the fixed sentence in the page and `"Traceback" not in body` |
| Manuscript history reads as regenerating after pull and sync, never as an empty history | `tests/web/test_b2_archive_page.py::test_manuscript_sentence_and_no_history_panel` (the exact B2:5.7.4 sentence; no history list/diff form); H-T9-2 |
| Handlers are `def`; mutating routes in `MUTATING_PATHS` and `SAMPLES` with CSRF | existing `test_async_routes.py`, `test_read_only.py`, `test_mutations.py` (E1) green |
| No unbounded read: listing paginated or capped, the cap stated when hit | `tests/core/test_b2_pointers_states.py::test_list_cap_and_note` (`LIST_CAP + 1` pointers), `tests/web/test_b2_archive_browse.py::test_entries_paginate_and_say_so` (`PAGE_ENTRIES + 1`), `tests/core/test_b2_zip_index.py::test_too_many_entries_refused_before_parse` |
| Full suite green | branch step: full default suite and sealed probes 0 failed / 0 errors |

### 5.14 What T-9 does not do

No streaming from the remote, no remote listing (H-T8-1), no deletion of downloads or leftovers (H-T8-2), no browser
upload or push, no browser pull of legacy per-file pointers, no replace of a local run from the browser (no `force`),
no progress percentage, no MCP tool, no change to `/workspace` (T-12) or to `workspace list`, no change to
`jobs.py`/`records.py` (pid reuse stays T-8's R9), no DVC configuration reading.

