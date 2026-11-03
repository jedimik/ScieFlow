"""Static checks of the CI test gate (M-I plan section 5.1; DECISIONS D22).

The checks read the workflow, the script and pyproject.toml as text and
return a list of violations. The negative self-tests run the same checks on
modified in-memory copies, so a checker that stops detecting a violation
fails here.
"""

from __future__ import annotations

import re
import stat
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
SCRIPT = ROOT / "scripts" / "ci-tests.sh"
PYPROJECT = ROOT / "pyproject.toml"
TESTING_DOC = ROOT / "docs" / "testing.md"
MKDOCS = ROOT / "mkdocs.yml"

MARKER_EXPR = "not slow and not live and not scite_live"
SEALED_IGNORE = "--ignore=tests/probes/test_k_task8_binding.py"
ALLOWED_USES = {"actions/checkout@v4", "astral-sh/setup-uv@v5"}
INSTALL_RUN = "uv sync --frozen --python 3.12 --all-extras --group dev"


def _load_workflow(text: str) -> dict:
    return yaml.safe_load(text)


def _uses_values(doc: dict) -> list[str]:
    values = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if "uses" in step:
                values.append(step["uses"])
    return values


def _steps(doc: dict) -> list[dict]:
    steps = []
    for job in (doc.get("jobs") or {}).values():
        steps.extend(job.get("steps") or [])
    return steps


def workflow_violations(text: str) -> list[str]:
    out: list[str] = []
    doc = _load_workflow(text)
    jobs = doc.get("jobs") or {}

    # 1. one job, ubuntu-24.04, no matrix, timeout present
    if len(jobs) != 1:
        out.append(f"check1: expected exactly one job, found {len(jobs)}")
    for name, job in jobs.items():
        if job.get("runs-on") != "ubuntu-24.04":
            out.append(f"check1: job {name} runs-on is {job.get('runs-on')!r}")
        if "strategy" in job:
            out.append(f"check1: job {name} has strategy (matrix not allowed)")
        if "timeout-minutes" not in job:
            out.append(f"check1: job {name} has no timeout-minutes")

    # 2. read-only token, no secrets, no untrusted event data
    if doc.get("permissions") != {"contents": "read"}:
        out.append(f"check2: top-level permissions are {doc.get('permissions')!r}")
    for name, job in jobs.items():
        if "permissions" in job:
            out.append(f"check2: job {name} sets its own permissions")
    if "secrets." in text:
        out.append("check2: the text mentions secrets.")
    if "pull_request_target" in text:
        out.append("check2: pull_request_target is used")
    if "${{ github.event" in text:
        out.append("check2: untrusted github.event data appears in the workflow")

    # 3. triggers exactly pull_request, push (main), workflow_dispatch
    triggers = doc.get("on", doc.get(True))
    if not isinstance(triggers, dict) or set(triggers) != {
        "pull_request",
        "push",
        "workflow_dispatch",
    }:
        out.append(f"check3: triggers are {triggers!r}")
    elif (triggers.get("push") or {}).get("branches") != ["main"]:
        out.append(f"check3: push trigger is {triggers.get('push')!r}")

    # 4. allowed actions only; checkout without persisted credentials
    uses = _uses_values(doc)
    if set(uses) != ALLOWED_USES or len(uses) != len(set(uses)):
        out.append(f"check4: uses values are {uses!r}")
    for step in _steps(doc):
        if str(step.get("uses", "")).startswith("actions/checkout"):
            if (step.get("with") or {}).get("persist-credentials") is not False:
                out.append("check4: checkout does not set persist-credentials: false")

    # 5. install step and test step
    runs = [step.get("run", "") for step in _steps(doc)]
    install = [r for r in runs if "uv sync" in r]
    if len(install) != 1:
        out.append(f"check5: expected one uv sync step, found {len(install)}")
    else:
        line = install[0].strip()
        if line != INSTALL_RUN:
            out.append(f"check5: install step runs {line!r}")
    if "scripts/ci-tests.sh" not in [r.strip() for r in runs]:
        out.append("check5: no step runs exactly scripts/ci-tests.sh")
    return out


def _marker_expression(script_text: str) -> str | None:
    found = re.findall(r"-m '([^']*)'", script_text)
    return found[0] if found else None


def _declared_markers(pyproject_text: str) -> list[str]:
    data = tomllib.loads(pyproject_text)
    markers = data["tool"]["pytest"]["ini_options"]["markers"]
    return [m.split(":", 1)[0].strip() for m in markers]


def _addopts_expression(pyproject_text: str) -> str | None:
    data = tomllib.loads(pyproject_text)
    addopts = data["tool"]["pytest"]["ini_options"]["addopts"]
    return _marker_expression(addopts)


