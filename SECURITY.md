# Security

## What ScieFlow protects, and what it does not

ScieFlow runs AI agents over research data. It protects that data with:

- an agent sandbox (bubblewrap) that limits what each dispatched agent can read
  and write, and hides other runs' homes and project secrets (see
  [the sandbox](docs/sandbox.md));
- data levels (L0, L1, L2) that the user declares for each run, with dispatch,
  literature, scite, NotebookLM and remote rules that refuse L2 data to cloud
  agents and services (see [Data protection](docs/data-protection.md));
- human-only gates for approvals, releases of checked aggregates, and some
  external sharing.

It does not protect against everything:

- the sandbox network is open, so an agent can open connections that ScieFlow's
  own clients do not make;
- a third-party agent CLI sends its own traffic to its provider, which ScieFlow
  does not inspect;
- the data scan and release checks are heuristics, and cannot know what a file
  holds;
- a process run by the user, including the coordinator, has the user's own
  permissions.

The full list of limits is in [Data protection](docs/data-protection.md#what-this-does-not-do).

## Reporting a vulnerability

Report a vulnerability privately: never a public issue. Open a private security
advisory on the repository (GitHub: Security, then Report a vulnerability), or
contact the maintainer directly. Include
the version, the steps to reproduce, and what an attacker could gain. Do not
include real patient or participant data in a report.
