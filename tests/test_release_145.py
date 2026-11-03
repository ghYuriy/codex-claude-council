"""Behavioural coverage for the 1.4.5 corrections."""
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys
import time
import unittest

import test_controller as original
import test_global_recovery as recovery
import test_bidirectional as bidirectional
import test_architecture as architecture
from unittest import mock


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def conversation_module():
    spec = importlib.util.spec_from_file_location('conversation_under_test', original.SCRIPT.with_name('conversation.py'))
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(original.SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(original.SCRIPT.parent))
    return module


class Release145Test(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def request(self):
        r = self.f.state()['runs'][-1]
        return json.loads((self.f.task / r['directory'] / 'request.json').read_text())

    def review(self, mode, goal='Review implementation', *extra, ok=False):
        return self.f.cmd('call', '--phase', 'REVIEW', '--stage', 'implementation', '--goal', goal, *extra,
                          mode=mode, ok=ok)

    def test_changed_goal_requires_reassessment_even_with_unchanged_evidence(self):
        self.f.check(); self.review('report_errors')
        self.review('report_errors', 'Assess failure handling instead')
        self.assertEqual(self.request()['report_feedback']['mode'], 'reassess')

    def test_correction_instruction_keeps_correct_report_for_the_same_goal(self):
        self.f.check(); self.review('report_errors')
        self.review('report_errors', 'Review implementation', '--correction', 'Return every finding in the schema')
        request = self.request()
        self.assertEqual(request['report_feedback']['mode'], 'correct_report')
        self.assertEqual(request['goal'], 'Review implementation')
        self.assertEqual(request['correction'], 'Return every finding in the schema')

    def task_with_optional_check(self):
        spec = json.loads(self.f.spec.read_text())
        spec['task_key'] = 'optional-check'
        spec['requirements'].append({'id': 'R-002', 'source': 'Keep value equal to one.', 'criterion': 'value is documented',
                                     'files': ['app.py'], 'checks': ['optional'], 'stage': 'implementation',
                                     'mandatory': False})
        spec['work_plan'][0]['steps'].append({'id': 'S-002', 'action': 'Inspect documentation',
                                              'requirement_ids': ['R-002'], 'checks': ['optional']})
        self.f.spec.write_text(json.dumps(spec))
        r = self.f.run_cmd('init', '--project', str(self.f.project), '--state-root', str(self.f.state_root),
                           '--intake', str(self.f.spec), '--claude', str(original.FAKE))
        self.f.task = Path(r.stdout.strip())

    def test_second_finalize_does_not_erase_an_interrupted_check(self):
        self.task_with_optional_check()
        self.f.check(); self.review('clear', ok=True)
        self.f.cmd('check', '--id', 'optional', '--timeout', '0.5', '--', sys.executable, '-B', '-c',
                   'import time; time.sleep(5)', ok=False)
        self.assertEqual(self.f.state()['status'], 'INTERRUPTED')
        self.f.not_passed()
        self.f.not_passed()

    def test_first_call_has_no_correction(self):
        self.f.check(); self.review('clear', ok=True)
        self.assertIsNone(self.request()['correction'])

    def test_interrupted_optional_check_blocks_until_it_is_repeated(self):
        self.task_with_optional_check()
        self.f.check(); self.review('clear', ok=True)
        self.f.cmd('check', '--id', 'optional', '--timeout', '0.5', '--', sys.executable, '-B', '-c',
                   'import time; time.sleep(5)', ok=False)
        self.f.check()
        self.assertEqual(self.f.state()['status'], 'VERIFY', 'another successful check replaces the shared status')
        self.f.not_passed()
        self.assertIn('optional', self.f.state()['reason'])
        self.f.cmd('check', '--id', 'optional', '--timeout', '5', '--', sys.executable, '-B', '-c', 'pass')
        self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_controller_crash_during_the_first_optional_check_blocks_until_it_is_repeated(self):
        self.task_with_optional_check()
        self.f.check(); self.review('clear', ok=True)
        env = dict(os.environ); env.pop('CODEX_THREAD_ID', None); env.pop('CODEX_COMPANION_SESSION_ID', None)
        p = subprocess.Popen([sys.executable, str(original.SCRIPT), 'check', '--task', str(self.f.task), '--id', 'optional',
                              '--timeout', '30', '--', sys.executable, '-B', '-c', 'import time; time.sleep(2)'],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(250):
            if self.f.state().get('active'): break
            time.sleep(0.02)
        else:
            self.fail('The check never became active')
        p.kill(); p.wait(timeout=5)
        for _ in range(300):
            result = self.f.cmd('recover', ok=False)
            if result.returncode == 0: break
            time.sleep(0.05)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('optional', self.f.state()['checks'], 'the first attempt left no check record')
        self.f.check()
        self.assertEqual(self.f.state()['status'], 'VERIFY')
        self.f.not_passed()
        self.assertIn('optional', self.f.state()['reason'])

    def test_changed_git_context_after_repeated_rejections_still_reaches_the_helper(self):
        self.f.check(); self.review('report_errors'); self.review('report_errors')
        launches = self.f.state()['counters']['launches']
        subprocess.run(['git', 'init', '-q'], cwd=self.f.project, check=True)
        self.review('report_errors')
        self.assertEqual(self.f.state()['counters']['launches'], launches + 1, 'not an identical request')
        self.assertEqual(self.request()['report_feedback']['mode'], 'reassess')

    def exchange(self, question):
        path = self.f.base / ('exchange-' + hashlib.sha256(question.encode()).hexdigest()[:8] + '.json')
        path.write_text(json.dumps({'question': question, 'alternatives': [], 'next_check': 'Inspect app.py',
                                    'positions': [{'kind': 'question', 'claim': question, 'evidence': []}]}))
        return str(path)

    def test_changed_exchange_requires_reassessment(self):
        self.f.check(); self.review('clear', ok=True)
        self.review('report_errors', 'Review implementation', '--exchange', self.exchange('Is the boundary handled?'))
        self.review('report_errors', 'Review implementation', '--exchange', self.exchange('Is the error path handled?'))
        self.assertEqual(self.request()['report_feedback']['mode'], 'reassess')

    def test_same_exchange_keeps_correct_report(self):
        self.f.check(); self.review('clear', ok=True)
        question = self.exchange('Is the boundary handled?')
        self.review('report_errors', 'Review implementation', '--exchange', question)
        self.review('report_errors', 'Review implementation', '--exchange', question)
        self.assertEqual(self.request()['report_feedback']['mode'], 'correct_report')

    def test_changed_git_context_with_the_same_files_requires_reassessment(self):
        self.f.check(); self.review('report_errors')
        subprocess.run(['git', 'init', '-q'], cwd=self.f.project, check=True)
        self.review('report_errors')
        self.assertEqual(self.request()['code_snapshot_id'], self.f.state()['runs'][-2]['snapshot_id'])
        self.assertEqual(self.request()['report_feedback']['mode'], 'reassess')

    def test_request_changed_between_saving_and_launch_is_refused(self):
        sys.path.insert(0, str(original.SCRIPT.parent))
        import council
        path = self.f.base / 'request.json'
        request = {'task_id': 't', 'request_id': 'r', 'phase': 'REPAIR', 'critic': 'Edit only plan files.'}
        council.atomic(path, request)
        self.assertEqual(council.saved_request_sha256(path, request), sha256(path))
        council.atomic(path, dict(request, critic='Edit anything.'))
        with self.assertRaisesRegex(council.CouncilError, 'changed before launch'):
            council.saved_request_sha256(path, request)

    def test_request_whose_value_changed_type_is_refused(self):
        sys.path.insert(0, str(original.SCRIPT.parent))
        import council
        path = self.f.base / 'request.json'
        request = {'task_id': 't', 'request_id': 'r', 'output_schema': {'type': 'object', 'additionalProperties': False}}
        council.atomic(path, request)
        path.write_text(path.read_text().replace('false', '0'))
        with self.assertRaisesRegex(council.CouncilError, 'changed before launch'):
            council.saved_request_sha256(path, request)

    def test_prompt_changed_after_launch_invalidates_the_response(self):
        self.f.check(); self.f.call('mutate_prompt', ok=False)
        run = self.f.state()['runs'][-1]
        self.assertEqual(run['status'], 'INVALID')
        self.assertIn('prompt.json', '\n'.join(run['validation_errors']))



class FinalizeProtocolTest(unittest.TestCase):
    """Each crash point of finalize leaves a state that every reader reports honestly and finalize can complete."""
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def status(self):
        return json.loads(self.f.cmd('status').stdout)

    def listed(self):
        r = self.f.run_cmd('list', '--project', str(self.f.project), '--state-root', str(self.f.state_root))
        return json.loads(r.stdout)[0]

    def passed(self):
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_finalize_records_the_hash_of_the_installed_report(self):
        self.passed()
        self.assertEqual(self.f.state()['final_sha256'], sha256(self.f.task / 'final.md'))
        self.assertFalse((self.f.task / 'final.pending.md').exists())
        self.assertEqual(self.status()['final_report'], 'verified')
        self.assertEqual(self.listed()['final_report'], 'verified')

    def test_report_not_installed_after_commit_is_visible_to_every_reader_and_finalize_completes_it(self):
        self.passed()
        (self.f.task / 'final.md').rename(self.f.task / 'final.pending.md')
        self.assertEqual(self.status()['final_report'], 'not_installed')
        self.assertEqual(self.listed()['final_report'], 'not_installed')
        self.assertEqual(conversation_module().council_summary(self.f.task)['final_report'], 'not_installed')
        self.f.cmd('finalize')
        self.assertEqual(self.status()['final_report'], 'verified')
        self.assertFalse((self.f.task / 'final.pending.md').exists())

    def test_changed_report_is_reported(self):
        self.passed()
        (self.f.task / 'final.md').write_text('# Council result\n\nStatus: PASSED\n')
        self.assertEqual(self.status()['final_report'], 'not_installed')

    def test_leftover_pending_report_from_an_earlier_crash_is_replaced(self):
        (self.f.task / 'final.pending.md').write_text('Status: PASSED\nleftover from an interrupted finalize\n')
        self.passed()
        self.assertNotIn('leftover', (self.f.task / 'final.md').read_text())
        self.assertEqual(self.status()['final_report'], 'verified')

    def test_pass_saved_before_this_release_reports_unrecorded_integrity(self):
        self.passed()
        state = self.f.state(); state.pop('final_sha256')
        (self.f.task / 'state.json').write_text(json.dumps(state))
        self.assertEqual(self.status()['final_report'], 'integrity_not_recorded')

    def test_unfinished_task_has_no_final_report_claim(self):
        self.f.check()
        self.assertIsNone(self.status()['final_report'])


class FinalizeConversationTest(unittest.TestCase):
    """Closing the bound conversation is recorded with the result and stays repeatable after a crash."""
    def setUp(self):
        self.r = recovery.GlobalRecoveryTest('test_objective_keeps_every_block_and_accepts_numeric_keys')
        self.r.setUp()
        self.addCleanup(self.r.doCleanups)
        result = self.r.initialize()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.task = Path(result.stdout.strip())
        self.record = self.r.records / 'native-new-project/state.json'

    def council(self, *action, ok=True):
        outcome = subprocess.run([sys.executable, '-B', str(original.SCRIPT), action[0], '--task', str(self.task), *action[1:]],
                                 env=self.r.env, capture_output=True, text=True)
        if ok: self.assertEqual(outcome.returncode, 0, outcome.stderr)
        return outcome

    def state(self):
        return json.loads((self.task / 'state.json').read_text())

    def reviewed(self, check='assert True'):
        self.council('check', '--id', 'unit', '--timeout', '5', '--', sys.executable, '-B', '-c', check, ok=check == 'assert True')
        self.council('call', '--phase', 'REVIEW', '--stage', 'implementation', '--goal', 'Review the task')

    def test_close_is_cleared_after_the_conversation_closes(self):
        self.reviewed(); self.council('finalize')
        self.assertNotIn('conversation_close_pending', self.state())
        self.assertEqual(json.loads(self.record.read_text())['status'], 'complete')

    def test_crash_after_install_before_close_is_visible_and_finalize_closes(self):
        self.reviewed(); self.council('finalize')
        state = self.state()
        state['conversation_close_pending'] = {'session': 'native-new-project', 'root': str(self.r.records), 'reason': None}
        (self.task / 'state.json').write_text(json.dumps(state))
        data = json.loads(self.record.read_text()); data['status'] = 'active'; self.record.write_text(json.dumps(data))
        self.assertEqual(json.loads(self.council('status').stdout)['conversation_close_pending']['session'], 'native-new-project')
        self.council('finalize')
        self.assertNotIn('conversation_close_pending', self.state())
        self.assertEqual(json.loads(self.record.read_text())['status'], 'complete')

    def test_record_waiting_for_the_user_keeps_the_pending_close_with_its_reason(self):
        data = json.loads(self.record.read_text())
        data.update(status='needs-user', blocker='Approval of the release wording is needed'); self.record.write_text(json.dumps(data))
        self.reviewed(); outcome = self.council('finalize')
        self.assertIn('Conversation record left open', outcome.stderr)
        pending = json.loads(self.council('status').stdout)['conversation_close_pending']
        self.assertIn('needs-user', pending['reason'])
        self.assertEqual(json.loads(self.record.read_text())['status'], 'needs-user')

    def test_older_task_identified_by_environment_and_default_root_keeps_its_pending_close(self):
        self.reviewed()
        state = self.state(); state.pop('conversation_recovery'); (self.task / 'state.json').write_text(json.dumps(state))
        self.council('finalize')
        pending = self.state()['conversation_close_pending']
        self.assertEqual(pending['session'], 'native-new-project')
        self.assertIn('No conversation record bound to this task was found', pending['reason'])

    def corrupt_installed_report(self):
        self.reviewed(); self.council('finalize')
        (self.task / 'final.md').write_text('# Council result\n\nStatus: PASSED\nfrom an earlier finalize\n')
        data = json.loads(self.record.read_text()); data['status'] = 'active'; self.record.write_text(json.dumps(data))

    def test_conversation_is_not_closed_automatically_while_the_report_is_not_installed(self):
        self.corrupt_installed_report()
        with self.assertRaisesRegex(Exception, 'final report'):
            conversation_module().close_council(self.r.records, 'native-new-project', self.task, self.task / 'final.md')
        self.assertEqual(json.loads(self.record.read_text())['status'], 'active')

    def test_conversation_is_not_marked_complete_by_hand_while_the_report_is_not_installed(self):
        self.corrupt_installed_report()
        outcome = self.r.f.base / 'result.txt'; outcome.write_text('Delivered.')
        checkpoint = self.r.f.base / 'done.json'
        checkpoint.write_text(json.dumps({'status': 'complete', 'next_step': '', 'operations': [], 'evidence': [],
                                          'result_file': str(outcome), 'council_task': str(self.task)}))
        result = subprocess.run([sys.executable, '-B', str(original.SCRIPT.with_name('conversation.py')), '--root',
                                 str(self.r.records), 'update', '--cwd', str(self.r.f.project), '--checkpoint', str(checkpoint)],
                                env=self.r.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('final report', result.stderr)
        self.assertEqual(json.loads(self.record.read_text())['status'], 'active')

    def test_shortened_card_keeps_the_final_report_state(self):
        self.corrupt_installed_report()
        module = conversation_module()
        data = json.loads(self.record.read_text())
        data.update(objective='Long objective ' * 400, next_step='Long step ' * 400, blocker='Long blocker ' * 400)
        for reserve in (700, 2500):
            card = module.context(self.record, data, reserve)
            with self.subTest(reserve=reserve):
                self.assertIn('Shortened card', card)
                self.assertIn('not_installed', card)
                self.assertIn(str(self.task), card)

    def test_card_with_very_long_paths_stays_valid_and_keeps_the_final_report_state(self):
        self.corrupt_installed_report()
        module = conversation_module()
        deep = self.r.f.base
        for letter in 'abcd':
            deep = deep / (letter * 200)
        deep.mkdir(parents=True)
        (deep / 'task').symlink_to(self.task)
        data = json.loads(self.record.read_text())
        data.update(objective='Long objective ' * 400, next_step='Long step ' * 400, blocker='Long blocker ' * 400,
                    council_task=str(deep / 'task'), source_file=str(deep / 'source.txt'))
        card = module.context(deep / 'state.json', data, 700)
        payload = json.loads(card[card.index('\n{') + 1:])
        self.assertEqual(payload['council_progress']['final_report'], 'not_installed')
        self.assertEqual(payload['state_file'], str(deep / 'state.json'))

    def test_blocked_result_does_not_close_or_mark_the_conversation(self):
        self.reviewed(check='raise SystemExit(1)')
        self.council('finalize', ok=False)
        self.assertNotIn('conversation_close_pending', self.state())
        self.assertEqual(json.loads(self.record.read_text())['status'], 'active')



class ModelInputTest(unittest.TestCase):
    """The helper receives the instruction and schema once, through the transport's own channel."""
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.log = self.f.base / 'transport.log'

    def saved(self):
        run = self.f.state()['runs'][-1]
        directory = self.f.task / run['directory']
        return run, json.loads((directory / 'request.json').read_text()), directory / 'prompt.json'

    def test_claude_reviewer_gets_the_contract_by_flags_and_a_prompt_without_copies(self):
        with mock.patch.dict('os.environ', {'COUNCIL_CLAUDE_LOG': str(self.log)}):
            self.f.check(); self.f.call()
        run, request, prompt = self.saved()
        logged = json.loads(self.log.read_text().splitlines()[-1])
        sent = json.loads(logged['stdin'])
        self.assertNotIn('critic', sent); self.assertNotIn('output_schema', sent)
        self.assertEqual(sent['request_id'], request['request_id'])
        argv = logged['argv']
        self.assertTrue(argv[argv.index('--append-system-prompt') + 1].startswith(request['critic']))
        self.assertEqual(json.loads(argv[argv.index('--json-schema') + 1]), request['output_schema'])
        self.assertEqual(hashlib.sha256(logged['stdin'].encode()).hexdigest(), run['prompt_sha256'])
        self.assertEqual(sha256(prompt), run['prompt_sha256'])
        self.assertEqual(sha256(prompt.with_name('request.json')), run['request_sha256'])

    def test_codex_helper_gets_the_contract_by_parameters_and_a_prompt_without_copies(self):
        b = bidirectional.BidirectionalTest('test_reverse_uses_identical_acceptance_and_native_resume')
        b.f = self.f
        with mock.patch.dict('os.environ', {'COUNCIL_NATIVE_LOG': str(self.log)}):
            b.reverse(); self.f.check(); self.f.call()
        run, request, prompt = self.saved()
        messages = [json.loads(line) for line in self.log.read_text().splitlines()]
        start = [m['params'] for m in messages if m.get('method') == 'thread/start'][-1]
        turn = [m['params'] for m in messages if m.get('method') == 'turn/start'][-1]
        text = turn['input'][0]['text']
        sent = json.loads(text)
        self.assertNotIn('critic', sent); self.assertNotIn('output_schema', sent)
        self.assertEqual(start['developerInstructions'], request['critic'])
        self.assertEqual(turn['outputSchema'], request['output_schema'])
        self.assertEqual(run['argv'][run['argv'].index('--contract-sha256') + 1], run['request_sha256'])
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), run['prompt_sha256'])
        self.assertEqual(sha256(prompt), run['prompt_sha256'])

    def test_rejected_report_rules_are_sent_only_with_report_feedback(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        first = self.saved()[1]
        self.assertNotIn('correct_report', first['contract_notes'])
        self.assertNotIn('correct_report', first['critic'])
        self.f.call('report_errors', ok=False)
        second = self.saved()[1]
        self.assertIn('correct_report', second['contract_notes'])
        self.assertEqual(second['critic'], first['critic'])

    def test_prompt_is_smaller_than_the_saved_request(self):
        self.f.check(); self.f.call()
        run, request, prompt = self.saved()
        saved = prompt.with_name('request.json').stat().st_size
        self.assertLess(prompt.stat().st_size, saved - len(request['critic']))



def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def tree(root):
    root = Path(root)
    return {str(p.relative_to(root)): (p.is_dir(), None if p.is_dir() else sha256(p))
            for p in root.rglob('*')} if root.exists() else {}


class FakeTransportTest(unittest.TestCase):
    """Process boundaries of both transports, checked with logging fakes that never contact a model."""
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.log = self.f.base / 'transport.log'
        self.pid = self.f.base / 'fake.pid'

    def test_reviewer_launch_is_logged_with_environment_names_and_never_values(self):
        with mock.patch.dict('os.environ', {'COUNCIL_CLAUDE_LOG': str(self.log), 'COUNCIL_TEST_SECRET': 'secret-value-7d1f'}):
            self.f.check(); self.f.call()
        logged = json.loads(self.log.read_text().splitlines()[-1])
        self.assertIn('COUNCIL_TEST_SECRET', logged['env_names'])
        self.assertEqual(Path(logged['cwd']).resolve(), self.f.project.resolve())
        self.assertNotIn('secret-value-7d1f', self.log.read_text())

    def test_reviewer_that_ignores_sigterm_is_stopped_at_the_call_timeout(self):
        started = time.monotonic()
        with mock.patch.dict('os.environ', {'COUNCIL_FAKE_PID_FILE': str(self.pid)}):
            self.f.check(); self.f.call('hang_ignore_term', ok=False)
        self.assertLess(time.monotonic() - started, 30)
        self.assertNotEqual(self.f.state()['runs'][-1]['status'], 'COMPLETE')
        self.assertFalse(alive(int(self.pid.read_text())))
        self.f.not_passed()

    def codex_helper(self, mode):
        b = bidirectional.BidirectionalTest('test_reverse_uses_identical_acceptance_and_native_resume')
        b.f = self.f
        with mock.patch.dict('os.environ', {'COUNCIL_FAKE_PID_FILE': str(self.pid), 'COUNCIL_NATIVE_LOG': str(self.log)}):
            b.reverse(); self.f.check()
            started = time.monotonic()
            self.f.call(mode, ok=False)
        self.assertLess(time.monotonic() - started, 30)
        self.assertNotEqual(self.f.state()['runs'][-1]['status'], 'COMPLETE')
        time.sleep(0.2)
        self.assertFalse(alive(int(self.pid.read_text())), 'the Codex process must not outlive the call')
        self.f.not_passed()

    def test_codex_helper_that_never_completes_its_turn_is_stopped_with_its_process(self):
        self.codex_helper('no_turn_complete')

    def test_codex_helper_that_ignores_sigterm_is_stopped_with_its_process(self):
        self.codex_helper('hang_ignore_term')

    def test_codex_launch_is_logged_with_environment_names_and_never_values(self):
        with mock.patch.dict('os.environ', {'COUNCIL_TEST_SECRET': 'secret-value-7d1f'}):
            self.codex_helper('no_turn_complete')
        launches = [json.loads(line)['launch'] for line in self.log.read_text().splitlines() if '"launch"' in line]
        self.assertTrue(launches)
        self.assertIn('COUNCIL_TEST_SECRET', launches[-1]['env_names'])
        self.assertNotIn('secret-value-7d1f', self.log.read_text())


class WriteBoundaryTest(unittest.TestCase):
    """Each scenario writes only to its own place: never to HOME, TMPDIR or the project."""
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.home = self.f.base / 'home'; self.home.mkdir()
        self.tmp = self.f.base / 'tmp'; self.tmp.mkdir()
        (self.home / '.keep').write_text('existing\n')
        patcher = mock.patch.dict('os.environ', {'HOME': str(self.home), 'TMPDIR': str(self.tmp)})
        patcher.start(); self.addCleanup(patcher.stop)

    def assert_untouched(self, scenario, before):
        with self.subTest(scenario=scenario):
            self.assertEqual(tree(self.home), before['home'])
            self.assertEqual(tree(self.tmp), before['tmp'])
            self.assertEqual(tree(self.f.project), before['project'])

    def snapshot(self):
        return {'home': tree(self.home), 'tmp': tree(self.tmp), 'project': tree(self.f.project)}

    def test_check_call_and_finalize_write_only_task_state(self):
        for scenario, action in (('check', self.f.check), ('call', self.f.call), ('finalize', lambda: self.f.cmd('finalize'))):
            before = self.snapshot(); state_before = tree(self.f.task)
            action()
            self.assert_untouched(scenario, before)
            self.assertNotEqual(tree(self.f.task), state_before)

    def test_installer_writes_only_its_target(self):
        target = self.f.base / 'skills' / 'codex-claude-council'
        before = self.snapshot()
        result = subprocess.run([sys.executable, '-B', str(original.SCRIPT.with_name('install.py')), '--target', str(target)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_untouched('install', before)
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ['codex-claude-council'])



class FailureDiagnosticsTest(unittest.TestCase):
    """A failed helper is reported by class and log path; raw stderr is shown only on explicit request."""
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_invalid_utf8_stderr_is_classified_and_does_not_block_the_next_call(self):
        self.f.check(); self.f.call('bad_utf8_stderr', ok=False)
        run = self.f.state()['runs'][-1]
        self.assertEqual(run['status'], 'INVALID')
        self.assertEqual(run['error_kind'], 'NETWORK')
        self.assertNotIn('codec', self.f.state()['reason'])
        self.f.call('clear', goal='Review implementation after the transport failure')
        self.assertEqual(self.f.state()['runs'][-1]['status'], 'COMPLETE')

    def test_invalid_utf8_inside_a_report_invalidates_it(self):
        with mock.patch.dict('os.environ', {'COUNCIL_FAKE_CORRUPT': '1'}):
            self.f.check(); self.f.call('clear', ok=False)
        run = self.f.state()['runs'][-1]
        self.assertEqual(run['status'], 'INVALID')
        self.assertIn('utf-8', '\n'.join(run['validation_errors']).lower())

    def test_invalid_utf8_in_a_rejected_report_leaves_no_candidate_and_the_next_call_runs(self):
        with mock.patch.dict('os.environ', {'COUNCIL_FAKE_CORRUPT': '1'}):
            self.f.check(); self.f.call('report_errors', ok=False)
        self.assertEqual(self.f.state().get('candidates', []), [])
        self.f.call('clear', goal='Review implementation after the corrupted response')
        self.assertEqual(self.f.state()['runs'][-1]['status'], 'COMPLETE')

    def test_unreachable_network_has_its_own_class(self):
        self.f.check(); self.f.call('bad_utf8_stderr', ok=False)
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['last_failure']['error_kind'], 'NETWORK')

    def test_failed_delegated_repair_is_reported_as_the_last_failure(self):
        a = architecture.ArchitectureTest('test_failed_prestart_repair_can_retry_but_cannot_be_approved')
        a.setUp(); self.addCleanup(a.doCleanups)
        a.init(); a.b.worsen()
        a.f.cmd('repair', '--stage', 'implementation', '--plan', a.b.plan(), mode='start_auth', ok=False)
        repair = a.f.state()['interventions']['implementation']['repairs'][-1]
        failure = json.loads(a.f.cmd('status', '--diagnostics').stdout)['last_failure']
        self.assertEqual(failure['operation'], 'repair')
        self.assertEqual(failure['request_id'], repair['request_id'])
        self.assertEqual(failure['error_kind'], 'AUTH')
        self.assertTrue(Path(failure['stderr_log']).is_file())

    def test_scope_failure_keeps_the_process_reason_visible(self):
        sys.path.insert(0, str(original.SCRIPT.parent))
        import council
        record = {'request_id': 'r1', 'directory': 'repairs/r1', 'status': 'INTERRUPTED', 'error_kind': 'SCOPE',
                  'process': {'reason': 'TIMEOUT', 'exit_code': None}}
        state = {'runs': [], 'last_helper': {'operation': 'repair', 'request_id': 'r1'},
                 'interventions': {'implementation': {'repairs': [record]}}}
        failure = council.last_failure(self.f.task, state)
        self.assertEqual((failure['error_kind'], failure['process_reason']), ('SCOPE', 'TIMEOUT'))

    def test_timed_out_delegated_repair_is_reported_as_a_timeout(self):
        a = architecture.ArchitectureTest('test_failed_prestart_repair_can_retry_but_cannot_be_approved')
        a.setUp(); self.addCleanup(a.doCleanups)
        a.init(); a.b.worsen()
        a.f.cmd('repair', '--stage', 'implementation', '--plan', a.b.plan(), mode='no_turn_complete', ok=False)
        failure = json.loads(a.f.cmd('status').stdout)['last_failure']
        self.assertEqual(failure['operation'], 'repair')
        self.assertEqual(failure['error_kind'], 'TIMEOUT')

    def test_invalid_utf8_stdout_does_not_block_the_next_call(self):
        self.f.check(); self.f.call('bad_utf8_stdout', ok=False)
        self.assertEqual(self.f.state()['runs'][-1]['status'], 'INVALID')
        self.f.call('clear', goal='Review implementation after the transport failure')
        self.assertEqual(self.f.state()['runs'][-1]['status'], 'COMPLETE')

    def test_status_reports_failure_class_and_log_without_raw_stderr(self):
        self.f.check(); self.f.call('long_stderr', ok=False)
        output = self.f.cmd('status').stdout
        failure = json.loads(output)['last_failure']
        self.assertEqual(failure['error_kind'], 'AUTH')
        self.assertTrue(Path(failure['stderr_log']).is_file())
        self.assertNotIn('authentication expired', output)
        self.assertNotIn('progress line', output)

    def test_diagnostics_show_a_bounded_tail_without_terminal_codes(self):
        self.f.check(); self.f.call('long_stderr', ok=False)
        failure = json.loads(self.f.cmd('status', '--diagnostics').stdout)['last_failure']
        tail = failure['stderr_tail']
        self.assertIn('fatal: authentication expired; run login', tail)
        self.assertNotIn('\x1b', tail)
        self.assertLessEqual(len(tail.encode()), 600)
        self.assertIn('not guaranteed', failure['warning'])

    def test_diagnostics_tolerate_invalid_utf8(self):
        self.f.check(); self.f.call('bad_utf8_stderr', ok=False)
        failure = json.loads(self.f.cmd('status', '--diagnostics').stdout)['last_failure']
        self.assertIn('fatal: network unreachable', failure['stderr_tail'])

    def test_successful_task_has_no_failure(self):
        self.f.check(); self.f.call()
        self.assertIsNone(json.loads(self.f.cmd('status').stdout)['last_failure'])


if __name__ == '__main__':
    unittest.main()
