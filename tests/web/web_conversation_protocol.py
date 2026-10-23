"""Inert web-local conversation protocol fixture; no core test imports.

Same inert wire shape as the accepted core k fixture: one session-id line,
one result line, nothing else — no model, tools, discovery or writes. An
optional leading ``--hold SECONDS`` sleeps before replying so a turn can
hold admission while a real socket probes health, SSE and cancellation.
"""
import hashlib
import json
import sys
import time

args = list(sys.argv[1:])
hold = 0.0
if args[:1] == ['--hold']:
    hold = float(args[1])
    args = args[2:]
session_id = 'web-protocol-session-1'
if args[:1] == ['--resume']:
    session_id = args[1]
    args = args[2:]
assert args and args.pop(0) in ('first', 'resume')
assert len(args) <= 1
text = args[0] if args else sys.stdin.read()
if hold:
    time.sleep(hold)
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session_id}))
print(json.dumps({'type': 'result', 'session_id': session_id, 'result': 'web protocol reply',
                  'input_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}))
