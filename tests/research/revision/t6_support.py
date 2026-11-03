"""Synthetic helpers for M-G t6 (response, coverage and the response letter; M-G 5.12).

Every comment, name and value here is invented. Built on the t5 letter fixture (revision_support).
"""

from __future__ import annotations

from pathlib import Path

from tests.research.revision.revision_support import LETTER, adopt, cli, ingest, valid_draft, write_draft

RESPONSE_PLACEHOLDER = "Response: [write your response]"
CHANGE_PLACEHOLDER = "Change: [file \u2014 what changed]"
NO_CHANGE_PLACEHOLDER = "No change: [why]"


def round_path(synth, *parts) -> Path:
    return synth.run_dir.joinpath("revision", "round-1", *parts)


def response_path(synth) -> Path:
    return round_path(synth, "response.md")


def adopted_round(synth, draft: dict | None = None) -> None:
    """Ingest LETTER and adopt `draft` (the valid five-comment draft by default); asserts both succeed."""
    assert ingest(synth).exit_code == 0
    path = write_draft(synth, draft if draft is not None else valid_draft())
    result = adopt(synth, path)
    assert result.exit_code == 0, result.output


def respond(synth, *extra: str):
    return cli("respond", "ms-run", "--round", "1", *extra)


def coverage(synth, *extra: str):
    return cli("coverage", "ms-run", "--round", "1", *extra)


def letter(synth, *extra: str):
    return cli("letter", "ms-run", "--round", "1", *extra)


def fill_all(synth, response: str = "Synthetic answer to the comment.",
             change: str = "methods.tex \u2014 synthetic change") -> None:
    """Answer every section in response.md: the Response line and the Change line (the No change line stays)."""
    path = response_path(synth)
    body = path.read_text(encoding="utf-8")
    body = body.replace(RESPONSE_PLACEHOLDER, f"Response: {response}")
    body = body.replace(CHANGE_PLACEHOLDER, f"Change: {change}")
    path.write_text(body, encoding="utf-8")


def change_methods(synth, text: str = "Synthetic methods text, changed for the revision.\n") -> None:
    """Change manuscript/methods.tex so that it differs from the point submitted-r1."""
    (synth.ms / "methods.tex").write_text(text, encoding="utf-8")


LETTER_TEXT = LETTER
