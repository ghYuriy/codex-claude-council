"""Regression checks for recovery, bounded questions and repair acceptance."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

import test_controller as original
import test_bidirectional as bidirectional


class ArchitectureTest(unittest.TestCase):
    def setUp(self):
        self.b = bidirectional.BidirectionalTest()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.f = self.b.f

    def init(self, key='architecture', lead='claude', launches=12, node=None, two_requirements=False, repair_calls=None):
        data = json.loads(self.f.spec.read_text())
        data['task_key'] = key
        data['policy'] = dict(launches=launches, transport_retries=1, per_call_timeout=5, task_seconds=120)
        if repair_calls is not None:
            data['policy']['repair_calls'] = repair_calls
        if two_requirements and len(data['requirements']) == 1:
            (self.f.project/'other.py').write_text('other = 2\n')
            data['scope'].append('other.py')
            data['requirements'].append(dict(data['requirements'][0], id='R-002', criterion='other == 2', files=['other.py']))
            data['work_plan'][0]['steps'].append({'id':'S-002','action':'Check other behavior','requirement_ids':['R-002'],'checks':['unit']})
        self.f.spec.write_text(json.dumps(data))
        args = ['init','--lead',lead,'--project',str(self.f.project),'--state-root',str(self.f.state_root),
                '--intake',str(self.f.spec),'--claude',str(original.FAKE),'--codex',str(bidirectional.FAKE_CODEX)]
        if node: args += ['--node',str(node)]
        self.f.task = Path(self.f.run_cmd(*args).stdout.strip())

    def request(self):
        run = self.f.state()['runs'][-1]
        return json.loads((self.f.task/run['directory']/'request.json').read_text())

    def test_prestart_failure_can_restart_identical_question_without_losing_budget_or_independence(self):
        for mode in ('start_auth', 'start_transient'):
            with self.subTest(mode=mode):
                self.init(key=mode); self.f.check()
                self.f.call(mode, ok=False)
                first = self.f.state(); session = first['sessions']['REVIEW:implementation']
                self.assertFalse(session['started'])
                self.assertTrue(first['runs'][-1]['session_not_started'])
                self.f.call()
                state = self.f.state()
                self.assertEqual(state['counters']['launches'], 2)
                self.assertEqual(state['counters']['retries'], 0)
                self.assertEqual(state['sessions']['REVIEW:implementation']['id'], session['id'])
                self.assertEqual(self.request()['perspective'], 'independent')
                self.assertEqual(self.request()['issues'], [])
                self.f.cmd('finalize')
                self.assertEqual(self.f.state()['status'], 'PASSED')

    def test_codex_explicit_transient_retry_preserves_limits(self):
        self.init(); self.f.check(); self.f.call('start_transient', ok=False)
        request = self.f.state()['runs'][-1]['request_id']
        self.f.cmd('call','--phase','REVIEW','--stage','implementation','--goal','Review implementation',
                   '--retry-of',request,mode='start_transient',ok=False)
        state = self.f.state(); self.assertEqual(state['counters']['launches'], 2)
        self.assertEqual(state['counters']['review'], 1)
        self.assertEqual(state['counters']['retries'], 1)
        last = state['runs'][-1]['request_id']
        failed = self.f.cmd('call','--phase','REVIEW','--stage','implementation','--goal','Review implementation',
                            '--retry-of',last,ok=False)
        self.assertIn('retry budget exhausted', failed.stderr)
        self.assertEqual(self.f.state()['counters']['launches'], 2)

    def test_poststart_failure_retains_and_resumes_the_native_thread(self):
        self.init(); self.f.check(); self.f.call('turn_network',ok=False)
        first=self.f.state(); sid=first['sessions']['REVIEW:implementation']['native_id']
        self.assertTrue(first['sessions']['REVIEW:implementation']['started'])
        self.assertFalse(first['runs'][-1].get('session_not_started',False))
        self.f.cmd('call','--phase','REVIEW','--stage','implementation','--goal','Review implementation',
                   '--retry-of',first['runs'][-1]['request_id'])
        self.assertEqual(self.f.state()['sessions']['REVIEW:implementation']['native_id'],sid)
        self.assertIn(sid,self.f.state()['runs'][-1]['argv'])

    def test_changed_prestart_proof_cannot_be_bypassed_with_a_different_goal(self):
        self.init();self.f.check();self.f.call('start_auth',ok=False)
        run=self.f.state()['runs'][-1]
        (self.f.task/run['directory']/'native-session.json').write_text('{}')
        failed=self.f.call(goal='Different wording must not waive startup evidence',ok=False)
        self.assertIn('checkpoint missing or changed',failed.stderr)
        self.assertEqual(self.f.state()['counters']['launches'],1)

    def test_missing_checkpoint_without_startup_proof_still_blocks(self):
        node=self.f.base/'node-unknown'
        node.write_text('#!'+sys.executable+'\nraise SystemExit(1)\n');node.chmod(0o755)
        self.init(node=node); self.f.check(); self.f.call(ok=False)
        self.assertTrue(self.f.state()['sessions']['REVIEW:implementation']['started'])
        failure=self.f.call(goal='Inspect unknown launch outcome',ok=False)
        self.assertIn('no saved native session',failure.stderr)
        self.assertEqual(self.f.state()['counters']['launches'],1)

    def test_worker_spawn_failure_is_distinct_from_a_lost_session(self):
        node=self.f.base/'node-wrapper'
        source='#!'+sys.executable+'\nimport os,sys\nos.execv('+repr(shutil.which('node'))+',['+repr(shutil.which('node'))+',*sys.argv[1:]])\n'
        node.write_text(source);node.chmod(0o755)
        self.init(node=node);self.f.check();node.unlink()
        self.f.call(ok=False)
        self.assertFalse(self.f.state()['sessions']['REVIEW:implementation']['started'])
        node.write_text(source);node.chmod(0o755)
        self.f.call()
        self.assertEqual(self.f.state()['counters']['launches'],2)

    def test_recover_retains_proven_prestart_failure_after_parent_loss(self):
        node=self.f.base/'node-wrapper'; real=shutil.which('node')
        node.write_text('#!'+sys.executable+'\nimport subprocess,sys,time\nsubprocess.run(['+repr(real)+',*sys.argv[1:]])\ntime.sleep(300)\n');node.chmod(0o755)
        self.init(node=node);self.f.check()
        p=subprocess.Popen([sys.executable,'-B',str(original.SCRIPT),'call','--task',str(self.f.task),
             '--phase','REVIEW','--stage','implementation','--goal','Review implementation'],
             env=dict(os.environ,COUNCIL_FAKE_MODE='start_auth'),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            for _ in range(150):
                checkpoints=list((self.f.task/'reviews').glob('*/native-session.json'))
                if checkpoints: break
                time.sleep(.02)
            else: self.fail('No startup outcome checkpoint')
            p.kill();p.communicate(timeout=5)
            for _ in range(100):
                result=self.f.cmd('recover',ok=False)
                if result.returncode==0: break
                self.assertIn('locked',result.stderr);time.sleep(.03)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse(self.f.state()['sessions']['REVIEW:implementation']['started'])
            node.write_text('#!'+sys.executable+'\nimport os,sys\nos.execv('+repr(real)+',['+repr(real)+',*sys.argv[1:]])\n');node.chmod(0o755)
            self.f.call()
            self.assertEqual(self.f.state()['counters']['launches'],2)
            self.assertEqual(self.request()['perspective'],'independent')
        finally:
            if p.poll() is None: p.kill();p.communicate(timeout=5)

    def test_budget_refusal_does_not_activate_repair_or_block_the_same_plan(self):
        self.init(launches=2);self.b.worsen();plan=self.b.plan()
        before=self.f.state()
        failure=self.f.cmd('repair','--stage','implementation','--plan',plan,ok=False)
        self.assertIn('budget exhausted',failure.stderr)
        self.assertEqual(self.f.state()['interventions'],before['interventions'])
        decision=self.f.base/'decision.txt';decision.write_text('Test fixture: authorize two more fake calls.')
        self.f.cmd('extend-budget','--launches','2','--user-decision',str(decision),'--reason','Verify the repair after an authorized extension')
        launches=self.f.state()['counters']['launches']
        self.f.call('close',goal='Must not bypass the pending repair',ok=False)
        self.assertEqual(self.f.state()['counters']['launches'],launches)
        self.f.cmd('repair','--stage','implementation','--plan',plan)
        self.f.check();self.f.call('close',goal='Verify the actual repair');self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'],'PASSED')
        self.assertEqual(self.f.state()['counters']['repair'],1)

    def test_failed_prestart_repair_can_retry_but_cannot_be_approved(self):
        self.init();self.b.worsen();plan=self.b.plan()
        self.f.cmd('repair','--stage','implementation','--plan',plan,mode='start_auth',ok=False)
        before=self.f.state()['counters']['launches']
        self.f.call('close',goal='Cannot review an unperformed repair',ok=False)
        self.assertEqual(self.f.state()['counters']['launches'],before)
        self.f.cmd('repair','--stage','implementation','--plan',plan)
        self.f.check();self.f.call('close',goal='Verify completed repair');self.f.cmd('finalize')
        self.assertEqual(self.f.state()['status'],'PASSED')

    def test_legacy_manual_record_in_claude_task_cannot_authorize_review(self):
        self.init();self.b.worsen()
        state=self.f.state();item=state['interventions']['implementation']
        item['status']='ACTIVE';item['repairs']=[{'status':'MANUAL'}]
        (self.f.task/'state.json').write_text(json.dumps(state))
        launches=state['counters']['launches']
        self.f.call('close',goal='Reject legacy false handoff',ok=False)
        self.assertEqual(self.f.state()['counters']['launches'],launches)
        self.f.not_passed()

    def test_scoped_questions_keep_review_complete_in_both_directions(self):
        for lead in ('claude','codex'):
            with self.subTest(lead=lead):
                self.init(key='scoped-'+lead,lead=lead,two_requirements=True);self.f.check()
                for phase in ('DESIGN','CONSULT'):
                    self.f.cmd('call','--phase',phase,'--stage','implementation','--requirements','R-001','--goal','Assess only the first obligation')
                    req=self.request()
                    self.assertEqual([r['id'] for r in req['requirements']],['R-001'])
                    self.assertEqual(req['required_files'],['app.py'])
                    self.assertEqual([s['id'] for s in req['work_plan']['steps']],['S-001'])
                    sid=self.f.state()['runs'][-1]['session_id']
                    self.f.cmd('call','--phase',phase,'--stage','implementation','--requirements','R-001','--goal','Assess a further concrete question')
                    self.assertEqual(self.f.state()['runs'][-1]['session_id'],sid)
                self.f.not_passed()
                self.f.call()
                self.assertEqual({r['id'] for r in self.request()['requirements']},{'R-001','R-002'})
                self.assertEqual(set(self.request()['required_files']),{'app.py','other.py'})
                self.f.cmd('finalize');self.assertEqual(self.f.state()['status'],'PASSED')

    def test_selection_is_validated_before_a_call_and_cannot_narrow_review(self):
        self.init(two_requirements=True)
        for phase,ids in [('REVIEW',['R-001']),('CONSULT',['R-999']),('DESIGN',['R-001','R-001'])]:
            with self.subTest(phase=phase,ids=ids):
                p=self.f.cmd('call','--phase',phase,'--stage','implementation','--requirements',*ids,'--goal','Invalid selection',ok=False)
                self.assertNotEqual(p.returncode,0)
                self.assertEqual(self.f.state()['counters']['launches'],0)

    def test_focused_question_keeps_related_routes_and_filters_unrelated_checks(self):
        self.init(two_requirements=True)
        data=json.loads(self.f.spec.read_text())
        data['requirements'][1]['checks']=['other-unit']
        data['work_plan'][0]['steps'][1]['checks']=['other-unit']
        for name in ('helper_a.py','helper_b.py'):
            (self.f.project/name).write_text('helper = True\n')
        data['work_plan'][0]['impact']=[
            {'route':['app.py','helper_a.py'],'reason':'Selected dependency','evidence':['app.py'],'checks':['unit']},
            {'route':['other.py','helper_b.py'],'reason':'Separate dependency','evidence':['other.py'],'checks':['other-unit']}]
        self.f.spec.write_text(json.dumps(data))
        self.init(key='related-routes')
        self.f.check()
        self.f.cmd('check','--id','other-unit','--timeout','3','--',sys.executable,'-B','-c','import other; assert other.other == 2')
        self.f.cmd('call','--phase','CONSULT','--stage','implementation','--requirements','R-001','--goal','Inspect the first route')
        req=self.request()
        self.assertEqual(set(req['required_files']),{'app.py','helper_a.py'})
        self.assertEqual(set(req['checks']),{'unit'})
        self.assertEqual(req['work_plan']['impact'],data['work_plan'][0]['impact'][:1])
        self.f.call()
        self.assertEqual(set(self.request()['required_files']),{'app.py','helper_a.py','other.py','helper_b.py'})
        self.f.cmd('finalize');self.assertEqual(self.f.state()['status'],'PASSED')

    def test_new_selection_cannot_erase_an_unresolved_discussion(self):
        self.init(two_requirements=True);self.f.check()
        self.f.cmd('call','--phase','CONSULT','--stage','implementation','--requirements','R-001','--goal','Investigate uncertainty',mode='unverified',ok=False)
        first=self.f.state()['runs'][-1]['session_id']
        self.f.cmd('call','--phase','CONSULT','--stage','implementation','--requirements','R-002','--goal','Investigate the other criterion')
        self.assertNotEqual(self.f.state()['runs'][-1]['session_id'],first)
        self.f.call();self.f.not_passed()
        self.f.cmd('call','--phase','CONSULT','--stage','implementation','--requirements','R-001','--goal','Resolve the original uncertainty with current evidence')
        self.assertEqual(self.f.state()['runs'][-1]['session_id'],first)
        self.f.cmd('finalize');self.assertEqual(self.f.state()['status'],'PASSED')

    def test_narrow_question_cannot_close_an_issue_for_another_requirement(self):
        self.init(two_requirements=True);self.f.check();self.f.call('issue')
        self.f.cmd('resolve','--issue','I-001','--action','FIXED_PENDING_CHECK','--reason','Fixture repair')
        self.f.check()
        self.f.cmd('call','--phase','CONSULT','--stage','implementation','--requirements','R-002','--goal','First narrow assessment')
        p=self.f.cmd('call','--phase','CONSULT','--stage','implementation','--requirements','R-002','--goal','Try to close an out-of-scope issue',mode='close_unrelated',ok=False)
        self.assertNotEqual(p.returncode,0)
        self.assertEqual(self.f.state()['issues'][0]['status'],'FIXED_PENDING_CHECK')

    def test_handoff_has_explicit_perspective_and_only_primary_evidence_root(self):
        self.init();self.b.worsen();self.f.cmd('repair','--stage','implementation','--plan',self.b.plan())
        self.f.check();self.f.call('close',goal='Verify the handed-off repair')
        req=self.request()
        self.assertEqual(req['perspective'],'handoff_verification')
        self.assertTrue(req['issues'])
        self.assertEqual(req['evidence_root'],str(self.f.task/'evidence'))
        self.assertEqual(req['evidence_reference_prefix'],'evidence/')
        self.assertEqual(req['implementer'],'codex');self.assertEqual(req['reviewer'],'claude')
        self.f.cmd('finalize');self.assertEqual(self.f.state()['status'],'PASSED')


if __name__ == '__main__': unittest.main()
