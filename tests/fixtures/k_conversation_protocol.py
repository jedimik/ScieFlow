"""Inert offline plumbing only: no model, tools, discovery or workflow writes."""
import hashlib
import json
import sys

args = list(sys.argv[1:])
session_id = 'k-session-1'
if args[:1] == ['--resume']:
    session_id = args[1]
    args = args[2:]
assert args and args.pop(0) in ('first', 'resume')
assert len(args) <= 1
text = args[0] if args else sys.stdin.read()
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session_id}))
print(json.dumps({'type': 'result', 'session_id': session_id, 'result': 'K protocol reply',
                  'input_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}))
