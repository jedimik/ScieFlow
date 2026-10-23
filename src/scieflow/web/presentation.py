"""Selected web presentation copies; none of these strings are authority.

Upstream service YAML/JSON materialization remains preexisting and unbounded;
these panel caps are not a storage/parser/capture limit.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlencode

from scieflow.core.turn_output import display_chunks, display_text


def text(value):
    return display_text(value if type(value) is str else str(value)).text


def sanitized(value):
    if isinstance(value, str):
        return text(str(value))
    if isinstance(value, dict):
        return {key: sanitized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitized(item) for item in value)
    return value


def argv_panel(arguments):
    def chunks():
        for index, argument in enumerate(arguments):
            if index:
                yield b' '
            for start in range(0, len(argument), 8192):
                yield argument[start:start + 8192].encode('utf-8', 'backslashreplace')
    return display_chunks(chunks()).text


def raw_url(slug, relative):
    return '/runs/' + quote(slug, safe='') + '/file?' + urlencode({'path': relative, 'download': 'true'}, quote_via=quote)


def verified_raw_url(ws: Path, slug: str, relative: str):
    from scieflow.web.files import acquire_owned

    try:
        _, owner = acquire_owned(ws, relative)
    except (ValueError, FileNotFoundError):
        return None
    owner.close()
    return raw_url(slug, relative)


def conversation_view(doc):
    result = sanitized(doc)
    for turn, raw in zip(result.get('turns', []), doc.get('turns', [])):
        if raw.get('audit'):
            turn['audit_display'] = text(raw['audit'])
    return result
