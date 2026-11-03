"""Synthetic fixtures for M-G t5 (decision letters: ingest, prompt, adopt; M-G 5.10-5.11).

Every name, journal, comment and paragraph here is invented. The letter has three blocks:
E (greeting, decision, one editor comment), R1 (two comments) and R2 (two comments, a closing).
Line numbers below are 1-based lines of LETTER (the normalised letter.txt).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner

from scieflow.cli import main

LETTER_LINES = [
    "Dear Dr. Synthetic,",                                                                    # 1  E.p1
    "",                                                                                       # 2
    "Thank you for submitting your manuscript \"Gait timing in synthetic cohorts\" to the "
    "Journal of Synthetic Neurology. We have now received the reviews.",                      # 3  E.p2
    "",                                                                                       # 4
    "Associate Editor's decision: major revision.",                                           # 5  E.p3
    "",                                                                                       # 6
    "The reviewers agree that the topic is relevant but raise important concerns about the "
    "analysis. Please address them.",                                                         # 7  E.p4
    "",                                                                                       # 8
    "Reviewer #1 (Synthetic Reviewer):",                                                      # 9  R1.p1
    "",                                                                                       # 10
    "1. The sample size calculation is not reported in the Methods section and should be added.",  # 11 R1.p2
    "2. The timing measure is defined twice with different units; please reconcile the two definitions.",  # 12
    "",                                                                                       # 13
    "Reviewer #2 (Synthetic Reviewer):",                                                      # 14 R2.p1
    "",                                                                                       # 15
    "The figures are clear but Figure 2 lacks a legend describing the error bars.",           # 16 R2.p2
    "",                                                                                       # 17
    "Please also check the reference list for formatting.",                                   # 18 R2.p3
    "",                                                                                       # 19
    "Sincerely,",                                                                             # 20 R2.p4
    "Synthetic Editor",                                                                       # 21
]

LETTER = "\n".join(LETTER_LINES) + "\n"
LETTER_SHA = hashlib.sha256(LETTER.encode("utf-8")).hexdigest()

# The expected structure of LETTER (block id, label line, start, end, paragraphs).
EXPECTED_BLOCKS = {
    "E": (5, 1, 7, [("E.p1", 1, 1), ("E.p2", 3, 3), ("E.p3", 5, 5), ("E.p4", 7, 7)]),
    "R1": (9, 9, 12, [("R1.p1", 9, 9), ("R1.p2", 11, 12)]),
    "R2": (14, 14, 21, [("R2.p1", 14, 14), ("R2.p2", 16, 16), ("R2.p3", 18, 18), ("R2.p4", 20, 21)]),
}

LETTER_HTML = "<html><body>" + "".join(
    f"<p>{line}</p>" for line in LETTER_LINES if line.strip()
) + "</body></html>\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cli(*args, env: dict | None = None):
    """Invoke `scieflow manuscript revision <args>` in-process."""
    return CliRunner().invoke(main, ["manuscript", "revision", *args], catch_exceptions=False,
                              env=env or {})


def read_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, record: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def inbox(synth, name: str = "letter.txt", text: str = LETTER, data: bytes | None = None) -> Path:
    """Write a letter inside the run (letters must be read from the run folder, M-G 5.1)."""
    path = synth.run_dir / "inbox" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else text.encode("utf-8"))
    return path


def ingest(synth, *extra: str, name: str = "letter.txt", text: str = LETTER):
    path = inbox(synth, name=name, text=text)
    return cli("ingest", "ms-run", "--letter", str(path), *extra)


def review_doc(block: str, major=(), minor=(), recommendation: str | None = None,
               summary: str = "Synthetic summary of the comments.") -> dict:
    """A manuscript-review document for one letter block; major and minor are (id, location, text)."""
    rec = recommendation or ("MAJOR REVISION" if major else "MINOR REVISION")
    return {
        "agent": f"journal-{block.lower()}",
        "reviewed": "manuscript",
        "recommendation": rec,
        "summary": summary,
        "major": [{"id": i, "location": loc, "problem": text, "why_it_matters": "Synthetic reason.",
                   "resolution": "Synthetic resolution."} for i, loc, text in major],
        "minor": [{"id": i, "location": loc, "comment": text} for i, loc, text in minor],
    }


def valid_draft(letter_sha: str = LETTER_SHA, round_: int = 1) -> dict:
    """A draft that adopts cleanly against LETTER (every paragraph accounted for, every quote verbatim)."""
    e = review_doc("E", major=[("M1", "letter:7-7",
                                "The reviewers agree that the topic is relevant but raise important "
                                "concerns about the analysis.")])
    r1 = review_doc("R1",
                    major=[("M1", "letter:11-11",
                            "The sample size calculation is not reported in the Methods section and "
                            "should be added.")],
                    minor=[("m1", "letter:12-12",
                            "The timing measure is defined twice with different units; please reconcile "
                            "the two definitions.")])
    r2 = review_doc("R2", minor=[
        ("m1", "letter:16-16", "The figures are clear but Figure 2 lacks a legend describing the error bars."),
        ("m2", "letter:18-18", "Please also check the reference list for formatting."),
    ])
    not_comments = [
        ("E.p1", "greeting line"), ("E.p2", "summary of the manuscript"), ("E.p3", "decision line"),
        ("R1.p1", "reviewer header line"), ("R2.p1", "reviewer header line"),
        ("R2.p4", "closing and signature"),
    ]
    return {
        "version": 1,
        "round": round_,
        "letter_sha256": letter_sha,
        "reviews": [{"block": "E", "review": e}, {"block": "R1", "review": r1},
                    {"block": "R2", "review": r2}],
        "not_comments": [{"paragraph": p, "reason": r} for p, r in not_comments],
    }


def write_draft(synth, draft: dict, name: str = "codex-paper.json", round_: int = 1) -> Path:
    path = synth.run_dir / "revision" / f"round-{round_}" / "draft" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def adopt(synth, draft_path, *extra: str, round_: int = 1):
    return cli("adopt", "ms-run", "--round", str(round_), "--draft", str(draft_path), *extra)


def events_text(synth) -> str:
    """Every event file under the run, concatenated (events are data, read back for leak checks)."""
    out = []
    for path in sorted(synth.run_dir.rglob("*")):
        if path.is_file() and ("event" in path.name or path.suffix == ".jsonl"):
            out.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)
