#!/usr/bin/env python3
"""Minimal local app-server double for the Council adapter; no model or network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

if sys.argv[1:] in (['--version'], ['app-server', '--help']):
    print('fake codex app-server'); sys.exit(0)

def send(value):
    print(json.dumps(value), flush=True)

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
        result = {'thread': {'id': str(uuid.uuid4())}}
    elif method == 'thread/resume':
        result = {'thread': {'id': str(uuid.uuid4()) if mode == 'wrong_native_session' else params['threadId']}}
    elif method == 'thread/name/set':
        result = {}
    elif method == 'turn/start':
        request = json.loads(params['input'][0]['text'])
        thread_id = params['threadId']; turn_id = str(uuid.uuid4())
        send({'id': msg['id'], 'result': {'turn': {'id': turn_id, 'status': 'inProgress'}}})
        if mode == 'native_denied':
            send({'id': 'approval', 'method': 'item/commandExecution/requestApproval', 'params': {}})
        if request['phase'] == 'REPAIR':
            file = request['plan']['files'][0]
            if mode != 'dispute_repair':
                Path(file).write_text('value = 1\n# repaired by delegated Codex\n')
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
        send({'method': 'item/completed', 'params': {'threadId': thread_id, 'turnId': turn_id,
              'item': {'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer', 'text': answer}}})
        send({'method': 'turn/completed', 'params': {'threadId': thread_id,
              'turn': {'id': turn_id, 'status': 'completed'}}})
        continue
    else:
        send({'id': msg['id'], 'error': {'code': -32601, 'message': 'Unsupported method'}})
        continue
    send({'id': msg['id'], 'result': result})
