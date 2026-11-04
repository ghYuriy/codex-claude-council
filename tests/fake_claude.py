#!/usr/bin/env python3
"""Local subprocess double. Never uses a network or a real model."""
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

if sys.argv[1:] == ['auth', 'status'] and os.environ.get('COUNCIL_FAKE_SIGN_IN') == 'hang':
    time.sleep(300)  # The controller must never run such a command before a launch.
raw_stdin = sys.stdin.read()
prompt = json.loads(raw_stdin)
mode = os.environ.get('COUNCIL_FAKE_MODE', 'clear')
args = sys.argv[1:]
if os.environ.get('COUNCIL_CLAUDE_LOG'):
    with open(os.environ['COUNCIL_CLAUDE_LOG'], 'a') as out:
        # Environment names only: values may hold credentials.
        out.write(json.dumps({'argv': args, 'cwd': os.getcwd(), 'env_names': sorted(os.environ), 'stdin': raw_stdin}) + '\n')
if os.environ.get('COUNCIL_FAKE_PID_FILE'):
    Path(os.environ['COUNCIL_FAKE_PID_FILE']).write_text(str(os.getpid()))
if os.environ.get('COUNCIL_FAKE_ECHO_NAME'):
    sys.stderr.write('value seen: ' + os.environ.get(os.environ['COUNCIL_FAKE_ECHO_NAME'], '') + '\n')
if mode == 'hang_ignore_term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(300)
if mode == 'long_stderr':
    sys.stderr.write('\x1b[33m' + 'progress line\n' * 400 + '\x1b[0m\x1b[31mfatal: authentication expired; run login\x1b[0m\n')
    sys.exit(1)
if mode == 'bad_utf8_stdout':
    sys.stdout.flush(); sys.stdout.buffer.write(b'\xff\xfe partial \x80 output'); sys.stdout.flush()
    sys.exit(1)
if mode == 'bad_utf8_stderr':
    sys.stderr.flush(); sys.stderr.buffer.write(b'\xff\xfe\x80 broken \xc3( bytes\nfatal: network unreachable\n')
    sys.exit(1)
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
if mode == 'mutate_prompt':
    sent = Path(prompt['original_task_path']).parents[1] / 'reviews' / prompt['request_id'] / 'prompt.json'
    sent.write_text(sent.read_text().replace('"REVIEW"', '"DESIGN"', 1))
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
if mode in ('issue', 'issue_renamed', 'growth', 'growth3', 'issue_last', 'report_errors', 'feedback_issue'):
    report['verdict'] = 'CHANGES_REQUIRED'
    report['coverage'][0]['status'] = 'violated'
    report['issues'] = [{
        'id': 'I-900' if mode == 'issue_renamed' else 'I-001', 'requirement_ids': [prompt['requirements'][0]['id']],
        'category': 'correctness', 'severity': 'high', 'claim': 'Boundary input fails',
        'location': prompt['required_files'][0], 'snapshot_id': prompt['code_snapshot_id'],
        'evidence': [prompt['required_files'][0]], 'failure_scenario': 'Empty input',
        'suggested_check': 'Exercise empty input', 'recommendation': 'Repair underlying boundary condition',
        'uncertainties': [], 'status': 'OPEN', 'root_cause': 'empty-input'}]
    if mode in ('growth', 'growth3'):
        report['issues'].append(dict(report['issues'][0], id='I-002', root_cause='related-path', claim='Related path fails'))
    if mode == 'growth3':
        report['issues'].append(dict(report['issues'][0], id='I-003', root_cause='another-path', claim='Another preserved path fails'))
    if mode == 'issue_last':
        report['issues'][0]['requirement_ids'] = [prompt['requirements'][-1]['id']]
if mode == 'close_foreign':
    report['closed_issues'] = [{'id': 'I-001', 'status': 'VERIFIED_FIXED', 'evidence': ['check:unit'], 'reason': 'Attempted cross-block closure'}]
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
if mode == 'close_unrelated':
    report['closed_issues'] = [{'id': 'I-001', 'status': 'VERIFIED_FIXED', 'evidence': ['check:unit'], 'reason': 'Try to close an issue outside this question'}]
