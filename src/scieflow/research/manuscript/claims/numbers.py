"""Checkable numbers of a claim (M-G 5.5, decision M-G-D5): tokens, relations and exclusions.

`extract(text)` reads the number tokens of a claim's text (comments already removed) and returns one dict
per checkable number, in text order:

  raw       the token as written (sign kept when it is a minus or plus sign, separators kept)
  value     the decimal string: separators removed, U+2212 read as '-', never a float
  decimals  the digits after the decimal point, as written
  percent   True when the token is followed by a percent sign
  relation  '=', '<', '<=', '>' or '>=' (the operator directly before the token)

Not claims: numbers inside citation, reference, label, URL, graphics and display-maths commands, inside
reference markers, years (1900-2099 without a percent sign), ordinals and tokens glued to letters
(H2O, SF-36, 1.2e-05), and tokens after words such as Figure, Table, Section, Eq. or version.
"""

from __future__ import annotations

import re
from decimal import Decimal

PREFIX_WINDOW = 40

# Masked by spaces of the same length, so the positions of the remaining tokens are preserved.
MASKS = (
    re.compile(r"\\begin\{(?P<d>equation|align|gather|multline|eqnarray|displaymath|flalign|alignat|math)"
               r"\*?\}.*?\\end\{(?P=d)\*?\}", re.S),
    re.compile(r"\$\$.*?\$\$", re.S),
    re.compile(r"\\\[.*?\\\]", re.S),
    re.compile(r"\[(?:data|run|doi):[^\]]*\]"),
    re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{[^{}]*\}"),
    re.compile(r"\\(?:c|C)?ref\*?\{[^{}]*\}"),
    re.compile(r"\\(?:eqref|autoref|label|url)\*?\{[^{}]*\}"),
    re.compile(r"\\href\{[^{}]*\}"),
    re.compile(r"\\includegraphics\*?(?:\[[^\]]*\])?\{[^{}]*\}"),
    re.compile(r"\\documentclass(?:\[[^\]]*\])?\{[^{}]*\}"),
    re.compile(r"\\begin\{[^{}]*\}\[[^\]]*\]"),
)

TOKEN = re.compile(
    r"(?P<sign>[-\u2212+]?)"
    r"(?P<int>\d{1,3}(?:[,\u202f\u00a0 ]\d{3})+|\d+)"
    r"(?P<frac>\.\d+)?"
    r"(?P<pct>\s*\\?%)?"
)

# The operator directly before the token; longer alternatives first.
RELATION = re.compile(
    r"(<=|>=|\u2264|\u2265|\\leq|\\le|\\lt|\\geq|\\ge|\\gt|<|>)[\s$=]*$"
)
RELATION_MAP = {
    "<": "<", "\\lt": "<", "<=": "<=", "\u2264": "<=", "\\le": "<=", "\\leq": "<=",
    ">": ">", "\\gt": ">", ">=": ">=", "\u2265": ">=", "\\ge": ">=", "\\geq": ">=",
}

# Words after which a token is a label, not a measured number.
PREFIX = re.compile(
    r"(?<![A-Za-z])(?:figs?\.?|figures?|tables?|tab\.|sections?|sec\.|eqs?\.|equations?|"
    r"supplementary|appendix|chapters?|steps?|phases?|versions?|v)[\s~]*$",
    re.I,
)

SEPARATORS = ",\u202f\u00a0 "


def _mask(text: str) -> str:
    for pattern in MASKS:
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _value(sign: str, int_raw: str, frac: str) -> str:
    digits = "".join(ch for ch in int_raw if ch not in SEPARATORS)
    minus = "-" if sign in ("-", "\u2212") else ""
    return format(Decimal(minus + digits + frac), "f")


def extract(text: str) -> list[dict]:
    """The checkable numbers of `text`, in text order (M-G 5.5)."""
    masked = _mask(text)
    found = []
    for match in TOKEN.finditer(masked):
        start = match.start()
        # The start of the text counts as a space: a sign there is a sign (review r1 finding 3).
        lead = masked[start - 1] if start > 0 else " "
        if lead.isalpha():
            continue                                # glued to a word: H2O, SF-36, 1.2e-05
        if lead == "." or re.match(r"\.\d", masked[match.end():]):
            # Part of a decimal or dotted run without a whole part (".05", "1.2.3"): not read as a plain
            # integer, so a false C002 cannot follow (review r1 finding 4).
            continue
        sign = match.group("sign")
        counted_sign = bool(sign) and not (lead.isalnum() or lead in ")]}")
        if not counted_sign:
            start = match.start("int")              # a range dash or a sign after a word: not a sign
        end_token = match.end("frac") if match.group("frac") else match.end("int")
        after = masked[match.end()] if match.end() < len(masked) else ""
        if after.isalpha():
            continue                                # glued to a word after: 2nd, 1.2e
        int_raw = match.group("int")
        frac = match.group("frac") or ""
        percent = bool(match.group("pct"))
        if not frac and not percent and not counted_sign and re.fullmatch(r"\d{4}", int_raw) \
                and 1900 <= int(int_raw) <= 2099:
            continue                                # a year
        before = masked[max(0, start - PREFIX_WINDOW):start]
        if PREFIX.search(before):
            continue                                # Figure 3, Table 2, Eq. 7, v2, ...
        relation = "="
        operator = RELATION.search(before)
        if operator:
            relation = RELATION_MAP[operator.group(1)]
        found.append({
            "raw": text[start:end_token],
            "value": _value(sign if counted_sign else "", int_raw, frac),
            "decimals": len(frac) - 1 if frac else 0,
            "percent": percent,
            "relation": relation,
        })
    return found
