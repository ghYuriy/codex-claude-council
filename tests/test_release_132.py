"""Behavioural coverage for report contracts, preserved findings and reusable proof."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_controller as original


class Release132Test(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def request(self):
        r = self.f.state()['runs'][-1]
        return json.loads((self.f.task / r['directory'] / 'request.json').read_text())

    def report(self):
        r = self.f.state()['runs'][-1]
        return json.loads((self.f.task / r['directory'] / 'report.json').read_text())

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.f.project), *args], capture_output=True, text=True, check=True)

    def init_git(self):
        self.git('init', '-q')
        self.git('config', 'user.name', 'Council Tests')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'commit.gpgsign', 'false')
        hooks = self.f.base / 'empty-hooks'; hooks.mkdir(exist_ok=True)
        self.git('config', 'core.hooksPath', str(hooks))

    def new_task(self, **updates):
        spec = json.loads(self.f.spec.read_text())
        spec.update(task_key='declared-inputs', **updates)
        self.f.spec.write_text(json.dumps(spec))
        r = self.f.run_cmd('init', '--project', str(self.f.project), '--state-root', str(self.f.state_root),
                           '--intake', str(self.f.spec), '--claude', str(original.FAKE))
        self.f.task = Path(r.stdout.strip())

    def test_new_reports_use_structured_references_without_rewriting_raw_answer(self):
        self.f.check(); self.f.call()
        report = self.report()
        self.assertIsInstance(report['coverage'][0]['evidence'][0], dict)
        self.assertTrue(all(isinstance(ref, dict) and ref['kind']=='check' for ref in self.request()['check_references']))
        run = self.f.state()['runs'][-1]
        raw = json.loads((self.f.task / run['directory'] / 'stdout.json').read_text())
        self.assertEqual(report, raw['structured_output'])
        self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_git_add_and_commit_preserve_current_review_and_test_proof(self):
        self.init_git()
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        before = self.f.state()
        self.git('add', 'app.py'); self.git('commit', '-qm', 'Record identical content')
        status = json.loads(self.f.cmd('status').stdout)
        self.assertEqual(status['status'], 'PASSED', status)
        self.assertEqual(status['snapshot_id'], before['final_snapshot_id'])
        self.assertEqual(self.f.state()['checks'], before['checks'])
        self.assertEqual(self.f.state()['counters'], before['counters'])

    def test_git_bookkeeping_during_a_check_does_not_turn_success_into_failure(self):
        self.init_git()
        command = "import subprocess; subprocess.run(['git','add','app.py'],check=True); subprocess.run(['git','commit','-qm','Record content'],check=True)"
        self.f.cmd('check', '--id', 'unit', '--timeout', '5', '--', sys.executable, '-B', '-c', command)
        self.assertEqual(self.f.state()['checks']['unit']['result'], 'passed')

    def test_rejected_findings_are_visible_separately_then_promoted(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        state = self.f.state()
        self.assertEqual(state['issues'], [])
        self.assertEqual(len(state.get('candidates', [])), 1)
        candidate = state['candidates'][0]
        self.assertEqual(candidate['status'], 'PENDING')
        status = json.loads(self.f.cmd('status').stdout)
        self.assertEqual(len(status['pending_candidates']), 1)
        self.f.not_passed()
        self.f.call('feedback_issue')
        self.assertEqual(self.f.state()['candidates'][0]['status'], 'PROMOTED')
        self.assertEqual(self.f.state()['candidates'][0]['id'], candidate['id'])
        self.assertEqual(self.f.state()['issues'][0]['id'], 'I-001')

    def test_omitting_a_rejected_candidate_does_not_silently_clear_it(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        r = self.f.cmd('call', '--phase', 'REVIEW', '--stage', 'implementation', '--goal', 'Review implementation',
                       '--correction', 'Correct the rejected report without dropping findings', mode='clear', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('candidate', r.stderr.lower())
        self.f.not_passed()

    def test_unchanged_report_repair_reuses_evidence_but_source_change_requires_reassessment(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        self.f.call('report_errors', ok=False)
        self.assertEqual(self.request()['report_feedback']['mode'], 'correct_report')
        (self.f.project / 'app.py').write_text('value = 1\n# relevant new context\n')
        self.f.check()
        result = self.f.call('feedback_issue', goal='Reassess changed source', ok=False)
        self.assertIn('Final review allowance', result.stderr)
        self.assertEqual(self.f.state()['runs'][-1]['status'], 'COMPLETE')
        self.assertEqual(self.request()['report_feedback']['mode'], 'reassess')

    def test_declared_check_inputs_reuse_unaffected_proof_but_invalidate_changed_input(self):
        self.init_git()
        (self.f.project / 'unrelated.txt').write_text('one\n')
        self.new_task(check_exclusions={'unit': ['unrelated.txt']})
        self.f.check(); original_check = self.f.state()['checks']['unit']
        (self.f.project / 'unrelated.txt').write_text('two\n')
        self.f.call(); self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'], 'PASSED')
        self.assertEqual(self.f.state()['checks']['unit'], original_check)
        self.assertTrue(self.request()['check_freshness']['unit']['current'])
        self.assertEqual(self.request()['check_freshness']['unit']['excluded_paths'], ['unrelated.txt'])
        (self.f.project / 'app.py').write_text('value = 2\n')
        status = json.loads(self.f.cmd('status').stdout)
        self.assertEqual(status['status'], 'STALE')
        self.assertTrue(any('check' in f.lower() or 'input' in f.lower() for f in status['acceptance_failures']))

    def test_declared_input_directory_detects_new_and_deleted_files(self):
        (self.f.project / 'data').mkdir()
        (self.f.project / 'data/a.txt').write_text('a')
        self.init_git(); self.new_task(check_exclusions={'unit': ['unrelated.txt']})
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        (self.f.project / 'data/b.txt').write_text('b')
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['status'], 'STALE')
        (self.f.project / 'data/b.txt').unlink()
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['status'], 'PASSED')
        (self.f.project / 'data/a.txt').unlink()
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['status'], 'STALE')

    def test_check_exclusions_cannot_hide_a_declared_requirement_file(self):
        (self.f.project / 'unrelated.txt').write_text('one\n')
        spec = json.loads(self.f.spec.read_text())
        spec.update(task_key='bad-input-scope', check_exclusions={'unit': ['app.py']})
        self.f.spec.write_text(json.dumps(spec))
        r = self.f.run_cmd('init', '--project', str(self.f.project), '--state-root', str(self.f.state_root),
                           '--intake', str(self.f.spec), '--claude', str(original.FAKE), ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('app.py', r.stderr)


    def transport(self, body):
        wrapper = self.f.base / 'transport.py'
        wrapper.write_text('#!' + sys.executable + '\n' + body)
        wrapper.chmod(0o755)
        state = self.f.state()
        state['claude'] = str(wrapper)
        (self.f.task / 'state.json').write_text(json.dumps(state))

    def report_mutator(self, mutation, mode='issue'):
        body = "import json,os,subprocess,sys\nrequest=sys.stdin.read()\nprompt=json.loads(request)\nenv=dict(os.environ, COUNCIL_FAKE_MODE=" + repr(mode) + ")\np=subprocess.run([sys.executable," + repr(str(original.FAKE)) + ",*sys.argv[1:]],input=request,capture_output=True,text=True,env=env)\nenvelope=json.loads(p.stdout)\nreport=envelope['structured_output']\n" + mutation + "\nprint(json.dumps(envelope))\n"
        self.transport(body)

    def test_rejected_copy_cannot_be_the_only_evidence_of_current_coverage(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        self.report_mutator("report['coverage'][0]['evidence']=[dict(kind='file',path=prompt['report_feedback']['raw_response'],check_id='',start_line=0,end_line=0,explanation='Earlier answer')]", mode='feedback_issue')
        result = self.f.call(ok=False)
        self.assertIn('Historical comparison alone', result.stderr)
        self.f.not_passed()

    def test_tampered_rejected_copy_is_rejected_when_cited(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        self.report_mutator("from pathlib import Path\ncopy=Path(prompt['evidence_root']).parent/prompt['report_feedback']['raw_response']\ncopy.write_bytes(copy.read_bytes()+b'\\n')", mode='feedback_issue')
        result = self.f.call(ok=False)
        self.assertIn('Rejected response artifact was altered', result.stderr)
        self.f.not_passed()

    def test_unregistered_rejected_copy_is_not_trusted(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        self.report_mutator("report['inspected_files'].append('evidence/rejected-'+'0'*32+'/stdout.json')", mode='feedback_issue')
        result = self.f.call(ok=False)
        self.assertIn('Unknown rejected response', result.stderr)
        self.f.not_passed()

    def test_all_independent_semantic_failures_are_reported_together(self):
        self.report_mutator("report['coverage'][0]['explanation']=''\nreport['issues'][0]['snapshot_id']='wrong'\nreport['verdict']='CLEAR'\nreport['unverified']=['A mandatory observation is absent']")
        self.f.check(); result = self.f.call(ok=False)
        self.assertNotEqual(result.returncode, 0)
        for marker in ['explanation', 'snapshot', 'CLEAR']:
            self.assertIn(marker.lower(), result.stderr.lower())
        self.f.not_passed()

    def test_all_independent_shape_failures_are_reported_together(self):
        self.report_mutator("report.pop('actions')\nreport['coverage'][0]['status']='not-an-enum'\nreport['issues'][0]['id']=42")
        self.f.check(); result = self.f.call(ok=False)
        for marker in ['actions', 'coverage', 'issues']:
            self.assertIn(marker, result.stderr)
        self.f.not_passed()

    def startup_transport(self):
        native = self.f.base / 'native-session.json'
        body = """import json,os,subprocess,sys
