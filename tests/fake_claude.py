#!/usr/bin/env python3
"""Local subprocess double. Never uses a network or a real model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

prompt = json.loads(sys.stdin.read())
mode = os.environ.get('COUNCIL_FAKE_MODE', 'clear')
args = sys.argv[1:]
sid = args[args.index('--resume') + 1] if '--resume' in args else args[args.index('--session-id') + 1]
if mode in ('sleep', 'tree'):
    if mode == 'tree':
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])
        Path(os.environ['COUNCIL_CHILD_PID']).write_text(str(child.pid))
    time.sleep(300)
if mode == 'exit':
    sys.stderr.write('transport failure'); sys.exit(7)
if mode == 'network':
    sys.stderr.write('ECONNRESET'); sys.exit(1)
if mode == 'auth':
    sys.stderr.write('authentication_error: please login'); sys.exit(1)
if mode == 'quota':
    sys.stderr.write('rate_limit_error: usage limit'); sys.exit(1)
if mode == 'badjson':
    print('{'); sys.exit(0)
if mode == 'missing':
    print(json.dumps({'type': 'assistant', 'message': 'done'})); sys.exit(0)
if mode == 'maxturns':
    print(json.dumps({'type': 'result', 'subtype': 'error_max_turns', 'is_error': True, 'session_id': sid})); sys.exit(0)
if mode == 'agree':
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': sid, 'result': 'AGREE'})); sys.exit(0)
if mode == 'mutate':
    Path(prompt['required_files'][0]).write_text('changed during review')
report = {
    'task_id': prompt['task_id'], 'request_id': prompt['request_id'],
    'phase': prompt['phase'], 'snapshot_id': prompt['code_snapshot_id'],
    'verdict': 'CLEAR', 'coverage': [
        {'requirement_id': r['id'], 'status': 'satisfied', 'explanation': 'Inspected implementation and current evidence.',
         'evidence': r['files'] + ['check:' + c for c in r['checks'] if c in prompt['checks']]}
        for r in prompt['requirements']],
    'inspected_files': prompt['required_files'], 'actions': ['Read required files and evidence'],
    'limitations': [], 'unverified': [], 'issues': [], 'closed_issues': []}
if mode == 'incomplete': report['coverage'] = []
if mode == 'wrong_snapshot': report['snapshot_id'] = 'wrong'
if mode == 'missing_artifact': report['coverage'][0]['evidence'] = ['absent.py']
if mode == 'unverified': report['unverified'] = ['integration untested']
if mode == 'needs_decision': report['verdict'] = 'NEEDS_DECISION'
if mode == 'needs_context': report['verdict'] = 'NEEDS_CONTEXT'
if mode == 'changes_without_issue': report['verdict'] = 'CHANGES_REQUIRED'
if mode in ('issue', 'issue_renamed'):
    report['verdict'] = 'CHANGES_REQUIRED'
    report['coverage'][0]['status'] = 'violated'
    report['issues'] = [{
        'id': 'I-001' if mode == 'issue' else 'I-900', 'requirement_ids': [prompt['requirements'][0]['id']],
        'category': 'correctness', 'severity': 'high', 'claim': 'Boundary input fails',
        'location': prompt['required_files'][0], 'snapshot_id': prompt['code_snapshot_id'],
        'evidence': [prompt['required_files'][0]], 'failure_scenario': 'Empty input',
        'suggested_check': 'Exercise empty input', 'recommendation': 'Repair underlying boundary condition',
        'uncertainties': [], 'status': 'OPEN', 'root_cause': 'empty-input'}]
if mode in ('close', 'reject', 'close_history', 'old_only'):
    report['closed_issues'] = [{'id': i['id'], 'status': 'REJECTED_WITH_EVIDENCE' if mode == 'reject' else 'VERIFIED_FIXED',
                                'evidence': ['check:' + prompt['requirements'][0]['checks'][0]], 'reason': 'Verified distinguishing case'}
                               for i in prompt['issues']]
if mode in ('close_history','old_only'):
    state=json.loads((Path(prompt['evidence_root'])/'state.json').read_text())
    old=state['runs'][0]['snapshot_id']
    old_manifest='evidence/snapshot-'+old+'/manifest.json'
    old_check=next(e['detail'] for e in state['events'] if e['event']=='check')
    report['inspected_files'].extend([old_manifest,old_check['stderr']])
    report['coverage'][0]['evidence'].append(old_manifest)
    report['closed_issues'][0]['evidence'].append(old_manifest)
    if mode == 'old_only': report['coverage'][0]['evidence']=[old_manifest,old_check['stderr']]
if mode == 'wrong_session': sid = '00000000-0000-4000-8000-000000000000'
if mode in ('rich_evidence','bad_lines','bad_artifact'):
    report['coverage'][0]['evidence'].append(prompt['required_files'][0] + (':999' if mode == 'bad_lines' else ':1'))
    report['inspected_files'].extend([c['stdout'] for c in prompt['checks'].values()])
    snapshot='evidence/snapshot-' + prompt['code_snapshot_id'] + '/'
    report['inspected_files'].extend([snapshot+'manifest.json',snapshot+'diff.patch'])
    if mode == 'bad_artifact': report['inspected_files'].append('evidence/nonexistent.txt')
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': sid,
                  'permission_denials': [{'tool': 'Read'}] if mode == 'denied' else [],
                  'structured_output': report, 'result': ''}))
