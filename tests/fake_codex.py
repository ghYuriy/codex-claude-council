#!/usr/bin/env python3
"""Minimal local app-server double for the Council adapter; no model or network."""
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
import uuid

if sys.argv[1:] in (['--version'], ['app-server', '--help']):
    print('fake codex app-server'); sys.exit(0)

if os.environ.get('COUNCIL_NATIVE_LOG'):
    with open(os.environ['COUNCIL_NATIVE_LOG'], 'a') as out:
        # Environment names only: values may hold credentials.
        out.write(json.dumps({'launch': {'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env_names': sorted(os.environ)}}) + '\n')
if os.environ.get('COUNCIL_FAKE_PID_FILE'):
    Path(os.environ['COUNCIL_FAKE_PID_FILE']).write_text(str(os.getpid()))
if os.environ.get('COUNCIL_FAKE_MODE') == 'hang_ignore_term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(300)

def send(value):
    print(json.dumps(value), flush=True)


USAGE_FILE = os.environ.get('COUNCIL_FAKE_USAGE')
USAGE_MODE = os.environ.get('COUNCIL_FAKE_USAGE_MODE', 'normal')
TURN = {'totalTokens': 110, 'inputTokens': 100, 'cachedInputTokens': 40, 'outputTokens': 10, 'reasoningOutputTokens': 4}


def totals():
    return json.loads(Path(USAGE_FILE).read_text()) if USAGE_FILE and Path(USAGE_FILE).exists() else {}


def usage(thread_id, turn_id, total):
    send({'method': 'thread/tokenUsage/updated', 'params': {'threadId': thread_id, 'turnId': turn_id,
          'tokenUsage': {'total': total, 'last': TURN, 'modelContextWindow': 1000}}})


def replay(thread_id):
    # The installed app-server repeats the thread's last total when a thread is resumed, before any new turn.
    if USAGE_FILE and USAGE_MODE != 'no_replay':
        total = totals().get(thread_id, {f: 0 for f in TURN})
        if USAGE_MODE == 'reset':
            total = {f: v + 10000 for f, v in total.items()}
        if USAGE_MODE == 'replay_twice_lower':
            # Both above the thread's last total and below this turn's end, the second below the first.
            usage(thread_id, 'previous-turn', {f: v + 3 for f, v in total.items()})
            total = {f: v + 2 for f, v in total.items()}
        if USAGE_MODE == 'replay_low':
            total = {f: max(0, v - 1) for f, v in total.items()}  # Just below the thread's last total.
        usage(thread_id, 'previous-turn', total)


