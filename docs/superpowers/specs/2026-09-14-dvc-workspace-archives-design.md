# Design: zip archives for DVC workspace push/pull

Date: 2026-09-14
Repos affected: ScieFlow (this repo) only

Opt-in archive mode for `scripts/dvc_sync.py`: a workspace is packed into a
single `.zip` before being pushed to the S3 DVC remote, and unpacked after
being pulled from it. Directory mode (today's behaviour) stays the default.

---

## Problem

`workspace/<slug>` run directories hold many thousands of small files
(NIfTI volumes, tractography outputs, logs, agent transcripts). DVC tracks
them per-file, so a push or pull is request-bound against the CESNET S3
endpoint: transfer time is dominated by per-object round trips, not by
bytes. Packing a run into one object removes that overhead.

The cost is DVC's per-file deduplication. An archive is a single blob: any
byte change re-uploads the whole run. That trade is acceptable for
**finished** runs and wrong for runs still being iterated, so archive mode
must be opt-in per workspace rather than global.

### Constraints measured on this machine (2026-09-14)

- `df` on the repo filesystem: 1007G total, 100G free.
- `.dvc/cache`: 223G.
- Largest workspaces: 254G (`2026-07-segsnake-thalamus-mrtrix-validation`),
  52G (`2026-09-full-brain-sampling-smoke`), then 6.6G, 1.5G, 1.3G, 840M.
- DVC 3.67.1, which supports `dvc add --to-remote`.

Two consequences shape the design:

1. Building an archive needs free space equal to the workspace. The 254G and
   52G runs **cannot** be archived on the current disk. Archive mode must
   refuse them with an explicit number rather than fill the disk.
2. A naive `dvc add` + `dvc push` would also write a full copy of the archive
   into `.dvc/cache`, doubling the cost. `dvc add --to-remote` uploads
   straight to S3 and writes no cache object, so the only transient cost is
   the archive file itself.

---

## Decisions

| Question | Decision |
|---|---|
| Which workspaces | Opt-in per workspace, never automatic |
| Existing directory tracking | Archive pointer replaces it; old S3 blobs left in place, never `dvc gc` |
| Format | `.zip`, `ZIP_STORED` (no compression), Zip64 |
| Pull onto an existing directory | Refuse, unless `--force` |
| Kept archives vs cache | After pull, the kept zip is hardlinked to its own DVC cache object; `.dvc/config` unchanged (amended 2026-09-16) |

`ZIP_STORED` is chosen because the payload is already-compressed data
(`.nii.gz`, `.mif`, `.png`); DEFLATE would spend hours of CPU on the large
runs for near-zero size reduction. The win being bought here is object
count, not bytes.

Old per-file S3 blobs are deliberately orphaned rather than pruned:
`dvc gc --cloud` is irreversible and can delete objects still referenced by
other branches or older commits. The pointers remain recoverable from git
history; reclaiming the storage stays a manual, deliberate act.

---

## 1. Mode resolution

Pointer presence is the source of truth, because on pull the workspace
directory — and therefore its `config.yml` — may not exist yet.

**Pull**, for each slug:

- `workspace/_archives/<slug>.zip.dvc` exists → archive mode
- otherwise → directory mode

**Push**, for each slug, first match wins:

1. `workspace/_archives/<slug>.zip.dvc` exists → archive mode
2. `--archive` passed → archive mode
3. `archive: true` in `workspace/<slug>/config.yml` → archive mode
4. otherwise → directory mode

`--no-archive` forces directory mode even when a pointer exists, which is how
a workspace migrates back.

`config/defaults.yml` gains:

```yaml
archive: false                # true = push/pull this run as a single zip
                              # (see docs/DVC_STORAGE.md). Opt in per run in
                              # workspace/<slug>/config.yml once the run is
                              # finished; archive mode trades DVC's per-file
                              # dedup for far fewer S3 objects.
```

It is read through the existing `config.load_run_config()` overlay; no new
config loader.

---

## 2. New module — `scripts/sflib/archive.py`

Pure filesystem functions. No `dvc` invocation, no network, so the whole
module is testable offline.

```python
def archive_path(workspace_root: Path, slug: str) -> Path   # workspace_root/_archives/<slug>.zip
def pointer_path(workspace_root: Path, slug: str) -> Path   # the same, + ".dvc"
def workspace_size(src_dir: Path) -> int                    # bytes, for the preflight
def ensure_space(src_dir: Path, dest_dir: Path, headroom: float = 1.05) -> None
def build_zip(src_dir: Path, dest_zip: Path) -> None
def verify_zip(zip_path: Path) -> int                       # returns member count
def extract_zip(zip_path: Path, dest: Path, *, force: bool = False) -> None
def link_to_cache(zip_path: Path, pointer: Path, cache_root: Path) -> bool
```

### `build_zip`

- `zipfile.ZipFile(dest_zip, "w", ZIP_STORED, allowZip64=True)`.
- Sorted walk, so the archive is byte-deterministic for an unchanged tree.
- Arcnames relative to `src_dir`, so extraction reconstitutes the run
  directory contents without a leading slug component.
- Skips only cache patterns mirrored from `.dvcignore`: `__pycache__/`,
  `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `.venv/`.
  **`logs/` and `*.log` are kept** — AGENTS.md rule 2 makes agent prompt and
  transcript files in `logs/` first-class run artifacts, unlike the
  `.dvcignore` entry that exists to keep build noise out of the cache.
- **Symlinks cause a refusal.** ZIP has no portable symlink representation,
  and silently dereferencing one that points into a data mount could
  multiply a run by orders of magnitude. `build_zip` collects every symlink
  under `src_dir` and raises with the list.
- Writes to a sibling `<slug>.zip.partial` and renames onto `dest_zip` only on
  success; the partial file is removed if the build raises. An interrupted
  build therefore leaves neither a plausible-looking archive nor debris.

### `extract_zip`

- Refuses when `dest` exists and is non-empty, unless `force=True`. The
  downloaded archive is left in place either way, so nothing is lost.
- Zip-slip guard: every member name is resolved against the destination and
  rejected if it escapes (`../`, absolute paths, drive-relative names).
- Extracts into a sibling temporary directory, then renames into place, so a
  failure mid-extraction never leaves a half-populated `workspace/<slug>`.
  With `force=True` the pre-existing directory is moved aside and removed
  only after the rename succeeds.

### `verify_zip`

`ZipFile.testzip()` plus a member count, run after pull and before
extraction. A truncated download fails here rather than half-way through
writing files.

---

## 3. Changes — `scripts/dvc_sync.py`

### `cmd_push`, archive mode, in order

1. **Disk preflight.** `shutil.disk_usage` free bytes must be at least
   `workspace_size(ws_dir) * 1.05`. On failure, abort that slug printing both
   figures (`needs 254.3G, 99.7G free`) and continue to the next slug.
2. `build_zip` → `workspace/_archives/<slug>.zip`
3. `dvc add --to-remote workspace/_archives/<slug>.zip` — uploads directly to
   S3, creating `workspace/_archives/<slug>.zip.dvc` and no `.dvc/cache`
   object. No `-r` is passed, so DVC uses `core.remote` from `.dvc/config`
   (`s3remote`); a `--remote NAME` flag appends `-r NAME` when an explicit
   remote is wanted.
4. Delete the local zip. `--keep-zip` suppresses this, which is what you want
   after a partially failed upload.
5. If `workspace/<slug>.dvc` exists, `dvc remove` it (drops the pointer and
   its generated `.gitignore` entry; leaves cache and S3 data alone).
6. Print the exact `git add` / `git commit` invocation for the new pointer and
   the removed one. **The script never runs git.**

Directory mode is unchanged.

### `cmd_pull`, archive mode, in order

1. `dvc pull workspace/_archives/<slug>.zip.dvc`
2. `verify_zip`
3. `extract_zip` into `workspace/<slug>`, refusing a non-empty directory
   unless `--force` was passed
4. `link_to_cache`: replace the pulled zip with a hardlink to its own cache
   object (`.dvc/cache/files/md5/<first 2 hex>/<remaining hex>`, hash read
   from the pointer's `outs[0].md5`). Skipped — leaving the copy in place and
   saying so — when the object is missing, the sizes differ, or `os.link`
   fails. The linked zip inherits the cache object's read-only mode.
5. Keep the zip

`--remote NAME` applies to directory-mode push and pull as well, so one flag
never silently targets two different remotes in a mixed batch.

### `cmd_status`

Three states per workspace instead of two:

```
 - workspace/<slug>    [ARCHIVE]      zip: present | not downloaded
 - workspace/<slug>    [TRACKED in DVC]
 - workspace/<slug>    [LOCAL ONLY - NOT TRACKED]
```

### `find_workspaces`

Excludes `_archives` so `--all` never treats the archive directory as a run
slug. `resolve_slugs` rejects `_archives` as an explicit argument for the
same reason, accepts a slug whose only trace is its archive pointer, and
`--all` is the union of local run directories and archived slugs — so
`pull --all` on a fresh clone finds archived runs. `push --all` skips an
archived slug with no local directory instead of failing.

### New flags

- `push`: `--archive`, `--no-archive`, `--keep-zip`, `--remote NAME`
- `pull`: `--force`, `--remote NAME`

---

## 4. Repo configuration

- **`.dvc/config`** — no change. The original design set repo-wide
  `cache.type = hardlink`. Amended 2026-09-16: with hardlink cache DVC makes
  every tracked output read-only, so directory-mode workspaces the research
  loop keeps writing to (`status.yml`, budget, `logs/`) would fail with
  `PermissionError` until `dvc unprotect`. The default (`reflink,copy`, which
  is copy on this ext4) stays; archives alone get deduplicated through
  `link_to_cache` (section 3). This relies on DVC 3's cache layout
  (`files/md5/xx/rest`, verified on this machine) and degrades to a plain copy
  if that layout is absent.

- **`config/defaults.yml`** — add `archive: false` (section 1).

- **`docs/DVC_STORAGE.md`** — new section covering archive mode: when to opt
  in, the commands, the disk requirement, and the dedup trade-off.

- **`.gitignore`** — amended 2026-09-16 after verification. `workspace/*`
  ignores the `_archives/` directory itself, and git never descends into an
  ignored directory, so `!workspace/**/*.dvc` could not re-include the
  pointer. Added after `!workspace/**/.gitignore`:

  ```
  !workspace/_archives/
  workspace/_archives/*
  !workspace/_archives/*.dvc
  ```

  Without this, an archive push staged deletion of the old
  `workspace/<slug>.dvc` while the new pointer stayed ignored.
- **`.dvcignore`** — no change; nothing in it hides `workspace/_archives/`.

### Adjacent issue, not part of this change

`.dvc/config` is currently **untracked** in git (`?? .dvc/config`). Archive
pointers are useless to anyone who clones the repo without a remote
definition. Committing it is a one-line fix and should be done, but it is
an independent decision and is called out here rather than folded in
silently.

---

## 5. Testing

New `tests/test_archive.py`, plus additions to `tests/test_dvc_sync.py`.
Offline, no `dvc` binary, no S3 — matching the existing fake-`run_cmd` style.

`scripts/sflib/archive.py`:

- build → extract round-trip reproduces the source tree exactly (names,
  contents, nesting)
- every archive member has `compress_type == ZIP_STORED`
- `__pycache__/`, `*.pyc` excluded; `logs/` and `*.log` **included**
- extract refuses a non-empty destination; `force=True` replaces it
- a crafted member named `../evil` is rejected and writes nothing outside
  the destination
- a symlink under the source raises, naming the symlink
- `verify_zip` fails on a truncated file
- a build that raises part-way leaves neither the `.zip` nor the `.partial`
- `link_to_cache` hardlinks to a fake `files/md5/xx/rest` object (same inode
  afterwards) and returns `False` without touching the zip when the object is
  missing, the size differs, or the pointer hash is a `.dir` hash

`scripts/dvc_sync.py`:

- mode resolution table: pointer present / `--archive` / `--no-archive` /
  `config.yml` / default, for both push and pull
- disk preflight with `shutil.disk_usage` monkeypatched — refuses below the
  threshold, proceeds above it, and the message contains both numbers
- exact argv asserted for archive push
  (`dvc add --to-remote …` with no `-r`, then `dvc remove …`; and `-r alt`
  present when `--remote alt` is passed) and archive
  pull (`dvc pull …`)
- `find_workspaces` skips `_archives`; `--all` includes pointer-only slugs
- directory-mode push and pull argv unchanged from today

---

## 6. Out of scope

- Streaming or split archives (would lift the disk ceiling, but requires
  bypassing `dvc add` and rules out `.zip`, which needs seekable output)
- `dvc gc --cloud` or any other S3 deletion
- Compression tuning beyond the `ZIP_STORED` decision
- Any change to `scripts/remote/` — metacentrum transport is untouched
- Archiving `vendors/` content

---

## 7. Known limitation

With 100G free, `2026-07-segsnake-thalamus-mrtrix-validation` (254G) and
`2026-09-full-brain-sampling-smoke` (52G) cannot be archived. The preflight
will refuse them by name with the required and available figures. Archive
mode is usable today on every workspace up to roughly 95G; the two large runs
stay in directory mode until disk is freed.
