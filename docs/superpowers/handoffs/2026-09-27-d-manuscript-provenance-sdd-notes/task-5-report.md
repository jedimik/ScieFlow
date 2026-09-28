# Task 5 report: the history and diff panels

Commit: `d83d38d` — "feat(web): read the manuscript's history and diff it on the workbench"
Follow-up commit: `49b659e` — "fix(tests): tighten three provenance-panel assertions that passed for the wrong reason" (see addendum at the bottom of this report)

## What was implemented

- `src/scieflow/web/pages.py`:
  - `_manuscript_diff(project, slug, a, b)`: returns `None` when either `a`
    or `b` is empty, otherwise calls `service.manuscript_diff` and catches
    `ServiceError`, returning `{"error": str(exc)}` — so a hostile or stale
    ref renders as a message, never a 500.
  - `drafts_page` gained `diff_a: str = ""` and `diff_b: str = ""` as plain
    query parameters (still `def`, never `async def`). The context now
    carries `"provenance": service.manuscript_history(project, slug)`,
    `"diff": _manuscript_diff(...)`, and `"diff_a"`/`"diff_b"` (so the
    template can mark the current selection), all placed *before* `**view`
    is spread, exactly as the brief's snippet shows.
  - No new route, no addition to `MUTATING_PATHS`/`SAMPLES`, no CSRF field
    on either panel — both are read-only and driven by the existing `GET`.
