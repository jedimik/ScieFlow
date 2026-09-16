from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Sequence

from . import agents
from .config import Config, resolve_selection
from .db import Database
from .prompts import build_prompt
from .report import ExtractionError, extract_block
from .templates import TEMPLATES, required_sections


@dataclass
class Progress:
    interest: str
    status: str  # "running" | "done" | "failed"
    detail: str = ""


def compute_window(
    db: Database,
    name: str,
    today: date,
    lookback_days: int,
    since: date | None,
    days: int | None,
) -> tuple[date, date]:
    if since is not None:
        return since, today
    if days is not None:
        return today - timedelta(days=days), today
    last = db.get_last_checked(name)
    if last is not None:
        return last, today
    return today - timedelta(days=lookback_days), today


def run_queue(
    config: Config,
    db: Database,
    *,
    agent: str | None = None,
    only: Sequence[str] | None = None,
    groups: Sequence[str] | None = None,
    since: date | None = None,
    days: int | None = None,
    today: date | None = None,
    model: str | None = None,
    reasoning: str | None = None,
    progress: Callable[[Progress], None] = lambda p: None,
    agent_runner: Callable[[str, str, int, str | None, str | None], str] | None = None,
) -> int:
    agent = agent or config.agent
    today = today or date.today()
    runner = agent_runner or agents.run_agent

    model = model or config.model
    reasoning = reasoning or config.reasoning
    names = resolve_selection(config, only, groups)
    if names is None:
        selected = list(config.interests)
    else:
        wanted = set(names)
        selected = [i for i in config.interests if i.name in wanted]

    run_id = db.create_run(agent)
    for interest in selected:
        start, end = compute_window(
            db, interest.name, today, interest.lookback_days or config.lookback_days, since, days
        )
        progress(Progress(interest.name, "running"))
        prompt = build_prompt(interest, start, end)
        timeout = config.timeout or TEMPLATES[interest.template].suggested_timeout
        error = ""
        last_raw: str | None = None
        for _attempt in range(2):
            try:
                last_raw = None
                raw = runner(agent, prompt, timeout, model=model, reasoning=reasoning)
                last_raw = raw
                block = extract_block(
                    raw, interest.name, required_sections=required_sections(interest.template)
                )
                break
            except (agents.AgentError, ExtractionError) as e:
                error = str(e)
        else:
            db.add_failure(run_id, interest.name, error, raw_output=last_raw)
            progress(Progress(interest.name, "failed", error))
            continue
        db.add_result(run_id, interest.name, start, end, block)
        db.set_last_checked(interest.name, today)
        progress(Progress(interest.name, "done"))
    return run_id
