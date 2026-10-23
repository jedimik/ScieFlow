"""Literature search through the provider Gateway (M-A T1, A-1/A-8).

Every `scieflow research search <source>` runs: policy (config/integrations.yml, the
search sources are on by default) -> `check_query` (leak check, before anything is
built) -> cache -> rate limit -> transport (no redirects) -> ledger. Only this module
creates the vetted search text the Gateway accepts (`providers.Checked` /
`providers.Composed`), so an unchecked string cannot reach the network as a query.

Refusals exit 3 (leak check, policy, provider switched off) and name the kind of
refusal, never the query. Transport failures exit 1 with their classification.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from typing import NoReturn

from scieflow.core.project import Project, ProjectError
from scieflow.core.review.claims import LeakError, check_text
from scieflow.integrations.scite.policy import PolicyError, exit_code
from scieflow.research.lib import http, papers, providers

# The search check (M-A-D2) is the review check plus two search conventions: a
# word-final `*` (a wildcard, `parkinson*`) and a standalone single digit (`type 2
# diabetes`) are ignored by the check only. The query is sent as typed.
_WILDCARD = re.compile(r"(?<=[^\W_])\*(?=\s|$)")
_STANDALONE_DIGIT = re.compile(r"(?<!\S)\d(?!\S)")

EXIT_REFUSED = 3
EXIT_FAILED = 1


def check_query(query: str) -> providers.Checked:
    """The query as a `Checked` value if it may leave the machine; `LeakError` otherwise."""
    if not isinstance(query, str):
        raise LeakError("refused: nothing left that can be sent")
    check_text(_STANDALONE_DIGIT.sub(" ", _WILDCARD.sub("", query)))
    return providers._mint(providers.Checked, query)


def _require_checked(checked) -> str:
    if not isinstance(checked, providers.Checked):
        raise TypeError("search syntax is composed only from a checked query (check_query)")
    return str(checked)


def arxiv_query(checked) -> providers.Composed:
    """arXiv's all-fields syntax around a checked query."""
    return providers._mint(providers.Composed, f"all:{_require_checked(checked)}")


def europepmc_query(checked, from_year: int | None = None) -> providers.Composed:
    """The Europe PMC query, with an optional publication-year clause (an integer year)."""
    text = _require_checked(checked)
    if from_year is not None:
        if not isinstance(from_year, int) or isinstance(from_year, bool):
            raise ValueError("from_year must be an integer year")
        text = f"{text} AND PUB_YEAR:[{from_year} TO 3000]"
    return providers._mint(providers.Composed, text)


def expect_object(body) -> None:
    """Validator for a JSON answer that must be an object."""
    if not isinstance(body, dict):
        raise http.HTTPFailure("search response has an unexpected shape", "MALFORMED")


def add_workspace_option(ap) -> None:
    ap.add_argument("--workspace", metavar="SLUG", default=None,
                    help="Record the request in workspace/SLUG (run ledger and events). "
                         "Without it the search is recorded in the per-day ledger only.")


def gateway(workspace: str | None, start=None) -> providers.Gateway:
    project = Project.discover(start)
    policy = providers.load(project.root)
    if workspace is None:
        return providers.Gateway.unscoped(policy)
    ws = project.run_dir(workspace)
    if not ws.is_dir():
        raise ProjectError(f"no run workspace/{workspace} in this project")
    return providers.Gateway(ws, policy)


def _fail(code: int, message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(code)


def run(workspace: str | None, search: Callable[[providers.Gateway], list]) -> None:
    """Build the gateway, run `search(gw)` (which checks the query before it sends
    anything), print the records. Refusals and failures end the process with a code."""
    try:
        gw = gateway(workspace)
        records = search(gw)
    except LeakError as exc:
        _fail(EXIT_REFUSED, f"refused: {exc}")
    except PolicyError as exc:
        _fail(exit_code(exc), f"refused: {exc}")
    except http.HTTPFailure as exc:
        _fail(EXIT_FAILED, f"search failed: {exc.classification}")
    except (ProjectError, FileNotFoundError, ValueError) as exc:
        _fail(EXIT_FAILED, f"error: {exc}")
    papers.emit(records)
