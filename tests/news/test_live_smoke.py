import shutil

import pytest

from scieflow.news.agents import run_agent

INSTALLED = [a for a in ("claude", "codex", "agy") if shutil.which(a)]


@pytest.mark.live
@pytest.mark.skipif(not INSTALLED, reason="no agent CLI on PATH")
def test_live_agent_responds():
    out = run_agent(
        INSTALLED[0], "Reply with exactly the word PONG and nothing else.", timeout=120
    )
    assert "PONG" in out
