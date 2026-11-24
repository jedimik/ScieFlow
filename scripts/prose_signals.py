#!/usr/bin/env python3
"""Signals of inventory prose in article text.

The rules are in src/scieflow/research/templates/article-prose.md. This script reads LaTeX section
files (.tex) or plain text (.txt, .md), measures the register of the running text and lists the
places that deserve a second look. With --model it compares the text with a model text the author
named. Standard library only, so it also runs as `python3 scripts/prose_signals.py`.

The signals can only fail a text. A clean report is no proof of good prose: a long sentence of facts
joined by "and" passes every count here and still fails the reader, and no count can tell it from a
sentence that argues.

Usage:
    uv run scripts/prose_signals.py [--model FILE]... [--argues] [--json] [--strict] FILE...

Exit status: 0 (signals are information), 1 with --strict when a signal is raised, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MIN_SENTENCES = 5            # below this no rate is judged
MIN_WORDS_FOR_RATES = 300    # below this one negation moves the rate per 1,000 words by more than 3
MIN_PARAGRAPHS = 3           # below this the length of a paragraph is not judged
SHORT_PARAGRAPH = 60         # words; an argument needs room, and a paragraph per claim is a list in disguise
LONG_SENTENCE = 45

# Environments whose content is not running text.
FLOATS = (
    "figure", "figure*", "table", "table*", "tabular", "tabular*", "tabularx", "longtable",
    "lstlisting", "verbatim", "minted", "equation", "equation*", "align", "align*", "tikzpicture",
    "algorithm", "algorithmic", "thebibliography", "frontmatter", "keyword",
)
HEADING = re.compile(
    r"\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\s*(?:\[[^\]]*\])?\{[^{}]*\}"
)
ARGUES = re.compile(r"^\s*%+\s*argues\s*:\s*(.*\S)\s*$", re.I)
# An argues line that opens like this describes the paragraph instead of stating its claim.
TOPIC_OPENER = re.compile(
    r"^(?:describes?|presents?|lists?|introduces?|summari[sz]es?|outlines?|covers?|discusses?|details?|"
    r"overview|description|summary)\b",
    re.I,
)
CODE = re.compile(r"\\(?:texttt|verb\*?|lstinline|path|url|nolinkurl|code)\s*(?:\{[^{}]*\}|\|[^|]*\||![^!]*!)")
CITE = re.compile(r"~?\\[A-Za-z]*cite[A-Za-z]*\*?\s*(?:\[[^\]]*\]\s*)*\{[^{}]*\}")
REF = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|pageref)\*?\s*\{[^{}]*\}")
DROP = re.compile(
    r"\\(?:label|gap|footnote|todo|index|vspace|hspace|input|include|includegraphics)\*?\s*(?:\[[^\]]*\])?\{[^{}]*\}"
)
RUN_IN = re.compile(r"^\s*\\textbf\{[^{}]{1,90}[.:]\}\s*")
COMMAND = re.compile(r"\\[A-Za-z@]+\*?\s*(?:\[[^\]]*\])?")
MATH = re.compile(r"\$[^$]*\$|\\\([^)]*\\\)")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’./_-]*[A-Za-z0-9%]|[A-Za-z0-9]")
ABBREVIATIONS = (
    "e.g.", "i.e.", "et al.", "cf.", "vs.", "Fig.", "Figs.", "Eq.", "Eqs.", "Tab.", "Sec.", "Ref.",
    "Refs.", "No.", "approx.", "resp.", "Dr.", "Prof.", "etc.",
)
SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]”’]*\s+(?=[\"'(\[“‘]?[A-Z0-9])")

# Words that tie one statement to another: cause, purpose, contrast, condition, consequence, reading
# of a result. A sentence without any of them reports a state and leaves its meaning to the reader.
RELATION = re.compile(
    r"\b(?:because|since|so that|such that|so as to|in order to|whereas|while|although|though|unless|"
    r"if|when|whenever|thereby|therefore|thus|hence|consequently|accordingly|as a result|"
    r"which|whose|rather than|instead of|despite|"
    r"allow(?:s|ed|ing)?|enabl(?:e|es|ed|ing)|let(?:s|ting)?|mak(?:e|es|ing) it|mean(?:s|ing)? that|"
    r"show(?:s|ed|ing)? that|indicat(?:e|es|ed|ing)|suggest(?:s|ed|ing)?|confirm(?:s|ed|ing)?|"
    r"impl(?:y|ies|ied|ying)|explain(?:s|ed|ing)?|reflect(?:s|ed|ing)?|leading to|resulting in)\b"
    r"|,\s+so\b"
    r"|^(?:To|By|Given|Using|Treating|Having|With)\s+\w+",
    re.I,
)
NEGATION = re.compile(r"\b(?:not|no|never|neither|nor|none|without|cannot)\b|n['’]t\b", re.I)
IDENT_TOKEN = "CODETOKEN"
PLAIN_IDENT = re.compile(
    r"`[^`]+`|\b[\w.-]+/[\w./-]+\b|\b\w+_\w+\b|\b[\w-]+\.(?:py|ya?ml|json|tex|sh|smk|md|txt|csv|nii|gz)\b"
)


@dataclass
class Paragraph:
    file: str
    line: int
    text: str
    argues: str | None = None

    def sentences(self) -> list[str]:
        return split_sentences(self.text)


@dataclass
class Report:
    paragraphs: list[Paragraph] = field(default_factory=list)

    def measure(self) -> dict:
        sentences = [s for p in self.paragraphs for s in p.sentences()]
        lengths = [count_words(s) for s in sentences]
        words = sum(lengths)
        n = len(sentences)
        related = sum(1 for s in sentences if RELATION.search(s))
        negations = sum(len(NEGATION.findall(s)) for s in sentences)
        identifiers = sum(s.count(IDENT_TOKEN) for s in sentences)
        return {
            "paragraphs": len(self.paragraphs),
            "sentences": n,
            "words": words,
            "words_per_sentence": round(words / n, 1) if n else 0.0,
            "relation_share": round(related / n, 3) if n else 0.0,
            "negations_per_1000_words": round(1000 * negations / words, 1) if words else 0.0,
            "identifiers_per_100_words": round(100 * identifiers / words, 1) if words else 0.0,
            "sentences_over_45_words": sum(1 for length in lengths if length > LONG_SENTENCE),
            "paragraphs_without_argues": sum(1 for p in self.paragraphs if not p.argues),
            "words_per_paragraph": round(words / len(self.paragraphs), 1) if self.paragraphs else 0.0,
        }

    def look_again(self) -> list[dict]:
        """The places a reader should return to, in reading order."""
        items: list[dict] = []
        for p in self.paragraphs:
            sentences = p.sentences()
            if p.argues and names_a_topic(p.argues):
                items.append(_item("topic, not a claim", p, sentences[0] if sentences else p.text,
                                   f"the argues line names a topic: '{_short(p.argues, 80)}'"))
            start = None
            for i, unrelated in enumerate([not RELATION.search(s) for s in sentences] + [False]):
                if unrelated and start is None:
                    start = i
                elif not unrelated and start is not None:
                    if i - start >= 3:
                        items.append(_item("inventory run", p, sentences[start],
                                           f"{i - start} sentences in a row state no relation"))
                    start = None
            for s in sentences:
                length = count_words(s)
                if length > LONG_SENTENCE:
                    items.append(_item("long sentence", p, s, f"{length} words"))
                identifiers = s.count(IDENT_TOKEN)
                if identifiers >= 3:
                    items.append(_item("table row", p, s, f"{identifiers} identifiers in one sentence"))
        return items


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _item(kind: str, paragraph: Paragraph, sentence: str, why: str) -> dict:
    words = sentence.replace(IDENT_TOKEN, "‹code›").split()
    return {
        "kind": kind,
        "where": f"{paragraph.file}:{paragraph.line}",
        "why": why,
        "starts": " ".join(words[:12]) + (" …" if len(words) > 12 else ""),
    }


def names_a_topic(argues: str) -> bool:
    """True when an argues line labels its paragraph instead of stating what the paragraph claims."""
    return count_words(argues) < 6 or bool(TOPIC_OPENER.match(argues.strip()))


# ── reading the text ────────────────────────────────────────────────────────
def strip_comment(line: str) -> str:
    """The line without its LaTeX comment (an escaped \\% is kept)."""
    out, i = [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            out.append(line[i : i + 2])
            i += 2
            continue
        if ch == "%":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def tex_paragraphs(text: str, name: str) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    buffer: list[str] = []
    first_line = 0
    argues: str | None = None
    skip: str | None = None

    def flush() -> None:
        nonlocal buffer, argues
        plain = tex_to_plain(" ".join(buffer))
        if count_words(plain) >= 3:
            paragraphs.append(Paragraph(name, first_line, plain, argues))
            argues = None
        buffer = []

    for number, raw in enumerate(text.split("\n"), 1):
        if skip:
            if re.search(r"\\end\{%s\}" % re.escape(skip), raw):
                skip = None
            continue
        found = ARGUES.match(raw)
        if found:
            flush()
            argues = found.group(1)
            continue
        line = strip_comment(raw)
        begin = re.search(r"\\begin\{([^{}]+)\}", line)
        if begin and begin.group(1) in FLOATS:
            flush()
            if not re.search(r"\\end\{%s\}" % re.escape(begin.group(1)), line):
                skip = begin.group(1)
            continue
        if not line.strip():
            if not raw.strip():      # a blank line ends the paragraph, a comment-only line does not
                flush()
            continue
        if HEADING.search(line):
            flush()
            line = HEADING.sub(" ", line)
            if not line.strip():
                continue
        if not buffer:
            first_line = number
        buffer.append(line.strip())
    flush()
    return paragraphs


def tex_to_plain(text: str) -> str:
    text = RUN_IN.sub("", text)
    text = CODE.sub(f" {IDENT_TOKEN} ", text)
    text = CITE.sub("", text)
    text = REF.sub("N", text)
    text = DROP.sub("", text)
    text = MATH.sub(" 1 ", text)
    text = re.sub(r"\\(?:begin|end)\{[^{}]*\}", " ", text)
    text = text.replace("\\%", "%").replace("\\&", "&").replace("\\_", "_").replace("\\#", "#")
    text = COMMAND.sub("", text)
    text = text.replace("~", " ").replace("{", "").replace("}", "").replace("``", "“").replace("''", "”")
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def plain_paragraphs(text: str, name: str) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    fenced = False
    buffer: list[str] = []
    first_line = 0

    def flush() -> None:
        nonlocal buffer
        plain = PLAIN_IDENT.sub(f" {IDENT_TOKEN} ", " ".join(buffer))
        plain = re.sub(r"\s+", " ", plain).strip()
        if count_words(plain) >= 3:
            paragraphs.append(Paragraph(name, first_line, plain))
        buffer = []

    for number, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("```"):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|")):
            flush()
            continue
        if not buffer:
            first_line = number
        buffer.append(re.sub(r"^(?:[-*+]|\d+\.)\s+", "", stripped))
    flush()
    return paragraphs


def split_sentences(text: str) -> list[str]:
    for index, abbreviation in enumerate(ABBREVIATIONS):
        text = text.replace(abbreviation, f"\x00{index}\x00")
    out = []
    for part in SENTENCE_END.split(text):
        for index, abbreviation in enumerate(ABBREVIATIONS):
            part = part.replace(f"\x00{index}\x00", abbreviation)
        part = part.strip()
        if count_words(part) >= 2:
            out.append(part)
    return out


def count_words(text: str) -> int:
    return len(WORD.findall(text))


def read(paths: list[str]) -> Report:
    report = Report()
    for path in paths:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        if path.endswith(".tex"):
            report.paragraphs += tex_paragraphs(text, path)
        else:
            report.paragraphs += plain_paragraphs(text, path)
    return report


# ── signals ─────────────────────────────────────────────────────────────────
def signals(text: dict, model: dict | None, argues: bool) -> tuple[list[str], list[str]]:
    """(signals, notes). A signal is a reason to return to the text; an empty list proves nothing."""
    found: list[str] = []
    notes: list[str] = []
    n, words = text["sentences"], text["words"]
    if not n:
        return ["no running text found"], notes
    if n < MIN_SENTENCES:
        notes.append(f"{n} sentences: fewer than {MIN_SENTENCES}, so no rate is judged")
    else:
        if model and model["sentences"] < MIN_SENTENCES:
            notes.append(f"the model text has {model['sentences']} sentences: fewer than {MIN_SENTENCES}, "
                         "so the built-in limits are used")
            model = None
        rates = words >= MIN_WORDS_FOR_RATES
        if not rates:
            notes.append(f"{words} words: fewer than {MIN_WORDS_FOR_RATES}, "
                         "so the negation and identifier rates are not judged")
        elif model and model["words"] < MIN_WORDS_FOR_RATES:
            rates = False
            notes.append(f"the model text has {model['words']} words: fewer than {MIN_WORDS_FOR_RATES}, "
                         "so the negation and identifier rates are not judged")
        wps, rel = text["words_per_sentence"], text["relation_share"]
        neg, ident = text["negations_per_1000_words"], text["identifiers_per_100_words"]
        if model:
            m_wps, m_rel = model["words_per_sentence"], model["relation_share"]
            m_neg, m_ident = model["negations_per_1000_words"], model["identifiers_per_100_words"]
            if wps < 0.8 * m_wps:
                found.append(f"sentences are shorter than the model text ({wps} against {m_wps} words): "
                             "facts may stand alone")
            if wps > 1.25 * m_wps:
                found.append(f"sentences are longer than the model text ({wps} against {m_wps} words)")
            if rel < m_rel - 0.12:
                found.append("fewer sentences state a relation than in the model text "
                             f"({rel:.0%} against {m_rel:.0%})")
            if rates and neg > 1.5 * m_neg + 1:
                found.append(f"more negations than the model text ({neg} against {m_neg} per 1,000 words): "
                             "caveats may sit in the running text")
            if rates and ident > 1.5 * m_ident + 0.5:
                found.append(f"more identifiers than the model text ({ident} against {m_ident} per 100 words): "
                             "detail may belong in a table or a listing")
        else:
            if wps < 18:
                found.append(f"short sentences ({wps} words on average): facts may stand alone")
            if wps > 32:
                found.append(f"long sentences ({wps} words on average)")
            if rel < 0.25:
                found.append(f"few sentences state a relation ({rel:.0%})")
            if rates and neg > 12:
                found.append(f"many negations ({neg} per 1,000 words): caveats may sit in the running text")
            if rates and ident > 4:
                found.append(f"many identifiers ({ident} per 100 words): detail may belong in a table or a listing")
        if text["paragraphs"] >= MIN_PARAGRAPHS and text["words_per_paragraph"] < SHORT_PARAGRAPH:
            found.append(f"short paragraphs ({text['words_per_paragraph']} words on average): "
                         "claims may stand alone, one per paragraph")
    if text["sentences_over_45_words"]:
        found.append(f"{text['sentences_over_45_words']} sentence(s) above {LONG_SENTENCE} words")
    if argues and text["paragraphs_without_argues"]:
        found.append(f"{text['paragraphs_without_argues']} of {text['paragraphs']} paragraphs "
                     "carry no '% argues:' line")
    return found, notes


ROWS = (
    ("paragraphs", "paragraphs"),
    ("sentences", "sentences"),
    ("words", "words"),
    ("words_per_paragraph", "words per paragraph"),
    ("words_per_sentence", "words per sentence"),
    ("relation_share", "sentences that state a relation"),
    ("negations_per_1000_words", "negations per 1,000 words"),
    ("identifiers_per_100_words", "identifiers per 100 words"),
    ("sentences_over_45_words", "sentences above 45 words"),
    ("paragraphs_without_argues", "paragraphs without an argues line"),
)


def render(text: dict, model: dict | None, found: list[str], notes: list[str], look: list[dict]) -> str:
    def show(key: str, value) -> str:
        return f"{value:.0%}" if key == "relation_share" else str(value)

    lines = [f"{'':36}{'text':>10}" + (f"{'model':>10}" if model else "")]
    for key, label in ROWS:
        line = f"{label:36}{show(key, text[key]):>10}"
        if model:
            line += f"{'' if key == 'paragraphs_without_argues' else show(key, model[key]):>10}"
        lines.append(line)
    lines.append("")
    lines.append("Signals:" + ("" if found else " none. This proves nothing: read the text as its reader."))
    lines += [f"  - {signal}" for signal in found]
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines += [f"  - {note}" for note in notes]
    if look:
        lines.append("")
        lines.append("Look again:")
        lines += [f"  - {item['kind']} at {item['where']} ({item['why']}): {item['starts']}" for item in look]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Signals of inventory prose in article text.")
    parser.add_argument("files", nargs="+", help="section files of the text (.tex, .txt or .md)")
    parser.add_argument("--model", action="append", default=[],
                        help="a model text whose register the text should match (repeatable)")
    parser.add_argument("--argues", action="store_true",
                        help="also require a '%% argues:' line above every paragraph")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 when a signal is raised")
    args = parser.parse_args(argv)
    missing = [p for p in args.files + args.model if not Path(p).is_file()]
    if missing:
        print("prose_signals: no such file: " + ", ".join(missing), file=sys.stderr)
        return 2
    report = read(args.files)
    text = report.measure()
    model = read(args.model).measure() if args.model else None
    found, notes = signals(text, model, args.argues)
    look = report.look_again()
    if args.json:
        print(json.dumps({"text": text, "model": model, "signals": found, "notes": notes, "look_again": look},
                         indent=2, ensure_ascii=False))
    else:
        print(render(text, model, found, notes, look))
    return 1 if args.strict and found else 0


if __name__ == "__main__":
    sys.exit(main())