def turn_usage(thread_id, turn_id):
    if not USAGE_FILE:
        return
    saved = totals()
    total = {f: saved.get(thread_id, {}).get(f, 0) + TURN[f] for f in TURN}
    halfway = {f: saved.get(thread_id, {}).get(f, 0) + TURN[f] // 2 for f in TURN}
    if USAGE_MODE == 'other_thread':
        usage('another-thread', 'another-turn', {f: 999999 for f in TURN})
    if USAGE_MODE == 'mid_reset':
        usage(thread_id, turn_id, halfway); usage(thread_id, turn_id, {f: 0 for f in TURN})
    usage(thread_id, turn_id, total)
    if USAGE_MODE == 'duplicate':
        usage(thread_id, turn_id, total)
    if USAGE_MODE == 'late_repeat':
        usage(thread_id, turn_id, halfway)
    saved[thread_id] = total
    Path(USAGE_FILE).write_text(json.dumps(saved))
    if USAGE_MODE == 'then_hang':
        time.sleep(300)

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get('method')
    if method is None:
        continue
    params = msg.get('params', {})
    mode = os.environ.get('COUNCIL_FAKE_MODE', 'clear')
    if os.environ.get('COUNCIL_NATIVE_LOG'):
        with open(os.environ['COUNCIL_NATIVE_LOG'], 'a') as out:
            out.write(json.dumps(msg) + '\n')
    if 'id' not in msg:
        continue
    if method == 'initialize':
        result = {}
    elif method == 'thread/start':
        if mode in ('start_auth', 'start_transient'):
            send({'id': msg['id'], 'error': {'code': -32000, 'message': 'authentication failed' if mode == 'start_auth' else 'connection reset'}})
            continue
        result = {'thread': {'id': str(uuid.uuid4())}}
        if USAGE_FILE and USAGE_MODE == 'foreign_before_thread':
            usage('another-thread', 'another-turn', {f: 20 for f in TURN})
    elif method == 'thread/resume':
        if mode == 'resume_auth':
            send({'id': msg['id'], 'error': {'code': -32000, 'message': 'authentication failed'}})
            continue
        result = {'thread': {'id': str(uuid.uuid4()) if mode == 'wrong_native_session' else params['threadId']}}
        send({'id': msg['id'], 'result': result})
        replay(result['thread']['id'])
        continue
    elif method == 'thread/name/set':
        result = {}
    elif method == 'turn/start':
        if mode == 'turn_network':
            send({'id': msg['id'], 'error': {'code': -32000, 'message': 'connection reset'}})
            continue
        request = json.loads(params['input'][0]['text'])
        if mode == 'no_turn_complete':
            send({'id': msg['id'], 'result': {'turn': {'id': str(uuid.uuid4()), 'status': 'inProgress'}}})
            continue
        thread_id = params['threadId']; turn_id = str(uuid.uuid4())
        send({'id': msg['id'], 'result': {'turn': {'id': turn_id, 'status': 'inProgress'}}})
        send({'method': 'turn/started', 'params': {'threadId': thread_id, 'turn': {'id': turn_id, 'status': 'inProgress'}}})
        if mode == 'native_denied':
            send({'id': 'approval', 'method': 'item/commandExecution/requestApproval', 'params': {}})
        if request['phase'] == 'REPAIR':
            file = request['plan']['files'][0]
            if mode != 'dispute_repair':
                Path(file).write_text('value = 1\n# repaired by delegated Codex\n')
            if mode in ('git_stage', 'git_stage_sleep'):
                subprocess.run(['git', 'add', file], check=True)
                if mode == 'git_stage_sleep':
                    time.sleep(300)
            if mode in ('outside_scope', 'outside_sleep'):
                Path('other.py').write_text('unrelated change\n')
            if mode == 'outside_sleep':
                time.sleep(300)
            report = {'task_id': request['task_id'], 'request_id': request['request_id'],
                      'summary': 'Assessed the cause and original requirement', 'changed_files': [] if mode == 'dispute_repair' else [file],
                      'checks_performed': [], 'unverified': [],
                      'issue_actions': [{'id': i, 'action': 'DISPUTED' if mode == 'dispute_repair' else 'FIXED_PENDING_CHECK', 'reason': 'Assessed against the original requirement'}
                                        for i in request['plan']['issue_ids']]}
            answer = json.dumps(report)
        else:
            env = dict(os.environ)
            # Forward the same report cases through the actual Node adapter.
            completed = subprocess.run([sys.executable, str(Path(__file__).with_name('fake_claude.py')),
                                       '--session-id', thread_id], input=json.dumps(request),
                                       capture_output=True, text=True, env=env)
            if completed.returncode:
                send({'method': 'turn/completed', 'params': {'threadId': thread_id,
                      'turn': {'id': turn_id, 'status': 'failed'}}})
                continue
            try:
                envelope = json.loads(completed.stdout)
                if envelope.get('is_error') or envelope.get('permission_denials') or envelope.get('type') != 'result':
                    raise ValueError('Incomplete fake result')
                answer = json.dumps(envelope['structured_output']) if 'structured_output' in envelope else envelope.get('result', '')
            except ValueError:
                answer = 'incomplete result'
        turn_usage(thread_id, turn_id)
        send({'method': 'item/completed', 'params': {'threadId': thread_id, 'turnId': turn_id,
              'item': {'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer', 'text': answer}}})
        send({'method': 'turn/completed', 'params': {'threadId': thread_id,
              'turn': {'id': turn_id, 'status': 'completed'}}})
        continue
    else:
        send({'id': msg['id'], 'error': {'code': -32601, 'message': 'Unsupported method'}})
        continue
    send({'id': msg['id'], 'result': result})
