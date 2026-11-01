"""A later clean check cannot erase an earlier breach of the original baseline."""
import json
import sys
import unittest
from pathlib import Path
import test_controller as original


class PreservationTest(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.observed = self.f.base / 'counter.json'; self.observed.write_text('{"counter":10}\n')
        self.collector = self.f.base / 'read_counter.py'
        self.collector.write_text('from pathlib import Path\nprint(Path(' + repr(str(self.observed)) + ').read_text(), end="")\n')
        spec = json.loads(self.f.spec.read_text()); spec.update(task_key='preserve', preservation={'R-001': {
            'command': [sys.executable, '-B', str(self.collector)], 'inputs': [], 'timeout': 3}})
        spec['requirements'][0]['criterion'] = 'Keep the external persistent counter unchanged.'
        self.f.spec.write_text(json.dumps(spec))
        self.f.task = Path(self.f.run_cmd('init', '--project', str(self.f.project), '--state-root', str(self.f.state_root),
                                      '--intake', str(self.f.spec), '--claude', str(original.FAKE)).stdout.strip())
        self.observed = self.f.base / 'counter.json'; self.observed.write_text('{"counter":10}\n')

    def observe(self, mode='observe', **kwargs):
        return self.f.cmd('preserve', '--requirement', 'R-001', '--mode', mode,
                          **kwargs)

    def baseline(self):
        self.observe('baseline')

    def test_baseline_is_required_before_checks_and_cannot_be_replaced(self):
        result = self.f.cmd('check', '--id', 'unit', '--timeout', '3', '--', 'true', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('baseline', result.stderr.lower())
        self.baseline(); self.observed.write_text('{"counter":11}\n')
        self.assertNotEqual(self.observe('baseline', ok=False).returncode, 0)

    def test_later_clean_checks_and_clear_review_do_not_erase_breach(self):
        self.baseline(); self.f.check(); self.observed.write_text('{"counter":11}\n'); self.observe()
        self.f.check(); self.observe(); self.f.call(); self.f.not_passed()
        status = json.loads(self.f.cmd('status').stdout)
        self.assertTrue(any('preservation' in x.lower() for x in status['acceptance_failures']))
        self.assertEqual(len(status['preservation']['R-001']['violations']), 1)

    def test_return_to_original_value_needs_explicit_restoration(self):
        self.baseline(); self.f.check(); self.observed.write_text('{"counter":11}\n'); self.observe()
        self.observed.write_text('{"counter":10}\n'); self.observe(); self.f.call(); self.f.not_passed()
        self.observe('restore'); self.f.call(goal='Review explicitly recorded restoration'); self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_restore_cannot_accept_the_later_changed_baseline(self):
        self.baseline(); self.f.check(); self.observed.write_text('{"counter":11}\n'); self.observe()
        self.assertNotEqual(self.observe('restore', ok=False).returncode, 0)
        self.f.not_passed()

    def test_user_change_is_explicit_and_does_not_rewrite_original_requirement(self):
        self.baseline(); self.f.check(); self.observed.write_text('{"counter":11}\n'); self.observe()
        decision = self.f.base / 'user.txt'; decision.write_text('I explicitly accept counter 11 for R-001 instead of counter 10.')
        original_intake = (self.f.task / 'intake.json').read_bytes()
        self.f.cmd('preserve', '--requirement', 'R-001', '--mode', 'accept-change', '--user-decision', str(decision),
                   '--new-criterion', 'The owner accepts the recorded counter value 11; no further drift is permitted.')
        self.f.call(); self.f.cmd('finalize')
        self.assertEqual((self.f.task / 'intake.json').read_bytes(), original_intake)
        self.observed.write_text('{"counter":12}\n'); self.observe(); self.f.not_passed()

    def test_new_action_requires_new_observation(self):
        self.baseline(); self.f.check(); self.observe(); self.f.check(); self.f.call(); self.f.cmd('finalize')
        self.observed.write_text('{"counter":12}\n'); self.f.not_passed()

    def test_changed_retained_baseline_blocks_acceptance(self):
        self.baseline(); self.f.check(); self.observe(); self.f.call()
        baseline = self.f.state()['preservation_records']['R-001']['baseline']
        (self.f.task / baseline['artifact']).write_text('changed')
        self.f.not_passed()

    def test_initial_action_drift_is_caught_without_manual_observation(self):
        self.baseline()
        code='from pathlib import Path; Path('+repr(str(self.observed))+').write_text('+repr('{"counter":11}\n')+')'
        self.f.cmd('check','--id','unit','--timeout','3','--',sys.executable,'-B','-c',code)
        self.assertEqual(self.f.state()['preservation_records']['R-001']['violations'][0]['status'],'OPEN')
        self.f.check();self.f.call();self.f.not_passed()
        self.assertEqual(self.f.state()['preservation_records']['R-001']['violations'][0]['status'],'OPEN')

    def test_changed_collector_cannot_redefine_the_original_measurement(self):
        self.baseline();self.f.check()
        self.collector.write_text('print(10)\n')
        self.observe();self.f.call();self.f.not_passed()
        status=json.loads(self.f.cmd('status').stdout)
        self.assertTrue(any('input changed' in item for item in status['acceptance_failures']))

    def test_missing_preservation_classification_is_not_assumed_empty(self):
        spec=json.loads(self.f.spec.read_text());spec['task_key']='unclassified';spec.pop('preservation')
        self.f.spec.write_text(json.dumps(spec))
        result=self.f.run_cmd('init','--project',str(self.f.project),'--state-root',str(self.f.state_root),
                              '--intake',str(self.f.spec),'--claude',str(original.FAKE),ok=False)
        self.assertIn('Declare preservation explicitly',result.stderr)

    def test_final_integration_covers_restoration_after_an_earlier_checkpoint(self):
        spec=json.loads(self.f.spec.read_text());spec['task_key']='multi-preserve';spec['stages']=['implementation','integration']
        spec['requirements'].append(dict(spec['requirements'][0],id='R-002',stage='integration'))
        spec['work_plan'].append({'stage':'integration','goal':'Verify the complete result','risk':'low','steps':[
            {'id':'S-002','action':'Verify all requirements','requirement_ids':['R-001','R-002'],'checks':['unit']}],'impact':[]})
        self.f.spec.write_text(json.dumps(spec))
        self.f.task=Path(self.f.run_cmd('init','--project',str(self.f.project),'--state-root',str(self.f.state_root),
                                      '--intake',str(self.f.spec),'--claude',str(original.FAKE)).stdout.strip())
        self.baseline();self.f.check();self.f.call();self.f.cmd('checkpoint','--stage','implementation')
        self.observed.write_text('{"counter":11}\n');self.observe()
        self.observed.write_text('{"counter":10}\n');self.observe('restore');self.f.check()
        self.f.cmd('call','--phase','REVIEW','--stage','integration','--goal','Review restoration and complete integration')
        self.f.cmd('finalize');self.assertEqual(self.f.state()['status'],'PASSED')
