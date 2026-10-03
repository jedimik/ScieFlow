# Manuscript provenance — retroactive spec and state of play

**Written:** 2026-10-03, retroactively, from the implementation as it stands rather than from intent.
**Status:** programme item D is feature-complete and unmerged. Verified, not remembered: suite
`1590 passed / 5 skipped / 6 deselected` at `5302829` on `dev/d-manuscript-provenance`.
**Supersedes nothing.** The design record is
[`2026-09-27-manuscript-provenance-design.md`](2026-09-27-manuscript-provenance-design.md),
which stays a dated record of what was *designed*; this document records what was *built*.

## Problem statement

A run's manuscript had no history. `workspace/*` is gitignored, so nothing under
`workspace/<slug>/` was ever seen by this project's git, and the only sync that existed was DVC's
— one zip per run, which preserves bytes and discards history. You could not ask what the merging
agent changed between rounds, or how far the current paper had drifted from what one agent
originally proposed. Programme item C made this sharper by producing exactly the artifact that
most wants a history (`manuscript/curation/rounds/<n>/<section>.tex`) in the one place git never
looks.

---

## 1. Current state

### Programme position

| Item | What it is | State |
|---|---|---|
| M1, M2a, M2b, A1 | foundations, web control | merged to `main` |
| C | the draft workbench | merged to `main` (`a7d523f` PR #5, `9b1c497` PR #6) |
| **D** | **manuscript provenance** | **complete on `dev/d-manuscript-provenance`, unmerged** |
| B | the run explorer | not started |
| E | container sandbox backend (macOS, native Windows) | not started |

The branch forked from `main` at `9b1c497`, tree clean. A commit count is deliberately not quoted
here: every later commit invalidates it, as two revisions of this line already demonstrated.

### What item D ships

All six planned tasks are complete, each reviewed, with every review round closed.

**`src/scieflow/core/provenance.py`** (988 lines) — the whole git layer. A bare repo per run at
`workspace/<slug>/provenance.git`, populated entirely through plumbing.

Public surface:

| Function | Contract |
|---|---|
| `available() -> bool` | is `git` on the host at all |
| `repo_path(ws) -> Path` | `ws / "provenance.git"` |
| `ensure_repo(ws) -> Path` | creates, or discards and rebuilds a corrupt repo; creates the root commit once |
| `ref_safe(name) -> bool` | `drafts.check_name` → leading-`-` refusal → `git check-ref-format` |
| `agents(ws) -> list[str]` | agent names discovered from the run's own artifacts, never a fixed list |
| `sync(ws) -> dict` | `{"commits": {ref: sha}, "agents": [...], "skipped": {"agents": [...], "artifacts": [...]}}` |
| `points(ws) -> list[dict]` | each `{"ref", "label", "kind", "commit", "at"}`, plus `"parent"` on the one refining kind |
| `diff(ws, a, b) -> dict` | `{"text", "truncated", "a", "b"}` |
| `ProvenanceError(ValueError)` | the module's one error type |

Constants that are part of the contract: `REPO_DIR = "provenance.git"`,
`EMPTY_ROOT_REF = "refs/heads/_root"`, `DIFF_LIMIT = 200_000`.

**The repo layout.** One rule: `main` carries what the run produced collectively,
`draft/<agent>` carries what that agent produced alone. Round-structured sources go in round
directories; once-only sources sit at the branch root.

```
main                          draft/<agent>
  outline.md                    findings.json
  merge_N/                      gaps.json
    sections/*.tex              sections/*.tex
    curation.yml                reviews/<agent>-on-<other>.json
  review_N/                     review_N/
    review.md                     <reviewer>-on-<author>.json
    response.md                   response-<author>.md
```

**`src/scieflow/core/service.py`** (+91) — `manuscript_history(project, slug) -> dict` and
`manuscript_diff(project, slug, a, b) -> dict`, plus a guarded `provenance.sync` after a
successful merge round.

**`src/scieflow/core/sandbox.py`** — the mask: an agent dispatch's run grant gets a `--tmpfs` over
`provenance.git` (`masks_for`), so the agent cannot write the object store.

**`src/scieflow/core/jobs.py`** — `jobs.start` passes `mask=` through to `sandbox.wrap`.

**`scripts/sflib/archive.py`** — `provenance.git` joins the archive's rebuildable skip set.

**`src/scieflow/core/events.py`** (+2 members) — `provenance.synced` and `provenance.skipped`
join the closed `TYPES` frozenset.

**`src/scieflow/web/`** — `pages.py` (+42) gains `diff_a` / `diff_b` query parameters on the
existing GET route and a `_manuscript_diff` helper; `templates/drafts.html` (+36) gains the
history panel and the two-select diff form. No new route, no POST, so no `MUTATING_PATHS` entry,
no `SAMPLES` case, no CSRF surface.

**Documentation** — `docs/web.md` (+140) carries the reader's guide; `docs/runs.md` (+7)
cross-references it.

**Tests** — `tests/core/test_provenance.py` (958), `tests/web/test_provenance_panel.py` (160),
`tests/core/test_service.py` (+69), `tests/core/test_merge_round.py` (+31). All against real git
in `tmp_path`, no mocks, nothing skipped for a missing binary.

---

## 2. Discovered constraints

Decisions settled during implementation and review rather than stated up front. Each is now
load-bearing: changing it breaks something a test pins.

### Process isolation and the git environment

- **The environment is constructed, never inherited.** `_env(parent=None)` never copies
  `os.environ`; it passes through only `PATH` and `LANG`. A test pins the exact key set, so
  replacing it with `dict(os.environ)` fails.
- **`HOME` is deliberately omitted**, which means git cannot read `~/.gitconfig`, which in turn
  means `commit-tree` fails with "empty ident name" unless the four identity variables are set
  explicitly. The identity is a fixed `ScieFlow`, correct for a derived repo — it would need
  revisiting only if these commits ever had to carry a real author.
- **`GIT_CONFIG_NOSYSTEM=1` is a security control, not hygiene.** Proven mechanism: a
  system-wide `core.hooksPath` makes a plain `update-ref` execute an arbitrary
  reference-transaction hook, twice, with the ref and SHA on stdin. Every task reaches
  `_update_ref`. (A reviewer's separate claim that `commit.gpgsign=true` breaks `commit-tree`
  was tested on this host and is **false** — `commit-tree` signs only when passed `-S`, which
  `_commit` never does. The code comment records what reproduced and what did not.)
- **`_git` is the only subprocess path**, with `encoding="utf-8"` pinned. Even
  `git init --bare` is routed through it via `--git-dir`, verified byte-identical to
  `git init --bare <path>`, so the environment is built in exactly one place.

### Determinism

- **The root commit is pinned to a fixed epoch** (`@0 +0000`) and asserted against the literal
  SHA `90f8e5daab6c2ed97a857cd6f9a6fe65c1158122`. Real dates stay for every other commit. The
  literal exists because a same-wall-clock-second SHA collision made three separate
  "idempotency" assertions vacuously true — pinning the literal kills the class, not the
  instance.
- **`HEAD` points at `refs/heads/master` after init**, deterministically, because omitting
  `HOME` also ignores the host's `init.defaultBranch`. Nothing reads `HEAD`. *Carry:* any future
  code that assumes `HEAD` tracks `main` must set it with `symbolic-ref`.

### Sync contract (rulings carried over from the deleted ledger)

- **`main` always exists after the first sync, even when empty**, created from the root's empty
  tree. A draft-only run would otherwise have no `main` ref and `git diff refs/heads/main ...`
  would raise in every dependent task. One special case in one place, and an empty `main` is
  honest: the run has no collective output yet.
- **A *digit-shaped but non-canonical* round directory is skipped and reported.** Only names equal
  to `str(int(name))` are accepted, so `1` is a round and `01` is reported as skipped: both would
  otherwise collapse onto one target with the winner decided by scandir order, so committed content
  could flip between syncs and produce a spurious commit from scan-order noise alone. A name that is
  not digit-shaped at all (`round-foo`) is skipped **silently and deliberately** — it is not a round
  of anything. The rule applies to all three round trees: `manuscript/curation/rounds/<n>`,
  `review/round-*` and `review/draft-round-*`.
- **One exception type crosses the module boundary: `ProvenanceError`.** `manuscript_history` is
  specified never to raise for an ordinary state and catches only that type, so the unguarded
  `ws.resolve()` (a bare `RuntimeError` on an odd symlink above the workspace) was fixed rather
  than deferred. Any new path that can raise something else to a caller breaks the contract. Inside
  `sync`, the deliberate `except Exception` guards around event emission are not an exception to
  this: they swallow a best-effort side effect so it cannot fail a round, and nothing escapes.
- **Blobs are hashed by path, so encoding does not matter.** `_blob_file` uses
  `hash-object --no-filters`, byte-exact, and never inspects content — which is also why a binary
  payload under a text extension is committed (see "No binary *source path*" below). A latin-1 or UTF-16 `.tex` is ordinary in LaTeX work;
  the earlier decode-based projection dropped such files from history silently.

### Injection boundaries

- **`mktree -z`**, NUL-delimited, plus a NUL-in-name refusal. The unescaped form admitted proven
  entry injection: a name containing a newline yielded two tree entries, the second pointing at a
  blob the caller never asked for. This is the same shape as the provenance-heading injection
  that cost three fix rounds in item C.
- **Symlink containment.** An agent-created symlink copied host content into a committed repo —
  reproduced: `findings/claude.json -> /outside/id_rsa` committed a private key. Closed by
  `_blob_file` using `hash-object -w --no-filters -- <path>` and by routing directory discovery
  through `drafts`' containment helpers. `_blob(repo, data)` is kept for in-memory content.
- **`diff`'s `a` and `b` are whitelisted by whole-string equality against `points()`** before any
  subprocess runs — not prefix-matched, not sanitised, not merely checked for `..`. An
  unvalidated ref could be *argument*-shaped (`--output=/tmp/x`) rather than merely path-shaped,
  which no containment check catches. A syntactically valid ref that simply is not a point
  (`refs/heads/main`) is refused identically.
- **`ref_safe` is the only thing between an agent-chosen directory name and a ref position.**
  `check-ref-format` accepts both `--all` and `-`, confirmed independently, so the leading-dash
  check is not redundant. `/` is blocked upstream by `drafts.check_name`, so no agent name can
  escape `refs/heads/draft/`.

### Properties that fall out of the design

- **No binary *source path* is projected, but a binary *payload* can be.** Every source in the
  layout is matched by an explicit text pattern (`*.tex`, `*.json`, named `.md`/`.yml`), so a
  `figure.pdf` is never globbed and never enters. The patterns constrain the *extension* only:
  `_blob_file` hashes by path and never inspects content, so a `fig.tex` holding PNG bytes **is**
  committed, byte-exact, and **does** reach `diff()`. That is deliberate — hashing by path is what
  stops a latin-1 or UTF-16 `.tex` being silently dropped from history
  (`test_a_non_utf8_source_is_committed_byte_exact` pins it). Reaching `diff()` is safe without a
  content check: git itself emits `Binary files … differ` for such a blob, and `diff` runs with
  `errors="replace"` for non-UTF-8 text.
- **Rounds are preserved as *paths*, not commit boundaries.** Because history lives in the tree
  structure (`main:merge_2`) rather than the commit graph, a missed or coalesced sync costs a
  commit boundary and never content. This is what makes best-effort committing the right trade
  rather than a compromise.
- **The repo is derived, never authoritative.** Disk is the truth; deleting `provenance.git`
  costs history only, and the next read rebuilds what the workspace still supports.
- **A sync failure never fails a round.** The round happened and cost budget. The call is guarded
  and records a closed-vocabulary event.

### Three facts the design got wrong, now corrected in place

- **`curation.yml` attaches only to the *highest* round at sync time.** There is one live
  `manuscript/curation/document.yml`, so a per-round snapshot is impossible. A person hand-running
  `git diff main:merge_1 main:merge_2` sees `curation.yml` as an *addition* on the `merge_2` side,
  never as a modification. The design diagram implied otherwise and now carries a dated
  correction note.
- **`review_N` is branch-specific.** On `main` it is `paper-review`'s review-and-response cycle.
  On `draft/<agent>` it is **`paper-draft`**'s adversarial cross-review. The design attributed
  both to `paper-review`; that error re-propagated verbatim into a task brief before being caught,
  which is why the spec carries a correction note rather than silent repair.
- **The workbench diff picker offers *every* point, unfiltered.** Both `<select>` elements iterate
  the full `points()` list, so a mismatched-depth comparison is reachable from the UI and produces
  a path-disjoint diff — every line deleted, every line added, no line-level comparison. The docs
  now say so plainly and teach the `deleted file` / `new file` pairing as the tell. Filtering was
  *deliberately not added*: it would have landed an unreviewed UI change inside a documentation
  task, and a filter might forbid legitimate comparisons.

### Boundaries inherited from the repo, reconfirmed here

- `git` is a **new optional runtime dependency**, degrading through `available()` exactly as
  `latexmk` already does. Verified present on this host at 2.43.0, so no provenance test skips.
- `-shell-escape` is **never** passed to `latexmk` and `-norc` **always** is, because agent-written
  `.tex` is untrusted. The compile environment is constructed, with `shell_escape=f`,
  `openout_any=p`, `openin_any=p` set positively — `pdflatex` honours `shell_escape` from the
  *environment*, so the flag's absence from argv is not sufficient.
- Every web handler is `def`, enforced by `tests/web/test_async_routes.py`. Jinja autoescape comes
  from Starlette's `Jinja2Templates`; **no `StrictUndefined` is configured anywhere** in
  `src/scieflow/`, so loud failures come from `Undefined.__getattr__`.
- **AGENTS.md rule 16:** `chats push`/`pull` must never begin on an agent's initiative.
  **Rule 15:** ask before uploading ≥1 GB, >~500 new files, >~5 GB total, or a never-synced run.
  Nothing in item D uploads anything, so rule 15 does not bind it. Both rules are now additionally
  encoded as harness `ask` rules in `.claude/settings.json` — `ask` rather than `deny`, because a
  hard deny would break `/workspace-sync`, the user-invoked skill that legitimately runs them.
- Model choice is the user's: provider, model and effort are picked per role, and ScieFlow never
  routes or downgrades models itself.

---

## 3. Remaining scope

### Decisions taken (user, 2026-10-03, grilling round 1)

1. **`provenance.git` is excluded from workspace archives.** It is derived by design, so it
   belongs in the archive's rebuildable skip set. Leaving it in let a derived artifact silently
   grow a push's file count toward the rule-15 consent threshold; a pulled run regenerates its
   history on the next `sync`. The same change has a destructive local side: `extract_zip(force=True)`
   renames the old run directory aside and `rmtree`s it, so `dvc_sync.py pull --force` now **deletes**
   a local `provenance.git` the archive no longer carries. Content is rebuilt by the next `sync`;
   commit boundaries are not.
2. **The temporary handoff scaffolding is deleted before merge.** The rulings worth keeping are
   distilled into this document; the duplicate process record is not kept.
3. **Item E is live scope, not dormant.** The deferral in the A1 design ("when those machines are
   needed") is withdrawn: ScieFlow is intended for other users on other operating systems, so a
   macOS and native-Windows backend is required work rather than contingent work. It still needs
   its own design spec before any implementation, because it is a redesign of the confinement
   model and not a driver swap — see the constraint note on identity-mapped paths above.
4. **Item B is split.** The local run explorer and DVC-archived run browsing become separate
   items with separate specs. They share a page and nothing else, and bundling them made the
   cheap half wait on the expensive half's consent and failure-mode design.

5. **`provenance.git` is subtracted from the sandbox writable set.** Today every agent dispatch can
   write to the object store because the grant is the whole run directory, and D's design defended
   this with a statement about an agent's *motive* ("no reason to touch it") rather than its
   *capability*. The blast radius is small — the repo is derived and a corrupt one is rebuilt — but
   "rebuilt" means history is silently lost rather than detected, which is the one thing the
   feature exists to provide.

   **Measured on this host, and it decides the mechanism.** A fresh run dispatches agents long
   before `provenance.git` exists, because `ensure_repo` is reached only after a merge round or on
   a workbench load. A `--ro-bind` of a missing source is fatal (`bwrap: Can't find source path`),
   so the exclusion cannot be a read-only bind. A `--tmpfs` over the same path works whether or not
   it exists, and writes inside it are discarded. It leaves an empty `provenance.git` on the host,
   because the mountpoint lands inside the writable bind mount — and `ensure_repo` tolerates
   exactly that: it probes, finds no bare repo, discards and re-inits, after which `sync` commits
   `refs/heads/main` normally. Verified end to end.

### To land item D

1. ~~Exclude `provenance.git` from workspace archives (decision 1).~~ **Done.**
2. **Done.** Remove the temporary handoff scaffolding (decision 2). It is **28 files and 7,733 lines** —
   two thirds of the branch's 11,527 insertions, committed by `a8ac710`, whose own message calls
   it temporary. Production code, tests and documentation are the other ~3,794 lines. The live
   ledger at `.superpowers/sdd/` is correctly gitignored and untracked.
3. ~~Narrow the sandbox writable grant~~ — **done** (decision 5; `provenance.git` masked per agent dispatch).
4. **Final whole-branch review** over `git merge-base main HEAD`..`HEAD`, on the most capable
   model, pointed explicitly at the ledger's deferred minors. Budget one fix wave plus one scoped
   re-review.
5. **Finish the branch** via `superpowers:finishing-a-development-branch`, presenting the
   three-option menu.

### Carried follow-ups — recorded, deliberately not built

| Follow-up | Why it was left |
|---|---|
| Filter the diff picker by depth compatibility | a UI change has no place in a docs task; may forbid legitimate comparisons |
| `points()` costs one `ls-tree` per round | linear in rounds, not in content; no run is near a problem |
| `DIFF_LIMIT` bounds the response, not peak memory | the whole diff is still produced before truncation |
| `HEAD` tracks `master`, not `main` | nothing reads `HEAD`; use `symbolic-ref` if that changes |
| Five parked item-C minors | recorded in auto-memory at `c-draft-workbench-parked-followups` |
| Older `main` follow-ups | inert Start-wizard `workflow` key; dashboard N+1; CSP nonces; envbuild apptainer/singularity |

### Then the programme's remainder

> **The items below are not the whole programme.** They are the remainder of the A–E table from the
> A1 design, which only ever scoped the research-run surface. Items F–K — agent configuration,
> workspace health, the experiments module, literature research, news, and an in-browser CLI — are
> recorded in [Web/CLI coverage](2026-10-03-web-cli-coverage.md), which is the authoritative scope
> record for "work with ScieFlow as a web application".

Each needs a design spec before implementation; none is specified yet.

- **B — the local run explorer.** Reads `workspace/<slug>/` directly. The known risk is the
  existing dashboard N+1, which a list of many runs amplifies.
- **B2 — DVC-archived run browsing** (split out of B by decision 4). A remote round-trip: whether
  to pull-and-extract or stream entries from the zip, what a missing remote does, and how an
  archive written by an older ScieFlow — whose `run.yml` may lack keys the reader expects —
  degrades rather than crashes. Because of decision 1, an archive carries no `provenance.git`, so
  an archived run has no manuscript history until it is pulled and re-synced; the explorer must
  say so rather than appear to show an empty history.
- **E — container sandbox backend** for macOS and native Windows (decision 3). Live scope. The
  open architectural questions, all downstream of choosing a runtime: Docker Desktop licensing
  versus rootless podman versus the Apptainer the experiments module already uses; whether
  `filelock` semantics survive a bind mount across a VM boundary, since `store.update_yaml` is
  read-modify-write and a lost update corrupts run state; UID mapping and root-owned files left in
  granted directories; orphaned containers versus `--die-with-parent`; and whether `verify()`'s
  negative control still proves anything when an ungranted path simply does not exist inside the
  container rather than being unwritable. This is the last item standing between the current state
  and "replaces my need to do everything in the CLI interface".

## Out of scope, permanently

Any remote for the provenance repo: no push, no fetch, no Overleaf, no GitHub, no collaboration —
provenance was chosen over sharing, and each of those is a separate feature with its own consent
questions. Rewriting history. A CLI surface (`manuscript log`/`diff`) — the browser is the chosen
reading surface. Replacing DVC: the archive keeps carrying the bytes, this carries the history.