def script_violations(text: str, mode: int, pyproject_text: str) -> list[str]:
    out: list[str] = []
    # 6. script exists, executable, marker expression, sealed ignore, pytest on tests
    if not mode & stat.S_IXUSR:
        out.append("check6: script is not executable")
    if MARKER_EXPR not in text:
        out.append("check6: script lacks the marker expression")
    if SEALED_IGNORE not in text:
        out.append("check6: script lacks the sealed-probe --ignore")
    if not re.search(r"python -m pytest[\s\S]*\btests\b", text):
        out.append("check6: script does not run pytest on tests")
    for forbidden in ("-m live", "-m scite_live", "--run-live"):
        if forbidden in text:
            out.append(f"check6: script selects live tests ({forbidden})")
    expr = _marker_expression(text) or ""
    for marker in _declared_markers(pyproject_text):
        if f"not {marker}" not in expr.split(" and "):
            out.append(f"check6: marker {marker} is not negated in the script")

    # 7. script expression equals pyproject addopts expression
    if _marker_expression(text) != _addopts_expression(pyproject_text):
        out.append("check7: script marker expression differs from pyproject addopts")

    # 8. hermetic test environment: TMPDIR outside /tmp (the sandbox mounts a
    #    private tmpfs on /tmp) and no agent CLI on the test process PATH
    if 'mktemp -d /var/tmp/' not in text or 'TMPDIR="$TEST_TMP"' not in text:
        out.append("check8: script does not point TMPDIR outside /tmp")
    if "for c in claude codex agy" not in text:
        out.append("check8: script does not strip claude, codex and agy from PATH")
    return out


def doc_gate_violations(text: str) -> list[str]:
    out: list[str] = []
    for needle in (
        "scripts/ci-tests.sh",
        MARKER_EXPR,
        "tests/probes/test_k_task8_binding.py",
        "ubuntu-24.04",
        "does not wait",
    ):
        if needle not in text:
            out.append(f"doc: testing.md does not name {needle!r}")
    return out


# --- the real files -------------------------------------------------------


def test_workflow_passes_static_checks():
    assert workflow_violations(WORKFLOW.read_text()) == []


def test_script_passes_static_checks():
    text = SCRIPT.read_text()
    mode = SCRIPT.stat().st_mode
    assert script_violations(text, mode, PYPROJECT.read_text()) == []


def test_testing_doc_is_in_nav_and_names_the_gate():
    assert "testing.md" in MKDOCS.read_text()
    assert doc_gate_violations(TESTING_DOC.read_text()) == []


# --- negative self-tests (modified in-memory copies) ----------------------


def _wf() -> str:
    return WORKFLOW.read_text()


def _sh() -> str:
    return SCRIPT.read_text()


def _pp() -> str:
    return PYPROJECT.read_text()


def test_negative_secret_reference_is_detected():
    bad = _wf() + "\n# token: ${{ secrets.SOME_TOKEN }}\n"
    assert any("check2" in v for v in workflow_violations(bad))


def test_negative_pull_request_target_is_detected():
    bad = _wf().replace("  pull_request:", "  pull_request_target:", 1)
    assert any("check2" in v or "check3" in v for v in workflow_violations(bad))


def test_negative_macos_runner_is_detected():
    bad = _wf().replace("runs-on: ubuntu-24.04", "runs-on: macos-latest", 1)
    assert any("check1" in v for v in workflow_violations(bad))


def test_negative_matrix_is_detected():
    bad = _wf().replace(
        "    timeout-minutes:",
        "    strategy:\n      matrix:\n        python: ['3.12']\n    timeout-minutes:",
        1,
    )
    assert any("check1" in v for v in workflow_violations(bad))


def test_negative_live_marker_in_script_is_detected():
    bad = _sh().replace(SEALED_IGNORE, "-m live " + SEALED_IGNORE, 1)
    assert any("check6" in v for v in script_violations(bad, 0o755, _pp()))


def test_negative_missing_sealed_ignore_is_detected():
    bad = _sh().replace(SEALED_IGNORE, "", 1)
    assert any("check6" in v for v in script_violations(bad, 0o755, _pp()))


def test_negative_non_executable_script_is_detected():
    assert any(
        "check6" in v for v in script_violations(_sh(), 0o644, _pp())
    )


def test_negative_expression_drift_is_detected():
    bad = _sh().replace(MARKER_EXPR, "not slow and not live", 1)
    assert any("check7" in v or "check6" in v for v in script_violations(bad, 0o755, _pp()))


def test_negative_tmpdir_inside_tmp_is_detected():
    bad = _sh().replace('TMPDIR="$TEST_TMP"', "", 1)
    assert any("check8" in v for v in script_violations(bad, 0o755, _pp()))


def test_negative_agent_cli_on_path_is_detected():
    bad = _sh().replace("for c in claude codex agy", "for c in none", 1)
    assert any("check8" in v for v in script_violations(bad, 0o755, _pp()))


def test_negative_unnegated_marker_is_detected():
    # a declared marker absent from the script expression must be caught
    pp = _pp().replace(
        '    "scite_live:',
        '    "newmark: a future marker\",\n    "scite_live:',
        1,
    )
    assert any("check6" in v for v in script_violations(_sh(), 0o755, pp))