- `src/scieflow/web/templates/drafts.html`: a new "Manuscript history"
  section between "Merged rounds" and "Curation":
  - When `provenance.available` is false or `provenance.points` is empty,
    renders `provenance.reason` as plain text and nothing else.
  - Otherwise, an unordered list of rows filtered to
    `not point.get("parent")` (one row per round/review/outline/draft, not
    per sub-point), each showing `point.ref`, `point.label` and
    `point.at[:19]`.
  - A GET form (`method="get"`, no CSRF field) with two `<select
    name="diff_a">` / `<select name="diff_b">` populated from *all*
    `provenance.points` (sub-points included, so a draft's `sections` can be
    compared against a round's `merge_N/sections` at matching tree depth),
    each option preselecting against the current `diff_a`/`diff_b`.
  - A diff panel, rendered whenever `diff` is set (independent of whether
    the history list above is showing rows or the "no history" message):
    `diff.error` as a failure message, else `diff.text` in a bare `<pre>`
    with `{{ }}` (never `|safe`), with a truncation note when
    `diff.truncated`.
- `tests/web/test_provenance_panel.py`: created, byte-for-byte the test file
  given in the brief.

## Decisions and why

- **Context key is `provenance`, not `history`.** Followed the brief's
  single most emphasized instruction exactly. Also strengthened
  `drafts_page`'s docstring with an explicit paragraph naming both keys and
  why they're kept apart, mirroring the existing `agents`/`conversational`
  paragraph already there for the same class of defect.
- **Diff panel is unconditional on `diff` being set**, not nested inside the
  "provenance available" branch. This matters for the hostile-ref test:
  if git were unavailable, or if the ref pair were simply wrong, the diff
  panel still needs to show `diff.error` even though the history list above
  it might be showing the "no history yet" message instead of rows. Nesting
  the diff panel inside the rows branch would have silently swallowed error
  messages in that combination.
- **All `provenance.points` (including `parent`-bearing sub-points) go into
  the `<select>` options**, while only `parent is None` points become visible
  rows — exactly the split the brief specifies, so a draft can be diffed
  against a round's `sections` sub-point despite neither being one of the
  "row" points.
- **No new helper module** — `_manuscript_diff` lives in `pages.py` next to
  `_as_int`/`_back`, following the file's existing pattern of small private
  helpers colocated with the one route that uses them.

## Ambiguities / things the brief didn't fully anticipate

1. **Several of the brief's own test assertions pass vacuously before (and
   independent of) the diff implementation**, because of an interaction
   between the `merged` fixture and the pre-existing "Merged rounds" panel:
   - `test_a_diff_is_rendered_when_two_points_are_given` asserts `"merged
     v1"` and `"merged v2"` appear in the page. Both round 1's and round 2's
     raw text are *already* rendered unconditionally by the existing
     "Merged rounds" grid (each round's own content, not a diff), so this
     assertion is satisfied whether or not the diff panel does anything at
     all. I confirmed this directly: with the entire diff-rendering block
     replaced by `{% if False %}`, all three diff-related tests
     (`test_a_diff_is_rendered_when_two_points_are_given`,
     `test_a_hostile_diff_ref_comes_back_as_a_message_not_a_500`, and even
     `test_diff_text_is_escaped_not_raw_html`) still pass.
   - `test_a_hostile_diff_ref_comes_back_as_a_message_not_a_500` asserts
     `"not a point"` appears in the page. This string is also a substring of
     pre-existing JS-comment prose already in `drafts.html` ("a passage is a
     quotation, **not a pointe**r: we post the TEXT…") — `"not a point"` is
     a substring of `"not a pointer"`. So this assertion, too, is satisfiable
     without the diff feature ever running.
   - `test_diff_text_is_escaped_not_raw_html`'s second assertion
     (`"&lt;script&gt;" in page.text`) is *also* satisfiable independently:
     the poisoned round-2 content is written to the same `results.tex` the
     "Merged rounds" panel already renders (auto-escaped, since Jinja
     autoescapes `{{ text }}` there too), so `&lt;script&gt;` appears on the
     page via that unrelated, pre-existing panel regardless of whether the
     diff panel renders anything.
   - What *is* genuinely load-bearing in that last test, and what I verified
     specifically per the brief's instruction, is the **negative** assertion:
     `"<script>alert" not in page.text`. I falsified this by changing
     `{{ diff.text }}` to `{{ diff.text | safe }}` in the template, re-ran
     just that test, and confirmed it failed (raw `<script>alert(...)`
     appeared, caught by the assertion) — then restored the `|safe` and
     re-ran to confirm green again. So the escaping test does catch the one
     regression the brief calls out (an XSS-shaped `|safe`), even though its
     positive/presence assertions don't, on their own, prove the diff panel
     ran.
   - I did not alter the test file (the brief says to use it verbatim), but
     I independently confirmed the diff panel is a real, working feature —
     not an accidental no-op riding on the coincidences above — with a
     scratch manual check (not committed) that hit
     `/runs/r1/drafts?diff_a=main:merge_1&diff_b=main:merge_2` and inspected
     the `<pre class="diff">…</pre>` block directly: it contains a genuine
     `git diff --no-color`-shaped unified diff (`diff --git a/sections/…`,
     `--- a/…`, `+++ b/…`, `-merged v1`, `+merged v2`), and the same block
     is entirely absent (`<pre class="diff">` not in the page) when no
     `diff_a`/`diff_b` are given. I'm flagging this as a finding rather than
     changing anything, since the brief was explicit about using its test
     file exactly as given.
2. Everything else in the brief matched the codebase as read: `service.py`'s
   `manuscript_history`/`manuscript_diff` signatures, error behaviour, and
   the `parent`-key convention on sub-points were all exactly as documented
   in Task 4's code, and the drafts-page collision hazard was exactly as
   described.

## Test results

- `uv run pytest tests/web/test_provenance_panel.py -v` — before
  implementation: 4 of 7 failed as expected (the two panels didn't exist).
  The other 3 passed immediately, for the fixture-overlap reasons in
  section "Ambiguities" above, not because the feature already worked — I
  did not treat this as a red flag warranting a brief deviation, since the
  brief pins this exact test file and the codebase (not my new code) is the
  source of the overlap.
- After implementation: `uv run pytest tests/web/test_provenance_panel.py -v`
  — **7 passed**.
- `uv run pytest tests/web -q` — **257 passed**.
- `uv run pytest tests/web/test_read_only.py tests/web/test_mutations.py tests/web/test_async_routes.py tests/web/test_provenance_panel.py -q` — **64 passed** (route inventory unchanged: no new mutating route, no new async handler).
- `uv run pytest -q` (whole suite, twice, before and after the falsification
  round-trips below) — **1575 passed, 5 skipped, 6 deselected, 2 warnings**
  both times (baseline was 1568 passed, 5 skipped, same 2 warnings; 1575 −
  1568 = 7, exactly the new tests).

## Falsification transcripts

All done by editing in place, running, confirming the specific failure, then
restoring — no worktree used (per the instruction, since scieflow is
editable-installed in the shared venv).

1. **Escaping (`|safe`) — the brief's explicitly load-bearing check.**
   Changed `<pre class="diff">{{ diff.text }}</pre>` to
   `<pre class="diff">{{ diff.text | safe }}</pre>`, ran
   `test_diff_text_is_escaped_not_raw_html` alone: **FAILED** (raw
   `<script>alert(...)` present in the page — `"<script>alert" not in
   page.text"` assertion tripped). Restored the line, reran: **PASSED**.

2. **History-panel content.** Replaced the `{% if not provenance.available
   or not provenance.points %}` branch condition with `{% if True %}` (so
   the rows/select-form branch could never render), ran the whole
   `test_provenance_panel.py` file: `test_the_panel_lists_the_rounds`,
   `test_a_run_with_no_history_says_so`, `test_the_panel_says_so_when_git_is_missing`,
   and `test_the_curation_version_panel_still_works` all **FAILED** as
   expected (they depend on the rows/select-form actually rendering); the
   three diff-content tests **still passed**, consistent with the overlap
   noted above. Restored the condition, reran the file: **7 passed**.

3. **The `history`/`provenance` key collision itself.** Reproduced the exact
   defect the brief warns about: changed the context dict to write
   `"history": service.manuscript_history(project, slug)` as a *second*
   `"history"` key (immediately after the real
   `"history": service.curation_history(...)`), leaving `provenance`
   undefined. Ran the test file: **all 7 tests failed**, every one with
   `jinja2.exceptions.UndefinedError: 'provenance' is undefined` — **not**
   because of a `StrictUndefined` setting (there is none anywhere in
   `src/scieflow/`; corrected in fix round 2, see the addendum below) but
   because the template reads *attributes* off `provenance`
   (`provenance.available`, `provenance.points`), and Jinja's plain, default
   `Undefined.__getattr__` already raises `UndefinedError` on attribute
   access even without `StrictUndefined` — only a bare `{{ provenance }}`
   reference would have rendered silently as empty. This confirmed the
   pinning test (and, incidentally, every other test on this page) genuinely
   catches this exact class of collision, not just the one test named for
   it. Restored the two separate keys, reran: **7 passed**.

4. **Diff panel entirely removed.** Wrapped the diff-rendering block in
   `{% if False %}` (leaving the history panel untouched). All 7 tests
   still **PASSED** — this is the fixture-overlap finding from section
   "Ambiguities" above, reproduced directly: none of the three
   diff-specific tests can, on their own, prove the diff panel ran. I then
   independently verified the real diff panel's behaviour with a
   non-committed manual script (see above) before restoring the block and
   confirming **7 passed** again.

After all four falsification round-trips, `git diff --stat` against the
final committed state showed exactly the intended two-file diff (no
leftover edits), and the full suite was rerun clean:
**1575 passed, 5 skipped, 2 warnings**.

## Self-review findings and what changed

- Initially placed the diff-rendering block inside the "else" (history
  available) branch while drafting the template by hand; moved it to be
  unconditional on `diff` alone before ever running tests, specifically to
  avoid swallowing `diff.error` when the run has no history but a stale
  `diff_a`/`diff_b` is still in the URL (e.g. after a repo rebuild wipes
  history but a bookmark still carries old refs).
- Verified the `<select>` options intentionally include sub-points
  (`parent` present) even though the visible row list filters them out —
  this was easy to get backwards (filtering both, or filtering neither) and
  is exactly the depth-matching mechanism the brief calls out
  (`draft:sections` vs `main:merge_N/sections`).
- Confirmed via the collision falsification (item 3 above) that a real
  `history`/`provenance` collision would be loud (an `UndefinedError`, not a
  silently wrong render) in this specific app — worth recording since the
  brief's framing ("silently shadow") describes the general hazard class,
  and the concrete failure mode here happens to be loud rather than silent
  once one of the two names goes fully missing (it would still be silent if
  both dict entries used the *same* key with two present-but-wrong values,
  which is the actual shape of the defect being guarded against — I did not
  reproduce that variant since it's harder to construct without editing the
  service layer, but the loud variant already proves the two keys are
  distinct in the final code). **Correction (fix round 2):** I originally
  attributed this loudness to a `StrictUndefined` Jinja setting; there is no
  such setting anywhere in `src/scieflow/`. The real mechanism is that
  Jinja's *default* `Undefined` class already raises `UndefinedError` from
  `__getattr__` on attribute access (confirmed directly:
  `jinja2.Undefined(name="provenance").available` raises
  `UndefinedError: 'provenance' is undefined`), and this template accesses
  `provenance.available`/`provenance.points` as attributes rather than
  referencing the bare variable. So the guard holds specifically because the
  template reads attributes off the key, not because of any environment
  configuration — if a future change referenced `provenance` as a bare
  value instead of via attribute access, this particular loud failure would
  not be there to catch a collision, with no config change needed to lose
  it.

## Concerns

- None blocking. The one thing worth the reviewer's attention is the
  "Ambiguities" section above: several of the brief's own test assertions
  are satisfiable by pre-existing, unrelated page content (the "Merged
  rounds" panel and an old JS comment), so `test_a_diff_is_rendered_when_two_points_are_given`
  and `test_a_hostile_diff_ref_comes_back_as_a_message_not_a_500` do not, by
  themselves, prove the diff feature works — only `test_diff_text_is_escaped_not_raw_html`'s
  negative assertion and my own uncommitted manual check do. I kept the test
  file exactly as specified rather than strengthening it, per the brief's
  "use it verbatim" instruction, but flagging it here in case the next task
  or a reviewer wants a tighter assertion (e.g. asserting on the literal
  `diff --git` line, which cannot come from anywhere else on the page).

## Files touched

- `/home/jedimik/Github/ScieFlow/src/scieflow/web/pages.py`
- `/home/jedimik/Github/ScieFlow/src/scieflow/web/templates/drafts.html`
- `/home/jedimik/Github/ScieFlow/tests/web/test_provenance_panel.py` (new)

## Addendum: fixing the three weak assertions (commit `49b659e`)

The coordinator's review confirmed the "Ambiguities" finding above was a
correctness defect in the brief's own test file, not merely an observation,
and asked for it to be fixed before review. All three fixes are to
`tests/web/test_provenance_panel.py`; no application code changed.

**1. `test_a_diff_is_rendered_when_two_points_are_given`.** Was asserting
`"merged v1"`/`"merged v2"` in `page.text` — satisfied by the pre-existing
"Merged rounds" panel regardless of the diff panel. Changed to assert on the
diff's own shape: `"-merged v1" in page.text and "+merged v2" in page.text`
(the unified-diff line prefixes) plus `"diff --git" in page.text` — output
only `git diff`'s own text, never a round pane, can produce.

**2. `test_a_hostile_diff_ref_comes_back_as_a_message_not_a_500`.** Named
precisely by the coordinator: `"not a point"` is a substring of the
unrelated, pre-existing phrase "a quotation, not a **pointe**r" already in
`drafts.html`'s own script comment (from item C's spec), so the assertion
passed on a page with no diff feature at all. Replaced with the exact,
whole refusal message `service.manuscript_diff` produces —
`provenance.diff`'s `f"not a point: {a!r}"` — HTML-escaped exactly as
Jinja/MarkupSafe renders it on this page (confirmed by direct inspection:
apostrophes become `&#39;`, giving `"not a point: &#39;--output=/tmp/pwned&#39;"`).
The test now builds this with `markupsafe.escape` rather than hardcoding the
entity, so it stays correct if the message text ever changes shape. The
"no file created" assertion (`not pathlib.Path("/tmp/pwned").exists()`) was
kept as-is.

**3. `test_diff_text_is_escaped_not_raw_html`.** The negative assertion
(`"<script>alert" not in page.text`) was already load-bearing (falsified
under `|safe` in the original round). The positive assertion
(`"&lt;script&gt;" in page.text`) was not: the poisoned round-2 content is
also rendered, auto-escaped, by the pre-existing "Merged rounds" panel, so
it passed even with the diff-rendering block fully disabled. Scoped the
positive (and, redundantly but harmlessly, the negative) check to the diff
panel's own `<pre class="diff">...</pre>` block, located via
`page.text.index(...)`, so the assertion can only be satisfied by the diff
panel itself having rendered and escaped the text.

**New test:** `test_no_diff_panel_without_diff_params` — asserts
`'<pre class="diff">' not in page.text` on a plain `GET
/runs/r1/drafts` with no `diff_a`/`diff_b`. This is the counterpart the
coordinator asked for: without it, nothing pinned that the `{% if False %}`
probe used below is actually observing a real state change (a panel that
is *always* absent would make the probe meaningless).

### Verification (the coordinator's requested probe, run again on the fixed tests)

Disabled the diff-rendering block in `drafts.html` by changing
`{% if diff %}` to `{% if False %}` (identical to the probe used to find
the original defect), then ran `tests/web/test_provenance_panel.py -v`:

```
test_the_panel_lists_the_rounds                              PASSED
test_a_run_with_no_history_says_so                            PASSED
test_the_panel_says_so_when_git_is_missing                    PASSED
test_a_diff_is_rendered_when_two_points_are_given             FAILED
test_a_hostile_diff_ref_comes_back_as_a_message_not_a_500     FAILED
test_no_diff_panel_without_diff_params                        PASSED
test_the_curation_version_panel_still_works                   PASSED
test_diff_text_is_escaped_not_raw_html                        FAILED
```

The three failures were the expected kind, not incidental:

- `test_a_diff_is_rendered_when_two_points_are_given`:
  `AssertionError` on `"-merged v1" in page.text and "+merged v2" in
  page.text` — neither prefix appears anywhere once the diff panel is gone.
- `test_a_hostile_diff_ref_comes_back_as_a_message_not_a_500`:
  `AssertionError` on `str(escape(f"not a point: {hostile!r}")) in
  page.text` — the exact escaped refusal string is nowhere on the page
  without the diff panel.
- `test_diff_text_is_escaped_not_raw_html`: `ValueError: substring not
  found` from `page.text.index('<pre class="diff">')` — the diff block
  itself does not exist, so there is nothing to scope the check to.

`test_no_diff_panel_without_diff_params` correctly still passed (it asserts
*absence*, which remains true), confirming it's testing the right thing and
not simply another false positive.

Restored `{% if diff %}`, reran the same file: **all 8 passed**. `git diff
--stat src/scieflow/web/templates/drafts.html` showed no diff against the
committed template — the probe left no residue.

### Full-suite re-verification after the test fix

- `uv run pytest tests/web/test_provenance_panel.py -v` — **8 passed**.
- `uv run pytest tests/web -v` — **258 passed**, 2 pre-existing warnings
  (was 257 before this addendum's new test).
- `uv run pytest tests/web/test_read_only.py tests/web/test_mutations.py tests/web/test_async_routes.py -v` — **57 passed**; route inventory unchanged (no new mutating route, no new async handler, confirmed again after the test-only change — expected, since no application code was touched in this addendum).
- `uv run pytest -q` — **1576 passed, 5 skipped, 6 deselected, 2 warnings**
  (1575 → 1576, the one new test; same skip count and same 2 pre-existing
  starlette/anyio deprecation warnings as every prior run in this task).

### What was left alone, per the coordinator's instruction

- The `provenance`/`history` key separation and its verification (the
  reintroduced-collision probe showing all 7 — now 8 — tests fail with
  `jinja2.exceptions.UndefinedError: 'provenance' is undefined`, from
  Jinja's default `Undefined.__getattr__` raising on the template's
  attribute access, not from `StrictUndefined` — see the correction below).
- The `|safe` falsification on `test_diff_text_is_escaped_not_raw_html`'s
  negative assertion, already confirmed load-bearing in the original round.
- All application code (`pages.py`, `drafts.html`) — this addendum is
  test-only.

## Addendum 2: the third sink, and the StrictUndefined correction (commit `3fce013`)

Review returned **approved** (spec ✅, task quality ✅), with one new test
requested and one correction to this report. Both are addressed here.

### New test: `point.ref`/`point.label` escaping (both sinks)

`test_diff_text_is_escaped_not_raw_html` only ever covered `diff.text`.
`point.ref` and `point.label` reach HTML too, and are not inert:
`provenance._draft_points` (`src/scieflow/core/provenance.py:882-883`)
builds both directly from an agent-chosen directory name —
`f"{short}:sections"` for `ref`, `f"{agent}'s draft"` for `label` — and
`drafts.html`'s own pre-existing comment already says an agent name is "not
a value from a safe alphabet" (about a different sink, but the same
underlying fact). Two places in the template read these two fields:

- body text, once: `drafts.html:100`, `<li><code>{{ point.ref }}</code> —
  {{ point.label }}`.
- an attribute value, twice: `drafts.html:107` and `:113`, `<option
  value="{{ point.ref }}">` in each of the two `<select>`s.

The attribute case is the one that mattered most to add: a raw `"` there
would terminate `value="…"` early and let the rest of the payload become
live markup — a different failure mode than the body-text case, and one no
body-text-only assertion would ever exercise.

Added `test_a_hostile_agent_name_is_escaped_in_the_history_panel` to
`tests/web/test_provenance_panel.py`. It writes a draft directory named
`evil"><script>alert(1)` (no trailing `/`, since `check_name` forbids path
separators) under `manuscript/drafts/`, which becomes a `draft/<name>`
branch and produces a `sections` point via the ordinary sync path — no
mocking, no direct call into `provenance` internals. Before writing the
test I confirmed directly that this exact payload is a genuine agent name
the feature must handle, not one upstream validation already excludes:

```
$ git check-ref-format 'refs/heads/draft/evil"><script>alert(1)'   # exit 0
$ python -c "from scieflow.core import drafts; drafts.check_name('evil\"><script>alert(1)')"   # no DraftError
```

(`</script>` with its closing slash *would* be refused by `check_name`,
since `/` is treated as a path separator — the payload deliberately omits
the closing tag for that reason; an unclosed `<script>` still demonstrates
the same escaping question.)

The test asserts:
- the raw payload (`'"><script>alert(1)'` and `"<script>alert(1)"`) is
  absent from the whole page;
- the `markupsafe`-escaped `ref` and `label` appear (covers the body-text
  sink, and — since the same `{{ point.label }}` expression is also used
  inside the `<option>` element's text content — most of the second
  `<option>` sink too);
- `f'value="{escape(ref)}"'` appears verbatim, specifically checking that
  the whole attribute value round-trips escaped rather than just checking
  the escaped string is present somewhere on the page.

### Falsification (as requested: `|safe` on the sinks under test, in place)

Two separate passes, each edited, run, confirmed to fail, then reverted
before the next:

**Pass 1 — body sink.** Changed `drafts.html:100` to `<li><code>{{
point.ref | safe }}</code> — {{ point.label | safe }}</li>`. Ran the new
test alone: **FAILED**, `AssertionError` on `'"><script>alert(1)' not in
page.text` (the raw payload leaked through the body text). Reverted the
line.

**Pass 2 — attribute sink.** With the body sink back to plain `{{ }}`,
changed both `<option value="{{ point.ref }}">` lines
(`drafts.html:107`, `:113`) to `<option value="{{ point.ref | safe }}">`.
Ran the new test alone: **FAILED** again, same assertion
(`'"><script>alert(1)' not in page.text`) — the raw `">` broke out of the
`value="…"` attribute exactly as expected, so the unescaped payload
appeared directly in the page's raw markup. Reverted both lines.

After both reverts: `diff /tmp/drafts.html.bak src/scieflow/web/templates/drafts.html`
showed **no difference** (a full byte-for-byte backup taken before the
falsification, per the "reviewer was killed mid-probe" warning), and
`git status`/`git diff --stat` showed only the intended test-file change —
confirmed *before* running the full suite or committing, exactly as asked.

Reran the whole file after both reverts: **9 passed** (the 8 from before
plus the new test).

### Correction: the "loud collision" mechanism is not `StrictUndefined`

The reviewer is right and I was wrong. I claimed in the original report
(the "Falsification transcripts" section and "Self-review findings") that
"this app's Jinja environment uses `StrictUndefined`." Checked directly:

```
$ grep -rn "StrictUndefined\|Undefined" src/scieflow/
(no output)
```

There is no `StrictUndefined` (or any custom `undefined=` setting) anywhere
in `src/scieflow/`; `TEMPLATES = Jinja2Templates(directory=...)` in
`src/scieflow/web/app.py` uses Jinja's plain default. The actual mechanism,
confirmed directly:

```
>>> from jinja2 import Undefined
>>> Undefined(name="provenance").available
jinja2.exceptions.UndefinedError: 'provenance' is undefined
```

Jinja's *default* `Undefined` class already raises `UndefinedError` from
`__getattr__` on attribute access — it only renders silently as empty for a
bare `{{ provenance }}` reference, not for `provenance.available` or
`provenance.points`, which is what `drafts.html` actually does. So the loud
failure I observed when reintroducing the `history`/`provenance` collision
was real and correctly interpreted, but for the wrong reason: it holds
because this template reads *attributes* off the context key, not because
of any Jinja environment configuration. Both places in the report
(`## Falsification transcripts` item 3, and `## Self-review findings`) have
been corrected in place to say this, with a note that the guard would stop
working if a future change referenced `provenance` as a bare value instead
of via attribute access — no environment/config change required to lose it,
which is the opposite of what the original (wrong) explanation implied.

### Full-suite re-verification after this addendum

- `uv run pytest tests/web/test_provenance_panel.py -v` — **9 passed**.
- `uv run pytest tests/web -v` — **259 passed**, 2 pre-existing warnings
  (258 → 259, the one new test).
- `uv run pytest tests/web/test_read_only.py tests/web/test_mutations.py tests/web/test_async_routes.py -v` — **57 passed**; route inventory unchanged (test-only change, no route or handler touched).
- `uv run pytest -q` — **1577 passed, 5 skipped, 6 deselected, 2 warnings**
  (1576 → 1577, the one new test; same skip count and same 2 pre-existing
  starlette/anyio deprecation warnings as every prior run in this task).

`git status`/`git diff --stat` confirmed clean (only the intended
`tests/web/test_provenance_panel.py` change) both before and after this
addendum's falsification passes. Committed as `3fce013`.
