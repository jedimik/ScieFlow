"""K-only bounded Claude parsing, exact raw copying and shared plain display.

No process/session state mutation, old full-string parser, unchecked path,
reopening, retry or raw-copy total ceiling lives here. Sanitization is solely
for presentation; protocol parsing and raw artifact bytes remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from scieflow.core.turn_artifacts import ArtifactError, ExclusiveArtifact, RegularArtifact, _WriteFailure

CHUNK_BYTES = 65536
RECORD_BYTES = 16384
PARSE_BYTES = 2097152
TYPE_PREFIX_BYTES = 64
_JSON_DEPTH = 128


@dataclass(frozen=True)
class BoundedSession:
    id: str | None
    text: str
    complete: bool
    reason: str


@dataclass(frozen=True)
class RawCopyResult:
    bytes_copied: int
    complete: bool
    reason: str


@dataclass(frozen=True)
class _Overflow:
    """An oversize record: only its first TYPE_PREFIX_BYTES bytes survive."""
    prefix: bytes


_OVERFLOW_TYPE = re.compile(rb'\{"type":"([A-Za-z0-9_]{1,32})"[,}]')


class _Records:
    """Bytes first: bounded whole record, discard overflow through delimiter."""
    def __init__(self):
        self._pending = bytearray()
        self._discard = False
        self._prefix = b''

    def _append(self, piece):
        self._pending.extend(piece)

    def _start_discard(self, chunk, start):
        # PF-6: the prefix is the record's first bytes, even when they straddle
        # the previous chunk boundary (short _pending) or lie in this piece.
        head = bytes(self._pending[:TYPE_PREFIX_BYTES])
        piece = bytes(chunk[start:start + TYPE_PREFIX_BYTES])
        self._prefix = head + piece[:TYPE_PREFIX_BYTES - len(head)]
        self._pending.clear()
        self._discard = True

    def _take(self):
        if self._discard:
            value = _Overflow(self._prefix)
        else:
            value = bytes(self._pending)
        self._pending.clear()
        self._discard = False
        self._prefix = b''
        return value

    def feed(self, chunk, *, final=False):
        start = 0
        while start < len(chunk):
            newline = chunk.find(b'\n', start)
            end = len(chunk) if newline < 0 else newline + 1
            if not self._discard:
                if len(self._pending) + end - start > RECORD_BYTES:
                    self._start_discard(chunk, start)
                else:
                    self._append(chunk[start:end])
            if newline >= 0:
                yield self._take()
            start = end
        if final and (self._pending or self._discard):
            yield self._take()


def _handle(handle):
    if type(handle) is not RegularArtifact:
        raise ValueError('owned regular artifact required')


def _pairs(pairs):
    event = {}
    for key, value in pairs:
        if key in event:
            raise ValueError('ambiguous-json')
        event[key] = value
    return event


def _nonstandard_constant(value):
    raise ValueError('nonstandard-json')


def _decode_record(text):
    # Bound decoder nesting independently of a host/pytest recursion setting.
    # JSON strings' bracket/escape bytes are not structural nesting.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '{[':
            depth += 1
            if depth > _JSON_DEPTH:
                raise ValueError('deep-json')
        elif char in '}]':
            depth -= 1
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonstandard_constant)


class _Claude:
    """Only response text and one consistent id, never whole event retention."""
    def __init__(self, reason):
        self.session_id, self.result = None, None
        self.blocks, self.reason = [], reason

    def fail(self, reason):
        self.reason = self.reason or reason

    def accept(self, raw):
        if type(raw) is _Overflow:
            # PF-6, human decision approvals/k-task8-attempt4-decision-human-20261021.md item 1: an oversize
            # record whose first key is a type other than system/assistant/result is skipped. The decoder
            # rejects duplicate keys, so no such record can be an accepted system/assistant/result record.
            # Only the compact type-first prefix is tested; the prefix is never decoded or used as data.
            match = _OVERFLOW_TYPE.match(raw.prefix)
            if match and match.group(1).decode('ascii') not in ('system', 'assistant', 'result'):
                return
            self.fail('record-limit')
            return
        try:
            text = raw.decode('utf-8').strip()
        except UnicodeError:
            self.fail('invalid-utf8')
            return
        if not text.startswith(('{', '[')):
            return  # bounded, valid diagnostic noise, never raw fallback
        try:
            event = _decode_record(text)
        except (ValueError, RecursionError):
            self.fail('invalid-json')
            return
        if type(event) is not dict:
            self.fail('invalid-record')
            return
        kind = event.get('type')
        if kind not in ('system', 'assistant', 'result'):
            return
        if 'session_id' in event:
            session_id = event['session_id']
            if type(session_id) is not str:
                self.fail('invalid-session')
            elif not session_id:
                self.fail('missing-session')
            elif self.session_id is not None and self.session_id != session_id:
                self.fail('contradictory-session')
            else:
                self.session_id = session_id
        if kind == 'result' and 'result' in event:
            result = event['result']
            if type(result) is not str:
                self.fail('invalid-field')
            elif result:
                self.result = result
        elif kind == 'assistant':
            message = event.get('message')
            if type(message) is not dict or type(message.get('content', [])) is not list:
                self.fail('invalid-field')
                return
            for block in message.get('content', []):
                if type(block) is not dict:
                    self.fail('invalid-field')
                elif block.get('type') == 'text':
                    if type(block.get('text')) is not str:
                        self.fail('invalid-field')
                    elif block['text'].strip():
                        self.blocks.append(block['text'])

    def finish(self):
        text = self.result if self.result is not None else '\n\n'.join(self.blocks)
        self.fail('' if self.session_id else 'missing-session')
        return BoundedSession(None if self.reason else self.session_id, text,
                              not bool(self.reason), self.reason)


def read_completed_turn(handle: RegularArtifact, family: str) -> BoundedSession:
    _handle(handle)
    if family != 'claude':
        return BoundedSession(None, '', False, 'unsupported-family')
    records, offset = _Records(), 0
    parsed = _Claude('parse-limit' if handle.snapshot_size > PARSE_BYTES else '')
    try:
        handle.verify_unchanged()
        limit = min(handle.snapshot_size, PARSE_BYTES)
        while offset < limit:
            requested = min(CHUNK_BYTES, limit - offset)
            chunk = handle.read_at(offset, requested)
            if len(chunk) != requested:
                parsed.fail('source-short-read')
                break
            offset += len(chunk)
            for raw in records.feed(chunk, final=offset == handle.snapshot_size):
                parsed.accept(raw)
        handle.verify_unchanged()
    except (ArtifactError, OSError):
        parsed.reason = 'source-invalidated'
    return parsed.finish()


def copy_raw_log(handle: RegularArtifact, exclusive_target: ExclusiveArtifact, *,
                 chunk_bytes: int = CHUNK_BYTES) -> RawCopyResult:
    _handle(handle)
    if type(exclusive_target) is not ExclusiveArtifact:
        raise ValueError('owned exclusive artifact required')
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= CHUNK_BYTES:
        raise ValueError('invalid bounded copy chunk')
    copied = 0
    try:
        handle.verify_unchanged()
        if not exclusive_target._begin_copy():
            return RawCopyResult(0, False, 'target-not-fresh')
        while copied < handle.snapshot_size:
            requested = min(chunk_bytes, handle.snapshot_size - copied)
            chunk = handle.read_at(copied, requested)
            if len(chunk) != requested:
                return RawCopyResult(copied, False, 'source-short-read')
            try:
                count = exclusive_target.write(chunk)
            except _WriteFailure as exc:
                return RawCopyResult(copied + exc.bytes_written, False, 'copy-invalidated')
            copied += count
            if count != len(chunk):
                return RawCopyResult(copied, False, 'target-short-write')
            handle.verify_unchanged()
            exclusive_target._verify_binding()
        handle.verify_unchanged()
        exclusive_target.verify_owned()
        # Source may drift DURING target integrity consumption. Neither facts
        # snapshot may be adopted into a new copy baseline.
        handle.verify_unchanged()
        exclusive_target._verify_binding()
    except (ArtifactError, OSError):
        return RawCopyResult(copied, False, 'copy-invalidated')
    return RawCopyResult(copied, True, '')


DISPLAY_UNITS = 200000


@dataclass(frozen=True)
class DisplayPanel:
    text: str
    truncated: bool
    reason: str


class DisplaySanitizer:
    """Incremental plain text with bounded decoder and escape-discard state.

    Known escape payload is consumed, not retained. Only a potential single
    ESC is kept alongside the decoder's at-most-three undecided UTF-8 bytes.
    A saturating raw-length counter bounds an escape independently of feeds.
    """
    def __init__(self):
        import codecs
        # surrogateescape preserves the raw width of malformed bytes inside
        # discarded escapes. These internal markers are NEVER returned.
        self._decoder = codecs.getincrementaldecoder('utf-8')('surrogateescape')
        self._pending = b''
        self._state = 'text'
        self._escape_bytes = 0
        self._discard = False
        self._reason = ''
        self._final = False

    @property
    def reason(self):
        return self._reason

    @property
    def pending_bytes(self):
        return len(self._pending) + len(self._decoder.getstate()[0])

    def _end_escape(self):
        self._state, self._pending = 'text', b''
        self._escape_bytes, self._discard = 0, False

    def _count_escape(self, char, result):
        self._escape_bytes = min(RECORD_BYTES + 1,
                                 self._escape_bytes + len(char.encode('utf-8', 'surrogateescape')))
        if self._escape_bytes > RECORD_BYTES and not self._discard:
            self._discard = True
            self._reason = 'escape-limit'
            result.append('[Escape omitted: escape-limit]')

    def _text(self, char, result):
        code = ord(char)
        if char == '\x1b':
            self._state, self._pending, self._escape_bytes = 'esc', b'\x1b', 1
        elif char in ('\x9b', '\x9d'):
            self._state = 'csi' if char == '\x9b' else 'osc'
            self._escape_bytes = 2
        elif 0xdc80 <= code <= 0xdcff:
            result.append('�')
        elif code < 32 and char not in ('\n', '\t') or 0x7f <= code <= 0x9f:
            result.append(f'\\x{code:02x}')
        elif code in (0x061c, 0x200e, 0x200f) or 0x202a <= code <= 0x202e or 0x2066 <= code <= 0x2069:
            result.append(f'\\u{code:04x}')
        else:
            result.append(char)

    def feed(self, data: bytes, final=False) -> str:
        if type(data) is not bytes or len(data) > CHUNK_BYTES:
            raise ValueError('bounded bytes feed required')
        if self._final:
            if data:
                raise ValueError('display sanitizer finalized')
            return ''
        result = []
        for char in self._decoder.decode(data, final=final):
            if self._state == 'text':
                self._text(char, result)
            elif self._state == 'esc':
                if char in ('[', ']'):
                    self._state = 'csi' if char == '[' else 'osc'
                    self._pending = b''
                    self._count_escape(char, result)
                else:
                    result.append('\\x1b')
                    self._end_escape()
                    self._text(char, result)
            elif self._state == 'csi':
                self._count_escape(char, result)
                if '@' <= char <= '~':
                    self._end_escape()
            else:  # OSC, including a possible ESC part of the ST terminator
                self._count_escape(char, result)
                if char in ('\x07', '\x9c') or self._pending and char == '\\':
                    self._end_escape()
                else:
                    self._pending = b'\x1b' if char == '\x1b' else b''
        if final:
            if self._state != 'text':
                if not self._discard:
                    self._reason = self._reason or 'unterminated-escape'
                    result.append('\\x1b' if self._state == 'esc' else
                                  '[Escape omitted: unterminated-escape]')
                self._end_escape()
            self._final = True
        # At most six output characters per decoded input scalar, plus one
        # fixed notice per overlong escape; no retained output across calls.
        return ''.join(result)


def _display_units(text):
    return sum(2 if ord(char) > 0xffff else 1 for char in text)


def _display_prefix(text, units):
    consumed = 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xffff else 1
        if consumed + width > units:
            return text[:index]
        consumed += width
    return text


class _DisplayPrefix:
    """Retain only a finite scalar-safe prefix; reserve notices on failure."""
    def __init__(self):
        self.parts, self.units, self.exhausted = [], 0, False

    def accept(self, text):
        if self.exhausted or not text:
            return
        remaining = DISPLAY_UNITS - self.units
        width = _display_units(text)
        if width > remaining:
            text = _display_prefix(text, remaining)
            self.exhausted = True
        self.parts.append(text)
        self.units += _display_units(text)

    def finish(self, reason):
        text = ''.join(self.parts)
        if reason:
            notice = f'\n[Display truncated: {reason}; raw artifact retained]'
            text = _display_prefix(text, DISPLAY_UNITS - _display_units(notice)) + notice
        return DisplayPanel(text, bool(reason), reason)


def _display_chunks(chunks):
    sanitizer, prefix = DisplaySanitizer(), _DisplayPrefix()
    lookahead = False
    for chunk, at_end in chunks:
        prefix.accept(sanitizer.feed(chunk))
        if prefix.exhausted:
            return prefix.finish(sanitizer.reason or 'display-limit')
        if lookahead and not at_end:
            # One bounded lookahead permits a final stripped escape without
            # scanning arbitrary additional zero-output input after the cap.
            return prefix.finish(sanitizer.reason or 'display-limit')
        lookahead = prefix.units == DISPLAY_UNITS
    prefix.accept(sanitizer.feed(b'', final=True))
    return prefix.finish(sanitizer.reason or ('display-limit' if prefix.exhausted else ''))


def display_text(text: str) -> DisplayPanel:
    """Already decoded audit text, encoded only in bounded scalar slices."""
    if type(text) is not str:
        raise ValueError('display string required')
    def chunks():
        for start in range(0, len(text), 8192):
            # Even all isolated surrogates expand to at most 49152 bytes.
            yield text[start:start + 8192].encode('utf-8', 'backslashreplace'), start + 8192 >= len(text)
    return _display_chunks(chunks())


def display_panel(handle: RegularArtifact) -> DisplayPanel:
    """Consume a finite prefix on the caller-owned original artifact handle."""
    _handle(handle)
    reason = ''
    def chunks():
        nonlocal reason
        offset, extent = 0, handle.snapshot_size
        while offset < extent:
            requested = min(CHUNK_BYTES, extent - offset)
            chunk = handle.read_at(offset, requested)
            if len(chunk) != requested:
                reason = 'source-short-read'
                yield chunk, True
                return
            offset += len(chunk)
            yield chunk, offset == extent
    panel = DisplayPanel('', False, '')
    try:
        handle.verify_unchanged()
        panel = _display_chunks(chunks())
    except (ArtifactError, OSError):
        reason = 'source-invalidated'
    try:
        handle.verify_unchanged()
    except (ArtifactError, OSError):
        reason = 'source-invalidated'
    if reason:
        prefix = _DisplayPrefix()
        prefix.accept(panel.text)
        return prefix.finish(reason)
    return panel


def display_chunks(chunks) -> DisplayPanel:
    """Lazy public adapter for bounded raw chunks; no whole-input join."""
    return _display_chunks((chunk, False) for chunk in chunks)