if mode == 'wrong_session': sid = '00000000-0000-4000-8000-000000000000'
if mode in ('rich_evidence','bad_lines','bad_artifact'):
    report['coverage'][0]['evidence'].append(prompt['required_files'][0] + (':999' if mode == 'bad_lines' else ':1'))
    report['inspected_files'].extend([c['stdout'] for c in prompt['checks'].values()])
    snapshot='evidence/snapshot-' + prompt['code_snapshot_id'] + '/'
    audit = 'evidence/' + Path(prompt['git_materials']).name + '/diff.patch' if prompt.get('git_materials') else snapshot+'diff.patch'
    report['inspected_files'].extend([snapshot+'manifest.json',audit])
    if mode == 'bad_artifact': report['inspected_files'].append('evidence/nonexistent.txt')
if mode == 'report_errors':
    report['inspected_files'] = ['abbreviated.py', '/tmp/unregistered-proof.txt']
    report['coverage'][0]['evidence'] = ['check:check-artifact-directory', 'app.py:999']
    report['issues'][0]['location'] = 'app.py:1 vs abbreviated.py:2'
if mode == 'feedback_issue':
    feedback = prompt['report_feedback']
    assert feedback['validation_errors'] and feedback['raw_response'].endswith('/stdout.json')
    copies = {feedback['raw_response']}
    copies.update(occurrence['raw_response'] for candidate in prompt.get('candidates', [])
                  for occurrence in candidate['occurrences'])
    report['inspected_files'].extend(sorted(copies))
    expected = [{'kind': 'check', 'path': '', 'check_id': 'unit', 'start_line': 0, 'end_line': 0, 'explanation': ''}] if prompt.get('report_format') == 2 else ['check:unit']
    assert prompt['check_references'] == expected
    assert prompt['issues'] == [], 'Rejected candidates must not become accepted issues'
if prompt.get('report_format') == 2:
    import re
    def reference(value):
        item = {'kind': 'file', 'path': value, 'check_id': '', 'start_line': 0, 'end_line': 0, 'explanation': ''}
        if value.startswith('check:'):
            item.update(kind='check', path='', check_id=value[6:])
        elif value not in prompt['required_files']:
            match = re.fullmatch(r'(.+):([1-9][0-9]*)(?:-([1-9][0-9]*))?', value)
            if match:
                item.update(path=match[1], start_line=int(match[2]), end_line=int(match[3] or match[2]))
        return item
    for field in ('coverage', 'issues', 'closed_issues'):
        for item in report[field]:
            item['evidence'] = [reference(ref) for ref in item['evidence']]
            if field == 'issues':
                item['location'] = reference(item['location'])
    if mode == 'feedback_issue':
        report['coverage'][0]['evidence'].append(prompt['check_references'][0])
    report['candidate_resolutions'] = []
    if mode in ('feedback_issue', 'dismiss_candidates'):
        report['candidate_resolutions'] = [
            {'candidate_id': candidate['id'], 'action': 'PROMOTE' if mode == 'feedback_issue' else 'WITHDRAW',
             'issue_id': 'I-001' if mode == 'feedback_issue' else '',
             'reason': 'The current fixture evidence supports this explicit disposition.',
             'evidence': [reference('check:' + prompt['requirements'][0]['checks'][0])]}
            for candidate in prompt.get('candidates', [])]
if os.environ.get('COUNCIL_FAKE_CORRUPT'):
    report['actions'].append('CORRUPTED-BYTE')
# Usage shaped as the installed CLI reports it: `usage` is this call, `modelUsage` the session so far.
USAGE = {'input_tokens': 3, 'cache_creation_input_tokens': 5, 'cache_read_input_tokens': 7, 'output_tokens': 11,
         'service_tier': 'standard'}
output = json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': sid,
                     'usage': USAGE, 'modelUsage': {'fake-model': {'inputTokens': 3, 'outputTokens': 11}},
                     'permission_denials': [{'tool': 'Read'}] if mode == 'denied' else [],
                     'structured_output': report, 'result': ''}) + '\n'
# An otherwise valid report with one byte that is not UTF-8.
sys.stdout.flush(); sys.stdout.buffer.write(output.encode().replace(b'CORRUPTED-BYTE', b'corrupted \xff byte')); sys.stdout.flush()
