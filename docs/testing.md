# Testing and the CI gate

ScieFlow has one offline test suite. Its default selection excludes the slow,
live and scite_live markers, so it never spends agent quota, never calls a
real scite service, and never builds apptainer containers.

## The CI gate

The GitHub workflow `.github/workflows/tests.yml` runs on pull requests, on
pushes to `main` and on manual dispatch. It runs on `ubuntu-24.04` only, with a
read-only token (`contents: read`) and no secrets. The job installs the locked
environment with `uv sync --frozen --python 3.12 --all-extras --group dev` and
then runs `scripts/ci-tests.sh`, and nothing else.

`scripts/ci-tests.sh` runs pytest on `tests` with the marker expression
`not slow and not live and not scite_live`, and it ignores the sealed probe
file `tests/probes/test_k_task8_binding.py`. The same marker expression is
declared in `pyproject.toml` (`addopts`); a static test keeps the two equal.

The gate does not wait for live agent calls or scite requests: those tests are
excluded, and no secret is available to the job.

## Running the same gate locally

```
uv sync --frozen --python 3.12 --all-extras --group dev
scripts/ci-tests.sh
```

Extra arguments go to pytest, for example `scripts/ci-tests.sh --junitxml=junit.xml`.

The script makes the test process hermetic before it starts pytest. It sets
`TMPDIR` to a fresh directory under `$HOME/.cache`, because the sandbox tests mount a
private tmpfs on `/tmp` and expect the temp homes they build to sit under `/home`,
and it removes `claude`, `codex` and `agy` from `PATH`, so a developer machine with
agent CLIs installed runs the same as CI.

## Static checks of the gate

`tests/test_ci_workflow.py` reads the workflow, the script and `pyproject.toml`
as text. It checks: one job on `ubuntu-24.04` with a timeout and no matrix; a
read-only top-level token and no secrets, no `pull_request_target` and no
untrusted event data; exactly the pull request, push-to-main and manual
triggers; only the two pinned actions, with checkout not persisting
credentials; the install and test steps; and the marker expression. Negative
self-tests show that each violation is detected.

## Sealed probes

The sealed probe suite under `tests/probes/` is not part of the CI gate. Its
binding file `tests/probes/test_k_task8_binding.py` is excluded by the script,
and the other probes run only when invoked directly.
