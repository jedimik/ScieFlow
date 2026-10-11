"""K-only bounded Claude parsing and byte-exact finite raw copying.

No process/session state mutation, old full-string parser, unchecked path,
reopening, retry, sanitation or raw-copy total ceiling lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from scieflow.core.turn_artifacts import ArtifactError, ExclusiveArtifact, RegularArtifact, _WriteFailure

CHUNK_BYTES = 65536
RECORD_BYTES = 16384
PARSE_BYTES = 2097152
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


class _Records:
    """Bytes first: bounded whole record, discard overflow through delimiter."""
    def __init__(self):
        self._pending = bytearray()
        self._discard = False

    def _append(self, piece):
        self._pending.extend(piece)

    def feed(self, chunk, *, final=False):
        start = 0
        while start < len(chunk):
            newline = chunk.find(b'\n', start)
            end = len(chunk) if newline < 0 else newline + 1
            if not self._discard:
                if len(self._pending) + end - start > RECORD_BYTES:
                    self._pending.clear()
                    self._discard = True
                else:
                    self._append(chunk[start:end])
            if newline >= 0:
                value = None if self._discard else bytes(self._pending)
                self._pending.clear()
                self._discard = False
                yield value
            start = end
        if final and (self._pending or self._discard):
            value = None if self._discard else bytes(self._pending)
            self._pending.clear()
            self._discard = False
            yield value


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
        if raw is None:
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