from pathlib import Path
request=sys.stdin.read();p=json.loads(request);args=sys.argv[1:]
sid=args[args.index('--resume')+1] if '--resume' in args else args[args.index('--session-id')+1]
native=Path(NATIVE)
mode=os.environ.get('COUNCIL_FAKE_MODE')
if mode=='auth_before_start':
 print(json.dumps({'type':'result','subtype':'success','is_error':True,'session_id':sid,'result':'Not logged in · Please run /login','modelUsage':{},'usage':{'input_tokens':0,'output_tokens':0,'cache_creation_input_tokens':0,'cache_read_input_tokens':0},'num_turns':1,'terminal_reason':'api_error','permission_denials':[]}));sys.exit(1)
if mode=='uncertain_failure':
 print('authentication error',file=sys.stderr);sys.exit(1)
if '--resume' in args and not native.exists():
 print('No conversation found with session ID: '+sid,file=sys.stderr);sys.exit(1)
if '--session-id' in args and native.exists():
 print('Error: Session ID '+sid+' is already in use.',file=sys.stderr);sys.exit(1)
native.write_text(json.dumps({'session_id':sid}))
env=dict(os.environ,COUNCIL_FAKE_MODE='clear')
r=subprocess.run([sys.executable,FAKE,*args],input=request,capture_output=True,text=True,env=env)
sys.stdout.write(r.stdout);sys.stderr.write(r.stderr);sys.exit(r.returncode)
""".replace('NATIVE', repr(str(native))).replace('FAKE,', repr(str(original.FAKE)) + ',')
        self.transport(body)
        return native

    def test_affirmative_absent_session_recovery_preserves_uuid_history_and_budget(self):
        self.startup_transport(); self.f.check()
        self.f.call('auth_before_start', ok=False)
        original_id = self.f.state()['runs'][0]['session_id']
        self.assertFalse(self.f.state()['sessions']['REVIEW:implementation']['started'])
        self.f.call()
        state = self.f.state()
        self.assertEqual(state['counters']['launches'], 2)
        self.assertEqual(len(state['runs']), 2)
        self.assertEqual({r['session_id'] for r in state['runs']}, {original_id})
        self.assertIn('--session-id', state['runs'][-1]['argv'])
        self.assertEqual([r['status'] for r in state['runs']], ['INVALID', 'COMPLETE'])
        self.f.cmd('finalize'); self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_uncertain_session_failure_never_authorizes_a_new_conversation(self):
        self.startup_transport(); self.f.check()
        self.f.call('uncertain_failure', ok=False)
        self.f.call(goal='Inspect explicit resume after an uncertain failure', ok=False)
        self.assertTrue(self.f.state()['sessions']['REVIEW:implementation']['started'])
        self.f.call(goal='Preserve uncertain existing identity', ok=False)
        self.assertIn('--resume', self.f.state()['runs'][-1]['argv'])
        self.f.not_passed()

    def test_established_session_cannot_be_recreated_after_later_failure(self):
        native = self.startup_transport(); self.f.check(); self.f.call()
        self.f.call('auth_before_start', goal='A later unavailable provider', ok=False)
        native.unlink()
        self.f.call(goal='Inspect lost established session', ok=False)
        self.assertTrue(self.f.state()['sessions']['REVIEW:implementation']['started'])
        self.f.not_passed()

    def test_actual_file_name_with_colon_is_not_misread_as_a_line_number(self):
        name = 'notes:2'
        (self.f.project / name).write_text('A real project filename.\n')
        spec = json.loads(self.f.spec.read_text())
        spec['requirements'][0]['files'].append(name)
        self.new_task(scope=['app.py', name], requirements=spec['requirements'])
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'], 'PASSED')


    def test_first_review_correction_remains_independent_of_design_findings(self):
        self.f.check()
        self.f.cmd('call', '--phase', 'DESIGN', '--stage', 'implementation', '--goal', 'Assess design', mode='issue')
        self.f.call('report_errors', ok=False)
        self.f.call('feedback_issue')
        request = self.request()
        self.assertEqual(request['perspective'], 'independent')
        self.assertEqual(request['issues'], [])
        self.assertEqual(Path(request['evidence_root']).resolve(), (self.f.task / 'evidence').resolve())
        self.assertTrue(request['candidates'])
        self.assertTrue((self.f.task / request['report_feedback']['raw_response']).is_file())
        self.assertEqual(self.f.state()['issues'][0]['id'], 'I-001')

    def test_candidate_withdrawal_is_evidenced_and_visible_in_final_report(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        self.f.call('dismiss_candidates')
        self.f.cmd('finalize')
        state = self.f.state()
        self.assertEqual(state['status'], 'PASSED')
        self.assertEqual(state['issues'], [])
        self.assertEqual(state['candidates'][0]['status'], 'WITHDRAWN')
        final = (self.f.task / 'final.md').read_text()
        self.assertIn(state['candidates'][0]['id'], final)
        self.assertIn('WITHDRAWN', final)

    def test_report_feedback_survives_an_intervening_transport_failure(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        first = self.f.state()['runs'][0]['request_id']
        self.f.call('exit', ok=False)
        self.f.call('feedback_issue', goal='Resume after the unavailable transport is restored', ok=False)
        self.assertEqual(self.f.state()['runs'][-1]['status'], 'COMPLETE')
        self.assertEqual(self.request()['report_feedback']['request_id'], first)
        self.assertEqual(self.f.state()['candidates'][0]['status'], 'PROMOTED')

    def test_same_rejected_finding_has_one_candidate_with_all_occurrences(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        self.f.call('report_errors', ok=False)
        candidates = self.f.state()['candidates']
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(candidates[0]['occurrences']), 2)
        third = self.f.call('report_errors', ok=False)
        self.assertIn('Identical request', third.stderr)
        self.assertEqual(self.f.state()['counters']['launches'], 2)
        self.assertEqual(len(self.f.state()['candidates'][0]['occurrences']), 2)

    def test_zero_usage_auth_with_an_existing_empty_session_resumes_that_same_id(self):
        native = self.startup_transport(); self.f.check()
        self.f.call('auth_before_start', ok=False)
        sid = self.f.state()['runs'][0]['session_id']
        native.write_text(json.dumps({'session_id': sid}))
        self.f.call(ok=False)
        self.assertTrue(self.f.state()['sessions']['REVIEW:implementation']['started'])
        self.f.call()
        state = self.f.state()
        self.assertEqual(state['counters']['launches'], 3)
        self.assertEqual({r['session_id'] for r in state['runs']}, {sid})
        self.assertIn('--resume', state['runs'][-1]['argv'])
        self.f.cmd('finalize'); self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_tampered_startup_proof_cannot_authorize_another_launch(self):
        self.startup_transport(); self.f.check(); self.f.call('auth_before_start', ok=False)
        run = self.f.state()['runs'][0]
        (self.f.task / run['directory'] / 'stdout.json').write_text('{}')
        result = self.f.call(ok=False)
        self.assertIn('changed', result.stderr.lower())
        self.assertEqual(self.f.state()['counters']['launches'], 1)

    def test_unlisted_imported_module_remains_a_check_dependency(self):
        self.init_git()
        (self.f.project / 'helper.py').write_text('value = 1\n')
        (self.f.project / 'app.py').write_text('from helper import value\n')
        (self.f.project / 'README.md').write_text('Documentation\n')
        self.new_task(check_exclusions={'unit': ['README.md']})
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        (self.f.project / 'README.md').write_text('Updated documentation\n')
        status = json.loads(self.f.cmd('status').stdout)
        self.assertEqual(len(status['reused_checks']), 1)
        self.assertEqual(status['reused_checks'][0]['excluded_paths'], ['README.md'])
        self.assertEqual(status['status'], 'STALE')  # full review still covers the changed project
        (self.f.project / 'helper.py').write_text('value = 2\n')
        status = json.loads(self.f.cmd('status').stdout)
        self.assertEqual(status['reused_checks'], [])
        self.assertTrue(any('check inputs' in failure for failure in status['acceptance_failures']))

    def test_staging_a_deletion_does_not_change_the_already_observed_content(self):
        self.init_git()
        (self.f.project / 'obsolete.txt').write_text('unused\n')
        self.git('add', '.'); self.git('commit', '-qm', 'Baseline')
        (self.f.project / 'obsolete.txt').unlink()
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        before = json.loads(self.f.cmd('status').stdout)['snapshot_id']
        self.git('add', '-A'); self.git('commit', '-qm', 'Record deletion')
        status = json.loads(self.f.cmd('status').stdout)
        self.assertEqual(status['snapshot_id'], before)
        self.assertEqual(status['status'], 'PASSED')
        path = self.f.project / 'app.py'
        path.chmod(path.stat().st_mode | 0o111)
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['status'], 'STALE')

    def test_distinct_git_contexts_retain_the_original_diff_and_legacy_revalidation(self):
        self.init_git(); self.git('add', '.'); self.git('commit', '-qm', 'Baseline')
        (self.f.project / 'app.py').write_text('value = 1\n# reviewed change\n')
        self.f.check(); self.f.call('rich_evidence')
        first = self.f.state()['runs'][0]; old_diff = Path(self.request()['git_materials']) / 'diff.patch'
        original_bytes = old_diff.read_bytes(); self.assertTrue(original_bytes)
        self.git('add', '.'); self.git('commit', '-qm', 'Record reviewed content')
        self.f.call('rich_evidence', goal='Inspect retained audit identity')
        new_diff = Path(self.request()['git_materials']) / 'diff.patch'
        self.assertNotEqual(old_diff, new_diff)
        self.assertEqual(old_diff.read_bytes(), original_bytes)
        self.assertEqual(new_diff.read_bytes(), b'')
        import importlib.util
        spec = importlib.util.spec_from_file_location('retained_contract', original.SCRIPT)
        controller = importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)
        request = json.loads((self.f.task / first['directory'] / 'request.json').read_text())
        report = json.loads((self.f.task / first['directory'] / 'report.json').read_text())
        controller.validate_report(self.f.task, self.f.state(), report, request, controller.snapshot(self.f.state()))
        refused = self.f.cmd('revalidate', '--request', first['request_id'], '--reason', 'Old evidence must not supersede a later report', ok=False)
        self.assertIn('Only the latest invalid completed response', refused.stderr)
        self.f.cmd('finalize'); self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_ignored_generated_files_in_scoped_directory_do_not_stale_proof(self):
        self.init_git()
        (self.f.project / '.gitignore').write_text('__pycache__/\n')
        (self.f.project / 'src').mkdir()
        (self.f.project / 'src/a.py').write_text('value = 1\n')
        self.new_task(scope=['app.py', 'src'])
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        cache = self.f.project / 'src/__pycache__'; cache.mkdir()
        (cache / 'a.pyc').write_bytes(b'generated')
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['status'], 'PASSED')

    def test_structured_check_cannot_hide_an_extra_file_or_boolean_line_number(self):
        self.report_mutator("report['coverage'][0]['evidence'][0]={'kind':'check','path':'app.py','check_id':'unit','start_line':0,'end_line':0,'explanation':''}")
        self.f.check(); result = self.f.call(ok=False)
        self.assertIn('empty path', result.stderr)
        self.f.not_passed()

    def test_structured_references_and_candidates_round_trip_through_codex_adapter(self):
        import test_bidirectional
        reverse = test_bidirectional.BidirectionalTest(); reverse.f = self.f; reverse.reverse()
        self.f.check(); self.f.call('report_errors', ok=False)
        native = self.f.state()['sessions']['REVIEW:implementation']['native_id']
        self.f.call('feedback_issue')
        self.assertEqual(self.f.state()['sessions']['REVIEW:implementation']['native_id'], native)
        self.assertEqual(self.f.state()['candidates'][0]['status'], 'PROMOTED')
        self.assertEqual(self.report()['issues'][0]['location']['kind'], 'file')
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'FIXED_PENDING_CHECK', '--reason', 'Exercise the preserved fixture condition')
        self.f.check(); self.f.call('close', goal='Verify accepted finding through the reverse adapter')
        self.f.cmd('finalize'); self.assertEqual(self.f.state()['status'], 'PASSED')


    def test_shape_error_does_not_hide_other_safe_reference_and_semantic_errors(self):
        self.report_mutator("report.pop('actions')\nreport['issues'][0]['snapshot_id']='stale'\nreport['coverage'][0]['evidence'][0].update(start_line=999,end_line=999)")
        self.f.check(); result = self.f.call(ok=False)
        for marker in ['actions', 'snapshot_id', 'line range', '999']:
            self.assertIn(marker, result.stderr)
        self.f.not_passed()

    def test_candidate_promotion_preserves_other_independent_issue_with_same_source_id(self):
        self.f.check()
        self.f.cmd('call', '--phase', 'DESIGN', '--stage', 'implementation', '--goal', 'Prior independent design', mode='issue')
        original_issue = self.f.state()['issues'][0]
        self.report_mutator("report['issues'][0]['root_cause']='a-distinct-cause'\nreport['issues'][0]['claim']='A separate demonstrated defect'\nreport['issues'][0]['location']['end_line']=999")
        self.f.call(ok=False)
        self.report_mutator("report['issues'][0]['root_cause']='a-distinct-cause'\nreport['issues'][0]['claim']='A separate demonstrated defect'\nprompt=json.loads(request)\nreport['candidate_resolutions']=[{'candidate_id':c['id'],'action':'PROMOTE','issue_id':'I-001','reason':'The new independent finding is supported','evidence':report['coverage'][0]['evidence']} for c in prompt['candidates']]")
        self.f.call(goal='Correct the separate finding')
        state = self.f.state()
        self.assertEqual(len(state['issues']), 2)
        self.assertEqual(next(i for i in state['issues'] if i['id']=='I-001'), original_issue)
        canonical = state['runs'][-1]['issue_ids']['I-001']
        self.assertNotEqual(canonical, 'I-001')
        self.assertEqual(state['candidates'][0]['resolution']['issue_id'], canonical)
        self.assertEqual(self.report()['issues'][0]['id'], 'I-001')

    def test_pending_legacy_string_report_is_recovered_without_resetting_history(self):
        import hashlib
        state = self.f.state(); state['version'] = 3
        (self.f.task / 'state.json').write_text(json.dumps(state))
        self.f.check(); self.f.call('report_errors', ok=False)
        state = self.f.state(); run = state['runs'][0]; directory = self.f.task / run['directory']
        request = json.loads((directory / 'request.json').read_text())
        for key in ('report_format', 'candidates', 'issue_id_aliases', 'git_materials', 'session_key'):request.pop(key,None)
        import importlib.util
        spec = importlib.util.spec_from_file_location('old_contract', original.SCRIPT)
        controller = importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)
        request['output_schema'] = controller.report_schema(1)
        envelope = json.loads((directory / 'stdout.json').read_text()); report = envelope['structured_output'];report.pop('candidate_resolutions')
        def legacy(ref):
            if ref['kind']=='check':return 'check:'+ref['check_id']
            suffix = ':'+str(ref['start_line']) if ref['start_line'] else ''
            if ref['end_line']!=ref['start_line']:suffix+='-'+str(ref['end_line'])
            return ref['path']+suffix
        for field in ['coverage','issues','closed_issues']:
            for item in report[field]:
                item['evidence']=[legacy(ref) for ref in item['evidence']]
                if field=='issues':item['location']=legacy(item['location'])
        # Construct an old saved fixture, including its original self-consistent hashes.
        (directory / 'request.json').write_text(json.dumps(request))
        (directory / 'stdout.json').write_text(json.dumps(envelope))
        run['request_sha256']=hashlib.sha256((directory/'request.json').read_bytes()).hexdigest()
        run['stdout_sha256']=hashlib.sha256((directory/'stdout.json').read_bytes()).hexdigest()
        run.pop('report_rejected',None);state.pop('candidates')
        (self.f.task / 'state.json').write_text(json.dumps(state))
        untouched=(self.f.task/'state.json').read_bytes()
        original_intake=(self.f.task/'intake.json').read_bytes();original_raw=(directory/'stdout.json').read_bytes()
        status=json.loads(self.f.cmd('status').stdout)
        self.assertEqual(len(status['pending_candidates']),1)
        self.assertEqual((self.f.task/'state.json').read_bytes(),untouched)
        self.f.call('feedback_issue')
        state=self.f.state()
        self.assertEqual(state['version'],3)
        self.assertEqual(state['counters']['launches'],2)
        self.assertEqual(state['runs'][0]['request_id'],run['request_id'])
        self.assertEqual(state['candidates'][0]['status'],'PROMOTED')
        self.assertEqual(state['runs'][0]['session_id'],state['runs'][1]['session_id'])
        self.assertEqual((self.f.task/'intake.json').read_bytes(),original_intake)
        self.assertEqual((directory/'stdout.json').read_bytes(),original_raw)

    def test_symlink_retargeting_invalidates_proof_even_for_equal_file_bytes(self):
        self.init_git()
        (self.f.project/'a.txt').write_text('same\n');(self.f.project/'b.txt').write_text('same\n')
        link=self.f.project/'alias.txt';link.symlink_to('a.txt')
        self.f.check();self.f.call();self.f.cmd('finalize')
        link.unlink();link.symlink_to('b.txt')
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['status'],'STALE')

    def test_git_metadata_changes_by_delegated_repair_still_fail(self):
        import test_bidirectional
        reverse=test_bidirectional.BidirectionalTest();reverse.f=self.f;reverse.reverse()
        self.init_git();self.git('add','.');self.git('commit','-qm','Baseline')
        reverse.worsen()
        result=self.f.cmd('repair','--stage','implementation','--plan',reverse.plan(),mode='git_stage',ok=False)
        self.assertIn('Git HEAD/index',result.stderr)
        self.assertEqual(self.f.state()['interventions']['implementation']['status'],'FAILED')
        self.f.not_passed()

    def test_recovery_of_interrupted_git_repair_uses_original_audit_context(self):
        import time
        import test_bidirectional
        reverse=test_bidirectional.BidirectionalTest();reverse.f=self.f;reverse.reverse()
        self.init_git();self.git('add','.');self.git('commit','-qm','Baseline')
        reverse.worsen()
        process=subprocess.Popen([sys.executable,'-B',str(original.SCRIPT),'repair','--task',str(self.f.task),
                                  '--stage','implementation','--plan',reverse.plan()],
                                 env=dict(os.environ,COUNCIL_FAKE_MODE='git_stage_sleep'),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            for _ in range(150):
                staged=self.git('diff','--cached','--name-only').stdout
                if 'app.py' in staged:break
                time.sleep(.03)
            else:self.fail('Fake delegated repair did not stage its source')
            process.kill();process.communicate(timeout=5)
            for _ in range(150):
                result=self.f.cmd('recover',ok=False)
                if result.returncode==0:break
                self.assertIn('locked',result.stderr);time.sleep(.03)
            self.assertEqual(result.returncode,0,result.stderr)
            intervention=self.f.state()['interventions']['implementation']
            self.assertEqual(intervention['status'],'FAILED')
            self.assertEqual(intervention['repairs'][-1]['status'],'INTERRUPTED')
            self.f.not_passed()
        finally:
            if process.poll() is None:process.kill();process.communicate(timeout=5)


    def test_unparseable_report_payload_does_not_strand_the_next_explicit_call(self):
        self.report_mutator("envelope.pop('structured_output')\nenvelope['result']=42")
        self.f.check(); self.f.call(ok=False)
        state=self.f.state();state['claude']=str(original.FAKE)
        (self.f.task/'state.json').write_text(json.dumps(state))
        self.f.call(goal='Retry after the malformed transport representation is repaired')
        self.assertEqual(self.f.state()['counters']['launches'],2)
        self.f.cmd('finalize');self.assertEqual(self.f.state()['status'],'PASSED')


if __name__ == '__main__':
    unittest.main()
