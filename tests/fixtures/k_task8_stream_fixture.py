"""Synthetic claude-stream-json emulator for offline K Task8 binding tests.

No model, no network, no claude binary. It only mimics the shape of a
stream-json run so the binding's parsing, interception and accounting paths
can be exercised. Offline GREEN does NOT prove the real CLI reports slash
interception or usage limits this way (plan section 5 residual).

Mode is read from the file named by --mode-file (production passes a fixed
environment, so an environment variable would not survive). Modes:
normal, skill (Skill scieflow-menu), other-skill (Skill workspace-sync),
hang (never finishes; killed by the production timeout), fast (finishes
at once, before any stop point), usage-limit (usage-limit result, exit 1), expand (CLI-written /scieflow-menu transcript pair, no stream Skill;
mode file line 2 is the skill dir).

R14-4 modes (synthetic text only, never the real AGENTS.md or a real stream):
big-tool-result is the normal output plus one type-first `user` tool_result record of at least 28,336 bytes
(its text is repeated in `message` and in a top-level `tool_use_result`, as Claude CLI 2.1.293 writes it).
big-assistant is the normal output plus one type-first `assistant` record over 16,384 bytes, which production
must still refuse with record-limit. Both extra records use compact separators with `type` as the first key,
the shape production's PF-6 rule matches at byte 0.
"""
import datetime
import json
import os
import sys
import time
import uuid

args = list(sys.argv[1:])
FULL_TOOLS = ['Bash', 'Edit', 'Glob', 'Grep', 'Read', 'Skill', 'Write']
tools = FULL_TOOLS  # R13-2: the init event reports the launch --tools set when given
mode = 'normal'
session_id = 'fixture-session-1'
model = None
disable_slash = False
prompt = None
mode_file = None
i = 0
while i < len(args):
    arg = args[i]
    if arg.startswith('--tools='):
        tools = arg.split('=', 1)[1].split(',')
        i += 1
        continue
    if arg == '--mode-file':
        mode_file = args[i + 1]
        i += 2
        continue
    if arg == '--resume':
        session_id = args[i + 1]
        i += 2
        continue
    if arg == '--model':
        model = args[i + 1]
        i += 2
        continue
    if arg == '--disable-slash-commands':
        disable_slash = True
    elif not arg.startswith('-') and i == len(args) - 1:
        prompt = arg
    i += 1

expand_skill_dir = None
if mode_file is not None:
    with open(mode_file, encoding='utf-8') as handle:
        mode_lines = handle.read().splitlines()
    mode = (mode_lines[0].strip() if mode_lines else '') or 'normal'
    if len(mode_lines) > 1:
        expand_skill_dir = mode_lines[1].strip()
if mode == 'expand' and '--resume' not in args:
    session_id = str(uuid.uuid4())  # R13-3: a real UUID, as the transcript path requires
if prompt is None:
    prompt = sys.stdin.read()

print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session_id, 'model': model,
                  'tools': tools}), flush=True)
if mode == 'hang':
    # Like the real CLI, the init line (session, model) is printed before the run stalls.
    time.sleep(600)

content = [{'type': 'text', 'text': 'fixture reply'}]
if mode == 'rate-talk':
    # Assistant prose that merely mentions a limit must not be read as a usage-limit pause (S12).
    content.append({'type': 'text', 'text': 'Background: a rate limit is a provider concept.'})
intercept_text = '/scieflow-menu' in prompt and not disable_slash
if mode == 'expand' and intercept_text and expand_skill_dir:
    # R13-3 fixture: the CLI itself expanded /scieflow-menu. No stream-json Skill tool_use; only the transcript pair.
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    pair = [
        {'type': 'user', 'sessionId': session_id, 'timestamp': now, 'promptId': 'fixture-prompt-1', 'isMeta': False,
         'message': {'role': 'user', 'content': '<command-name>/scieflow-menu</command-name>\n<command-message>scieflow-menu</command-message>'}},
        {'type': 'user', 'sessionId': session_id, 'timestamp': now, 'promptId': 'fixture-prompt-1', 'isMeta': True,
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Base directory for this skill: ' + expand_skill_dir + '\n\nfixture body'}]}},
    ]
    folder = os.path.join(os.environ['HOME'], '.claude', 'projects', '-synthetic-fixture-proj')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, session_id + '.jsonl'), 'a', encoding='utf-8') as handle:
        handle.write(''.join(json.dumps(record) + '\n' for record in pair))
if mode == 'skill' and intercept_text:
    content.insert(0, {'type': 'tool_use', 'id': 'toolu_1', 'name': 'Skill',
                       'input': {'skill': 'scieflow-menu'}})
if mode == 'other-skill' and intercept_text:
    content.insert(0, {'type': 'tool_use', 'id': 'toolu_2', 'name': 'Skill',
                       'input': {'skill': 'workspace-sync'}})
if mode in ('normal', 'big-tool-result', 'big-assistant') and intercept_text:
    content.insert(0, {'type': 'tool_use', 'id': 'toolu_3', 'name': 'Skill',
                       'input': {'skill': 'scieflow-menu'}})
print(json.dumps({'type': 'assistant', 'session_id': session_id,
                  'message': {'model': model, 'content': content}}), flush=True)
if mode == 'big-tool-result':
    # R14-4: 'synthetic\n' repeated 1320 times is 13,200 bytes; each newline is two bytes in JSON, so the
    # record is about 29 KB with the duplicate, above the 28,336-byte attempt-4 record.
    big = 'synthetic\n' * 1320
    record = {'type': 'user', 'session_id': session_id,
              'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_3',
                                                       'content': big}]},
              'tool_use_result': big}
    print(json.dumps(record, separators=(',', ':')), flush=True)
if mode == 'big-assistant':
    # R14-4: 1700 * 'synthetic\n' is 17,000 bytes of text, over the 16,384-byte record limit in JSON.
    record = {'type': 'assistant', 'session_id': session_id,
              'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'synthetic\n' * 1700}]}}
    print(json.dumps(record, separators=(',', ':')), flush=True)

if mode == 'usage-limit':
    print(json.dumps({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
                      'session_id': session_id, 'result': 'Claude AI usage limit reached|1760000000'}),
          flush=True)
    sys.exit(1)

print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                  'session_id': session_id, 'result': 'fixture reply',
                  'usage': {'input_tokens': 11, 'output_tokens': 7}, 'total_cost_usd': 0.0}),
      flush=True)
sys.exit(0)
