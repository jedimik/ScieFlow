### Task 3: Reading — points and diffs

**Files:**
- Modify: `src/scieflow/core/provenance.py`
- Test: `tests/core/test_provenance.py` (append)

**Interfaces:**
- Consumes: everything Tasks 1–2 produced.
- Produces:
  `provenance.points(ws) -> list[dict]` — each `{"ref": "main:merge_2", "label": "Merge round 2", "kind": "merge"|"review"|"draft"|"outline", "commit": "<sha>", "at": "<iso>"}`;
  `provenance.diff(ws, a, b) -> dict` — `{"text": str, "truncated": bool, "a": str, "b": str}`;
  `provenance.DIFF_LIMIT = 200_000`.

**A point is a `<ref>:<tree path>` pair** — `main:merge_2`, `draft/claude:sections`. It is what the panel lists, what `diff` accepts, and the whitelist the query parameters are compared against as whole strings.

**Ref validation is the security requirement of this task.** `a` and `b` reach `git diff`. Each must equal a string `points()` returned, compared as a whole string — not prefix-matched, not sanitised, not merely checked for `..`. An unvalidated `a` could be argument-shaped (`--output=/tmp/x`), which no containment check would catch.

- [ ] **Step 1: Write the failing test**

```python
# appended to tests/core/test_provenance.py

def test_points_lists_what_the_run_has(drafted):
    provenance.sync(drafted)
    refs = [p["ref"] for p in provenance.points(drafted)]
    assert "main:merge_1" in refs
    assert "main:merge_2" in refs
    assert "main:review_1" in refs
    assert "draft/claude:sections" in refs
    assert "draft/codex:sections" in refs
    for point in provenance.points(drafted):
        assert point["commit"] and point["at"] and point["label"]


def test_a_run_with_no_history_has_no_points(ws):
    """REVIEW FOCUS 2: an ordinary state — the panel says so rather than
    raising or rendering an empty shell."""
    provenance.sync(ws)
    assert provenance.points(ws) == []


def test_points_are_stable_across_syncs(drafted):
    provenance.sync(drafted)
    before = [p["ref"] for p in provenance.points(drafted)]
    provenance.sync(drafted)
    assert [p["ref"] for p in provenance.points(drafted)] == before


def test_a_round_to_round_diff_shows_only_what_changed(drafted):
    provenance.sync(drafted)
    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert "-merged v1" in result["text"]
    assert "+merged v2" in result["text"]
    assert result["truncated"] is False


def test_a_draft_against_a_round_diff_works(drafted):
    provenance.sync(drafted)
    result = provenance.diff(drafted, "draft/claude:sections", "main:merge_1/sections")
    assert "claude's results" in result["text"]
    assert "merged v1" in result["text"]


@pytest.mark.parametrize("hostile", [
    "--output=/tmp/pwned",
    "-x",
    "main:merge_1 --output=/tmp/pwned",
    "../../etc/passwd",
    "main:../../../etc",
    "refs/heads/main",          # a real ref, but not a point `points()` returned
    "",
])
def test_a_ref_that_is_not_a_listed_point_is_refused(drafted, hostile):
    """The whitelist is whole-string equality against `points()`. An
    argument-shaped value is the case a containment check would miss."""
    provenance.sync(drafted)
    with pytest.raises(provenance.ProvenanceError, match="not a point"):
        provenance.diff(drafted, hostile, "main:merge_2")
    with pytest.raises(provenance.ProvenanceError, match="not a point"):
        provenance.diff(drafted, "main:merge_2", hostile)


def test_no_file_is_created_by_a_hostile_ref(drafted, tmp_path):
    provenance.sync(drafted)
    target = tmp_path / "pwned"
    with pytest.raises(provenance.ProvenanceError):
        provenance.diff(drafted, f"--output={target}", "main:merge_2")
    assert not target.exists(), "git was handed an option as a ref"


def test_a_large_diff_is_truncated_and_says_so(drafted):
    """REVIEW FOCUS 3: a big diff must not be read whole into a page."""
    big = "x" * (provenance.DIFF_LIMIT + 5000) + "\n"
    (drafted / "manuscript" / "curation" / "rounds" / "2" / "results.tex").write_text(big)
    provenance.sync(drafted)
    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert result["truncated"] is True
    assert len(result["text"]) <= provenance.DIFF_LIMIT + 200
    assert "truncated" in result["text"].lower()


def test_a_binary_artifact_diffs_without_dumping_bytes(drafted):
    """REVIEW FOCUS 3: `manuscript/` can hold a compiled PDF or a figure."""
    pdf = drafted / "manuscript" / "curation" / "rounds" / "2" / "figure.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 20)
    provenance.sync(drafted)
    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert "Binary files" in result["text"] or "figure.pdf" in result["text"]
    assert "\x00" not in result["text"], "raw bytes reached the diff text"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_provenance.py -k "points or diff or hostile or truncated or binary" -v`
Expected: FAIL — `module 'scieflow.core.provenance' has no attribute 'points'`.

- [ ] **Step 3: Write the implementation**

`points(ws)` reads the repo rather than the workspace, so it reflects what is actually committed:
- for `refs/heads/main`, list its tree's top level (`ls-tree --name-only main`) and emit a point per `merge_<n>` and `review_<N>` directory, plus one for `outline.md` when present.
- for each `refs/heads/draft/<agent>`, emit a point for `sections` when that path exists in the tree, and one per `review_<N>` directory.
- each point carries the branch tip's sha and committer date, read once per branch with `log -1 --format=%H%x00%cI`.
- sort newest-round-first within a branch, `main` before the draft branches, so the panel reads top-down as the most recent work.
- a ref that does not exist contributes nothing; a repo with no branches yields `[]`.

`diff(ws, a, b)`:
- build `allowed = {p["ref"] for p in points(ws)}` and raise `ProvenanceError(f"not a point: {a!r}")` unless `a in allowed`, same for `b`. Whole-string equality, and do this **before** any git call.
- run `_git(repo, "diff", "--no-color", a, b)` — note `a` and `b` are already `<ref>:<path>` strings, which `git diff` accepts as tree-ish arguments.
- truncate to `DIFF_LIMIT` characters, appending a line stating the diff was truncated, and set `truncated`.
- the returned `text` must be safe to render: `_git` already decodes as text, and git itself reports binary files as `Binary files … differ` rather than emitting bytes. Pass `errors="replace"` when decoding so a malformed byte cannot raise — the same choice `service._log_tail` made.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_provenance.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/provenance.py tests/core/test_provenance.py
git commit -m "feat(core): list provenance points and diff between them, refs whitelisted"
```

---

