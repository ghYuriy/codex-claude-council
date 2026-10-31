import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

import test_controller as original

FAKE_CODEX = Path(__file__).with_name('fake_codex.py')


class BidirectionalTest(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def reverse(self, lead='claude', extra_scope=False, multistage=False):
        data = json.loads(self.f.spec.read_text())
        data['task_key'] = 'bidirectional'
        data['policy'] = dict(launches=12, transport_retries=1, per_call_timeout=5, task_seconds=120)
        if extra_scope:
            (self.f.project/'other.py').write_text('untouched\n')
            data['scope'].append('other.py')
        if multistage:
            data['stages'].append('integration')
            data['requirements'].append(dict(data['requirements'][0], id='R-002', stage='integration'))
            data['work_plan'].append({'stage': 'integration', 'goal': 'Verify the whole result', 'risk': 'low',
                'steps': [{'id': 'S-002', 'action': 'Check all requirements', 'requirement_ids': ['R-001', 'R-002'], 'checks': ['unit']}], 'impact': []})
        self.f.spec.write_text(json.dumps(data))
        p = self.f.run_cmd('init', '--lead', lead, '--project', str(self.f.project), '--state-root', str(self.f.state_root),
                           '--intake', str(self.f.spec), '--claude', str(original.FAKE), '--codex', str(FAKE_CODEX))
        self.f.task = Path(p.stdout.strip())

    def worsen(self):
        self.f.check(); self.f.call('issue')
        (self.f.project/'app.py').write_text('value = 1\n# attempted repair\n')
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'FIXED_PENDING_CHECK', '--reason', 'Attempted the boundary repair')
        self.f.check()
        p = self.f.call('growth', goal='Check the repair and related path', ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.f.state()['interventions']['implementation']['status'], 'PENDING')

    def plan(self):
        p = self.f.base/'repair.json'
        p.write_text(json.dumps({'issue_ids': ['I-001', 'I-002'], 'cause': 'One shared boundary assumption affects both paths',
                                'cause_status': 'hypothesis', 'evidence': ['app.py'], 'files': ['app.py'],
                                'checks': ['unit'], 'strategy': 'Check the shared condition and repair it once; preserve the public value'}))
        return str(p)

    def test_reverse_uses_identical_acceptance_and_native_resume(self):
        self.reverse(); self.f.check(); self.f.call('issue')
        first = self.f.state()['sessions']['REVIEW:implementation']['native_id']
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'FIXED_PENDING_CHECK', '--reason', 'Repaired boundary')
        self.f.check(); self.f.call('close', goal='Verify the repair')
        state = self.f.state()
        self.assertEqual(first, state['sessions']['REVIEW:implementation']['native_id'])
        self.assertIn(first, state['runs'][-1]['argv'])
        self.f.cmd('checkpoint', '--stage', 'implementation'); self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'], 'PASSED')
        (self.f.project/'app.py').write_text('value = 2\n')
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['status'], 'STALE')

    def test_reverse_rejects_invalid_reports_and_changed_sources(self):
        self.reverse(); self.f.check()
        for mode in ('incomplete', 'wrong_snapshot', 'unverified', 'badjson', 'missing_artifact', 'maxturns', 'native_denied', 'mutate'):
            with self.subTest(mode=mode):
                self.assertNotEqual(self.f.call(mode, goal=mode, ok=False).returncode, 0)
                self.f.not_passed()

    def test_reverse_budget_and_evidence_tampering_are_enforced(self):
        self.reverse(); self.f.check(); self.f.call()
        (self.f.task/self.f.state()['checks']['unit']['stdout']).write_text('tampered')
        self.f.not_passed()
        s = self.f.state(); s['counters']['launches'] = s['policy']['launches']
        (self.f.task/'state.json').write_text(json.dumps(s))
        self.assertNotEqual(self.f.call(goal='Over budget', ok=False).returncode, 0)
        self.assertEqual(self.f.state()['counters']['launches'], 12)

    def test_growth_hands_only_the_block_to_codex_then_claude_verifies(self):
        self.reverse(); self.worsen()
        before = self.f.state()['counters']['launches']
        self.f.call(goal='Do not bypass intervention', ok=False)
        self.assertEqual(self.f.state()['counters']['launches'], before)
        self.f.cmd('repair', '--stage', 'implementation', '--plan', self.plan())
        s = self.f.state()
        self.assertEqual(s['lead'], 'claude')
        self.assertEqual(s['counters']['launches'], before+1)
        self.assertTrue(all(i['status'] == 'FIXED_PENDING_CHECK' for i in s['issues']))
        self.f.not_passed()
        self.f.check(); self.f.call('close', goal='Independent Claude verifies the Codex repair')
        self.assertEqual(self.f.state()['runs'][-1]['provider'], 'claude')
        self.assertEqual(self.f.state()['interventions']['implementation']['status'], 'VERIFIED')
        self.f.cmd('checkpoint', '--stage', 'implementation'); self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_codex_lead_never_hands_writing_to_claude_or_launches_codex(self):
        self.reverse(lead='codex'); self.worsen()
        before = self.f.state()['counters']['launches']
        result = json.loads(self.f.cmd('repair', '--stage', 'implementation', '--plan', self.plan()).stdout)
        self.assertEqual(result['implementer'], 'current Codex')
        self.assertEqual(self.f.state()['counters']['launches'], before)
        for issue in ('I-001', 'I-002'):
            self.f.cmd('resolve', '--issue', issue, '--action', 'FIXED_PENDING_CHECK', '--reason', 'Current Codex repaired shared cause')
        self.f.check(); self.f.call('close', goal='Claude verifies current Codex')
        self.f.cmd('finalize')
        self.assertTrue(all(r['provider'] == 'claude' for r in self.f.state()['runs']))

    def test_returned_verified_issue_stops_even_without_count_growth(self):
        self.reverse(); self.f.check(); self.f.call('issue')
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'FIXED_PENDING_CHECK', '--reason', 'Fixed')
        self.f.check(); self.f.call('close', goal='Verify closure')
        self.assertEqual(self.f.state()['issues'][0]['guard_checks'], ['unit'])
        self.f.call('issue_renamed', goal='Regression discovered', ok=False)
        self.assertEqual(len(self.f.state()['issues']), 1)
        self.assertEqual(len(self.f.state()['issues'][0]['closures']), 1)
        self.assertEqual(self.f.state()['interventions']['implementation']['status'], 'PENDING')

    def test_failed_preservation_check_stops_before_another_review(self):
        self.reverse(); self.f.check(); self.f.call('issue')
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'FIXED_PENDING_CHECK', '--reason', 'Fixed')
        self.f.check(); self.f.call('close', goal='Verify closure')
        (self.f.project/'app.py').write_text('value = 2\n')
        self.f.check(fail=True)
        self.assertEqual(self.f.state()['interventions']['implementation']['status'], 'PENDING')
        self.f.not_passed()

    def test_same_check_rerun_is_not_new_progress(self):
        self.reverse(); self.f.check(); self.f.call('issue'); self.f.check()
        self.f.call('issue', goal='Different wording is not progress', ok=False)
        self.assertEqual(self.f.state()['interventions']['implementation']['status'], 'PENDING')

    def test_outside_scope_write_cannot_be_accepted_or_automatically_retried(self):
        self.reverse(extra_scope=True); self.worsen()
        self.f.cmd('repair', '--stage', 'implementation', '--plan', self.plan(), mode='outside_scope', ok=False)
        self.assertEqual(self.f.state()['interventions']['implementation']['status'], 'FAILED')
        self.f.check(); self.f.call(goal='Cannot wash away scope violation', ok=False); self.f.not_passed()

    def test_intervention_cannot_be_skipped_by_clear_report_or_finalize(self):
        self.reverse(); self.worsen(); self.f.check()
        self.f.call(goal='Clear does not override pending intervention', ok=False); self.f.not_passed()
        self.assertNotEqual(self.f.cmd('checkpoint', '--stage', 'implementation', ok=False).returncode, 0)

    def test_wrong_native_session_on_resume_fails(self):
        self.reverse(); self.f.check(); self.f.call('issue')
        self.f.call('wrong_native_session', goal='Resume cannot silently switch threads', ok=False)
        self.f.not_passed()

    def test_second_deterioration_stops_without_swapping_back(self):
        self.reverse(); self.worsen()
        self.f.cmd('repair', '--stage', 'implementation', '--plan', self.plan()); self.f.check()
        self.f.call('growth3', goal='Inspect the delegated repair', ok=False)
        self.assertEqual(self.f.state()['interventions']['implementation']['status'], 'FAILED')
        roles = json.loads(self.f.cmd('status').stdout)['roles']['implementation']
        self.assertEqual(roles, {'implementer': 'codex', 'reviewer': 'claude'})
        before = self.f.state()['counters']['launches']
        self.f.cmd('repair', '--stage', 'implementation', '--plan', self.plan(), ok=False)
        self.assertEqual(self.f.state()['counters']['launches'], before)
        self.f.not_passed()

    def test_handoff_is_local_and_other_blocks_keep_normal_roles(self):
        self.reverse(multistage=True); self.worsen()
        self.f.cmd('repair', '--stage', 'implementation', '--plan', self.plan()); self.f.check()
        self.f.call('close', goal='Verify local handoff'); self.f.cmd('checkpoint', '--stage', 'implementation')
        self.f.cmd('call', '--phase', 'REVIEW', '--stage', 'integration', '--goal', 'Verify the whole task independently')
        self.assertEqual(self.f.state()['runs'][-1]['provider'], 'codex')
        self.f.cmd('finalize')

    def test_handoff_does_not_erase_an_unresolved_consultation(self):
        self.reverse()
        self.f.cmd('call', '--phase', 'CONSULT', '--stage', 'implementation', '--topic', 'choice',
                   '--goal', 'Resolve a real user choice', mode='needs_decision', ok=False)
        self.worsen(); self.f.cmd('repair', '--stage', 'implementation', '--plan', self.plan())
        self.f.check(); self.f.call('close', goal='Verify the repaired block')
        self.f.not_passed()
        self.assertIn('CONSULT:implementation:choice', self.f.cmd('status').stdout)

    def test_timeout_preserves_native_identity_and_budget(self):
        self.reverse()
        # Use the original finite call timeout, not a state edit or a reset.
        self.f.check(); self.f.call('sleep', goal='Interrupted review', ok=False)
        before = self.f.state()
        self.assertEqual(before['status'], 'INTERRUPTED')
        native = before['sessions']['REVIEW:implementation']['native_id']
        self.f.cmd('recover'); self.f.call(goal='Review after inspecting the interruption')
        self.assertEqual(self.f.state()['sessions']['REVIEW:implementation']['native_id'], native)
        self.assertEqual(self.f.state()['counters']['launches'], before['counters']['launches']+1)
        self.f.cmd('finalize')

    def test_two_failed_claimed_fixes_stop_even_when_count_does_not_grow(self):
        self.reverse(); self.f.check(); self.f.call('issue')
        for number in range(2):
            (self.f.project/'app.py').write_text('value = 1\n# incomplete repair '+str(number)+'\n')
            self.f.cmd('resolve', '--issue', 'I-001', '--action', 'FIXED_PENDING_CHECK', '--reason', 'Claimed complete repair')
            self.f.check(); self.f.call('issue', goal='Inspect attempt '+str(number), ok=number == 0)
        self.assertEqual(self.f.state()['interventions']['implementation']['status'], 'PENDING')

    def test_handoff_can_dispute_bad_advice_without_manufacturing_a_patch(self):
        self.reverse(); self.worsen()
        before = (self.f.project/'app.py').read_bytes()
        self.f.cmd('repair', '--stage', 'implementation', '--plan', self.plan(), mode='dispute_repair')
        self.assertEqual((self.f.project/'app.py').read_bytes(), before)
        self.assertTrue(all(i['status'] == 'DISPUTED' for i in self.f.state()['issues']))
        self.f.check(); self.f.call('reject', goal='Verify the counterexample independently'); self.f.cmd('finalize')
        self.assertTrue(all(i['status'] == 'REJECTED_WITH_EVIDENCE' for i in self.f.state()['issues']))

    def test_repair_artifact_tampering_invalidates_acceptance(self):
        self.reverse(); self.worsen()
        self.f.cmd('repair', '--stage', 'implementation', '--plan', self.plan())
        self.f.check(); self.f.call('close', goal='Verify repair'); self.f.cmd('finalize')
        record = self.f.state()['interventions']['implementation']['repairs'][-1]
        (self.f.task/record['directory']/'stdout.json').write_text('{}')
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['status'], 'STALE')

    def test_growth_without_a_changed_snapshot_does_not_blame_a_repair(self):
        self.reverse(); self.f.check(); self.f.call('issue')
        self.f.call('growth', goal='Discover an older defect before any repair')
        self.assertEqual(self.f.state()['interventions'], {})

    def test_recovery_detects_scope_violation_after_controller_crash(self):
        self.reverse(extra_scope=True); self.worsen()
        p = subprocess.Popen([sys.executable, '-B', str(original.SCRIPT), 'repair', '--task', str(self.f.task),
                              '--stage', 'implementation', '--plan', self.plan()],
                             env=dict(os.environ, COUNCIL_FAKE_MODE='outside_sleep'),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if (self.f.project/'other.py').read_text() == 'unrelated change\n':
                    break
                time.sleep(.03)
            else:
                self.fail('Delegated write did not start')
            p.kill(); p.communicate(timeout=5)
            for _ in range(100):
                result = self.f.cmd('recover', ok=False)
                if result.returncode == 0:
                    break
                self.assertIn('locked', result.stderr)
                time.sleep(.03)
            self.assertEqual(result.returncode, 0, result.stderr)
            intervention = self.f.state()['interventions']['implementation']
            self.assertEqual(intervention['status'], 'FAILED')
            self.assertEqual(intervention['repairs'][-1]['status'], 'INTERRUPTED')
            self.assertTrue(self.f.state()['sessions']['REPAIR:implementation']['native_id'])
            self.f.not_passed()
        finally:
            if p.poll() is None:
                p.kill(); p.communicate(timeout=5)

    def test_a_block_cannot_close_an_unrelated_blocks_issue(self):
        self.reverse(multistage=True); self.f.check(); self.f.call()
        self.f.cmd('checkpoint', '--stage', 'implementation')
        self.f.cmd('call', '--phase', 'REVIEW', '--stage', 'integration', '--goal', 'Inspect integration', mode='issue_last')
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'FIXED_PENDING_CHECK', '--reason', 'Repair the integration issue')
        self.f.check()
        result = self.f.call('close_foreign', goal='Recheck implementation only', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('another block', result.stderr)
        self.assertEqual(self.f.state()['issues'][0]['status'], 'FIXED_PENDING_CHECK')


if __name__ == '__main__':
    unittest.main()
