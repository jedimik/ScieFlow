"""M-D3 t3 RED stub: the remote campaign submit path (plan 5.4-5.6). Every function raises until GREEN."""

from __future__ import annotations

from datetime import datetime, timezone

RUNNER = None  # test seam: the transport runner (None = the real subprocess runner)


class RemoteError(Exception):
    """A refusal: `rule` names the rule, `message` says what failed (nothing was written or sent)."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resume_submission(campaign_dir, run_dir, *, runner=None):
    raise NotImplementedError("M-D3 t3: resume_submission is not implemented yet")


def preflight(*args, **kwargs):
    raise NotImplementedError("M-D3 t3: preflight is not implemented yet")


def stage_and_submit(*args, **kwargs):
    raise NotImplementedError("M-D3 t3: stage_and_submit is not implemented yet")
