"""Report repair must improve diagnostics without accepting an invalid review."""
import json
import unittest

import test_controller as original


class ReportFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.check()

    def request(self):
        run = self.f.state()['runs'][-1]
        return json.loads((self.f.task / run['directory'] / 'request.json').read_text())

    def test_reports_all_reference_errors_in_one_failed_attempt(self):
        result = self.f.call('report_errors', ok=False)
        for detail in ('app.py', 'abbreviated.py', '/tmp/unregistered-proof.txt',
                       'check-artifact-directory', 'app.py:999', 'app.py:1 vs abbreviated.py:2'):
            self.assertIn(detail, result.stderr)
        state = self.f.state()
        self.assertEqual(state['runs'][-1]['status'], 'INVALID')
        self.assertGreaterEqual(len(state['runs'][-1]['report_errors']), 6)
        self.assertEqual(state['issues'], [])
        self.assertEqual(state['counters']['launches'], 1)
        self.f.not_passed()

    def test_same_session_corrects_report_then_tracks_real_issue_normally(self):
        self.f.call('report_errors', ok=False)
        first = self.f.state()['runs'][-1]
        raw = (self.f.task / first['directory'] / 'stdout.json').read_bytes()
        self.f.call('feedback_issue')
        state = self.f.state()
        self.assertEqual(state['runs'][-1]['session_id'], first['session_id'])
        self.assertIn('--resume', state['runs'][-1]['argv'])
        self.assertEqual(self.request()['report_feedback']['request_id'], first['request_id'])
        self.assertEqual((self.f.task / first['directory'] / 'stdout.json').read_bytes(), raw)
        self.assertEqual(state['counters']['launches'], 2)
        self.assertEqual(state['issues'][0]['id'], 'I-001')
        self.assertEqual(state['issues'][0]['status'], 'OPEN')
        self.f.not_passed()
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'FIXED_PENDING_CHECK',
                   '--reason', 'Verify the fixture boundary without discarding the rejected report')
        self.f.check()
        self.f.call('close', goal='Verify the preserved boundary fix')
        self.assertIsNone(self.request().get('report_feedback'))
        self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_unchanged_malformed_answer_does_not_enable_an_identical_loop(self):
        self.f.call('report_errors', ok=False)
        self.f.call('report_errors', ok=False)
        self.assertEqual(self.f.state()['counters']['launches'], 2)
        third = self.f.call('report_errors', ok=False)
        self.assertIn('Identical request', third.stderr)
        self.assertEqual(self.f.state()['counters']['launches'], 2)

    def test_rejected_design_does_not_contaminate_first_review(self):
        self.f.cmd('call', '--phase', 'DESIGN', '--stage', 'implementation',
                   '--goal', 'Assess a bounded question', mode='report_errors', ok=False)
        self.f.call()
        request = self.request()
        self.assertEqual(request['perspective'], 'independent')
        self.assertIsNone(request.get('report_feedback'))
        self.assertEqual(request['issues'], [])
        self.f.not_passed()

    def test_changed_rejected_response_blocks_reuse_before_another_launch(self):
        self.f.call('report_errors', ok=False)
        run = self.f.state()['runs'][-1]
        (self.f.task / run['directory'] / 'stdout.json').write_text('{}')
        result = self.f.call(goal='Correct the malformed report', ok=False)
        self.assertIn('changed', result.stderr.lower())
        self.assertEqual(self.f.state()['counters']['launches'], 1)

    def test_material_unverified_work_still_prevents_acceptance(self):
        self.f.call('unverified', ok=False)
        self.assertEqual(self.f.state()['runs'][-1]['status'], 'INVALID')
        self.f.not_passed()

    def test_older_rejected_run_keeps_feedback_after_sources_change(self):
        self.f.call('report_errors', ok=False)
        state = self.f.state()
        old_snapshot = state['runs'][-1]['snapshot_id']
        state['runs'][-1].pop('report_errors')
        (self.f.task / 'state.json').write_text(json.dumps(state))
        (self.f.project / 'app.py').write_text('value = 1\n# a newly inspected boundary\n')
        self.f.check()
        self.f.call('feedback_issue', goal='Continue an older saved task against the new source')
        request = self.request()
        self.assertEqual(request['report_feedback']['snapshot_id'], old_snapshot)
        self.assertNotEqual(request['code_snapshot_id'], old_snapshot)
        self.assertEqual(self.f.state()['issues'][0]['status'], 'OPEN')


if __name__ == '__main__':
    unittest.main()
