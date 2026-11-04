# Changelog

All notable changes to ScieFlow are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- CI test gate: a `Tests` GitHub workflow runs the offline default suite on
  Ubuntu for every pull request and every push to `main`. `scripts/ci-tests.sh`
  runs the same command locally. See [Testing](docs/testing.md).
- Record checks: every event and job record is validated before it is written,
  and `scieflow workspace doctor` reports invalid lines in existing logs. The
  rules are in `schemas/events.yml` and `schemas/jobs.yml`.
- Read-only terminal views: `scieflow run overview`, `scieflow run budgets`,
  `scieflow run workflows` and `scieflow agent conversational`.
- Read-only MCP server: `scieflow mcp serve` offers twelve read-only tools to an
  interactive coordinator. See [The MCP server](docs/mcp.md).
- `CHANGELOG.md` and `CITATION.cff`: this file, and how to cite ScieFlow.

### Merged pull requests

- #28 m-g2 (2026-11-03)
- #27 m-d3 (2026-11-03)
- #26 pl (2026-11-02)
- #25 m-g (2026-11-02)
- #24 m-c2 (2026-10-31)
- #23 m-f (2026-10-30)
- #22 m-d2 (2026-10-29)
- #21 m-d1 (2026-10-29)
- #20 m-c1 (2026-10-28)
- #19 m-b (2026-10-27)
- #18 m-e (2026-10-25)
- #17 m-a-correctness (2026-10-25)
- #16 scite-web-integration (2026-10-23)
- #15 k-integration (2026-10-23)
- #13 scite-m2 (2026-10-14)
- #12 scite-m1 (2026-10-13)
- #11 scite-m1 (2026-10-13)
- #10 cloud-continuation (2026-10-13)
- #8 b-run-explorer (2026-10-04)
- #7 d-manuscript-provenance (2026-10-03)
- #6 c-workbench-followups (2026-09-27)
- #5 c-draft-workbench (2026-09-27)
- #4 a1d-start-wizard (2026-09-26)
- #3 a1c-conversation (2026-09-26)
- #2 a1b-run-charter (2026-09-26)
- #1 m2c-web-control (2026-09-26)

## [0.2.0] - 2026-09-16

First installable package; earlier history is in the git log.
