import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import test_controller as original
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/conversation.py'


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve(); self.root = self.base / 'records'
        self.cwd = self.base / 'project'; self.cwd.mkdir()
        self.source = self.base / 'request.txt'; self.source.write_text('Finish the release and preserve the waiting review.')

    def run_cmd(self, *args, event=None, ok=True):
        p = subprocess.run([sys.executable, '-B', str(SCRIPT), '--root', str(self.root), *args],
                           input=json.dumps(event) if event else None, capture_output=True, text=True)
        if ok: self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def begin(self, sid='session-a', objective='Finish release A'):
        self.run_cmd('begin', '--session', sid, '--cwd', str(self.cwd), '--source-file', str(self.source),
                     '--objective', objective, '--next-step', 'Wait for the existing reviewer and inspect its result')

    def update(self, status, sid='session-a', **kwargs):
        if status == 'complete' and 'council_task' not in kwargs:
            fixture=original.CouncilTest()
            with patch.object(tempfile, 'tempdir', str(self.cwd)):
                fixture.setUp()
            self.addCleanup(fixture.tearDown)
            spec=json.loads(fixture.spec.read_text());spec.update(task_key='bound-'+sid,source=self.source.read_text())
            fixture.spec.write_text(json.dumps(spec))
            fixture.task=Path(fixture.run_cmd('init','--project',str(fixture.project),'--state-root',str(fixture.state_root),
                '--intake',str(fixture.spec),'--claude',str(original.FAKE),'--conversation',sid).stdout.strip())
            fixture.check();fixture.call();fixture.cmd('finalize');kwargs['council_task']=str(fixture.task)
        cp = self.base / 'checkpoint.json'
        cp.write_text(json.dumps(dict(status=status, next_step='Inspect existing review' if status in ('active','waiting') else '',
                                      operations=[], evidence=[], **kwargs)))
        return self.run_cmd('update', '--session', sid, '--cwd', str(self.cwd), '--checkpoint', str(cp))

    def event(self, name, sid='session-a', **kwargs):
        data = dict(session_id=sid, cwd=str(self.cwd), hook_event_name=name, transcript_path=None, **kwargs)
        return json.loads(self.run_cmd('hook', event=data).stdout)

    def test_compaction_restores_only_matching_conversation_without_history(self):
        self.begin(); self.begin('session-b', 'Investigate unrelated B')
        out = self.event('SessionStart', source='compact')['hookSpecificOutput']['additionalContext']
        self.assertIn('Finish release A', out); self.assertNotIn('unrelated B', out)
        self.assertEqual(list(self.root.iterdir()).__len__(), 2)

    def test_waiting_operation_is_preserved_and_never_relaunched(self):
        self.begin()
        cp=self.base/'wait.json'; cp.write_text(json.dumps({'status':'waiting','next_step':'Read existing review',
            'operations':[{'id':'existing-123','kind':'review','status':'running','reference':'saved task'}],'evidence':[]}))
        self.run_cmd('update','--session','session-a','--cwd',str(self.cwd),'--checkpoint',str(cp))
        before=json.loads((self.root/'session-a/state.json').read_text())['operations']
        out=self.event('SessionStart',source='compact')
        self.assertIn('existing-123',out['hookSpecificOutput']['additionalContext'])
        self.assertEqual(before,json.loads((self.root/'session-a/state.json').read_text())['operations'])

    def test_stop_checks_unfinished_task_not_a_banned_phrase_and_never_loops(self):
        self.begin()
        for text in ['Принял инструкции', 'All set', 'Finished.']:
            self.assertEqual(self.event('Stop',last_assistant_message=text,stop_hook_active=False)['decision'],'block')
        self.assertNotIn('decision',self.event('Stop',stop_hook_active=True))

    def test_completed_and_cancelled_tasks_are_not_resumed(self):
        for sid, status, field in [('session-a','complete','result_file'),('session-b','cancelled','user_decision')]:
            self.begin(sid); result=self.base/(sid+'.txt');result.write_text('Completed deliverable' if status=='complete' else 'User cancelled this task')
            self.update(status,sid,**{field:str(result)})
            self.assertEqual(self.event('Stop',sid,stop_hook_active=False),{})
            self.assertIn(status,self.event('SessionStart',sid,source='compact')['hookSpecificOutput']['additionalContext'])

    def test_interrupt_does_not_auto_resume(self):
        self.begin();self.event('Interrupt')
        self.assertEqual(self.event('Stop',stop_hook_active=False),{})
        self.assertIn('paused',self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext'])

    def test_corrupted_state_is_unknown_not_no_task(self):
        self.begin();(self.root/'session-a/state.json').write_text('{}')
        out=self.event('SessionStart',source='compact')
        self.assertIn('Do not infer that the task is absent',out['hookSpecificOutput']['additionalContext'])

    def test_unbound_conversation_has_no_effect(self):
        self.assertEqual(self.event('SessionStart',source='compact'),{})
        self.assertFalse(self.root.exists())

    def test_new_user_instruction_is_retained_without_reviving_completed_work(self):
        self.begin();result=self.base/'done.txt';result.write_text('Release delivered')
        self.update('complete',result_file=str(result))
        self.event('UserPromptSubmit',prompt='New request: inspect the contract only',turn_id='turn-new')
        saved=json.loads((self.root/'session-a/state.json').read_text())
        self.assertEqual(saved['status'],'complete');self.assertTrue(saved['pending_user_request'])
        self.assertEqual(Path(saved['user_messages'][-1]['file']).read_text(),'New request: inspect the contract only')

    def test_mismatched_cwd_does_not_expose_another_task(self):
        self.begin()
        out=json.loads(self.run_cmd('hook',event={'session_id':'session-a','cwd':str(self.base),
                       'hook_event_name':'SessionStart','source':'compact'}).stdout)
        self.assertIn('does not match',out['systemMessage']);self.assertNotIn('Finish release A',json.dumps(out))

    def test_installation_preserves_other_hooks_and_is_idempotent(self):
        config=self.cwd/'.codex/hooks.json';config.parent.mkdir()
        other={'hooks':{'Stop':[{'hooks':[{'type':'command','command':'other-guard'}]}]}}
        config.write_text(json.dumps(other))
        self.run_cmd('install-hooks','--project',str(self.cwd))
        before=config.read_bytes();self.run_cmd('install-hooks','--project',str(self.cwd))
        self.assertEqual(config.read_bytes(),before)
        self.assertEqual(json.loads(before)['hooks']['Stop'][0],other['hooks']['Stop'][0])

    def test_terminal_status_requires_nonempty_result_and_council_acceptance(self):
        self.begin();empty=self.base/'empty.txt';empty.write_text('')
        cp=self.base/'done.json';cp.write_text(json.dumps({'status':'complete','next_step':'','operations':[],'evidence':[],'result_file':str(empty)}))
        self.assertNotEqual(self.run_cmd('update','--session','session-a','--cwd',str(self.cwd),'--checkpoint',str(cp),ok=False).returncode,0)
        self.assertEqual(self.event('Stop',stop_hook_active=False)['decision'],'block')

    def test_new_request_after_completion_needs_its_own_objective(self):
        self.begin();result=self.base/'done.txt';result.write_text('Delivered the original task')
        self.update('complete',result_file=str(result))
        self.event('UserPromptSubmit',prompt='Now investigate a different issue',turn_id='next-task')
        out=self.event('Stop',stop_hook_active=False,last_assistant_message='Принял инструкции')
        self.assertEqual(out.get('decision'),'block')
        self.assertIn('newer user request',out['reason'])

    def test_success_of_another_task_does_not_complete_this_request(self):
        self.begin();fixture=original.CouncilTest()
        with patch.object(tempfile,'tempdir',str(self.cwd)):fixture.setUp()
        self.addCleanup(fixture.tearDown);fixture.check();fixture.call();fixture.cmd('finalize')
        result=self.base/'result.txt';result.write_text('A different task passed')
        cp=self.base/'wrong.json';cp.write_text(json.dumps({'status':'complete','next_step':'','operations':[],'evidence':[],
                                                         'result_file':str(result),'council_task':str(fixture.task)}))
        outcome=self.run_cmd('update','--session','session-a','--cwd',str(self.cwd),'--checkpoint',str(cp),ok=False)
        self.assertIn('original user request',outcome.stderr)
        self.assertEqual(self.event('Stop',stop_hook_active=False)['decision'],'block')

    def test_matching_request_in_another_native_conversation_cannot_be_bound(self):
        self.begin();fixture=original.CouncilTest()
        with patch.object(tempfile,'tempdir',str(self.cwd)):fixture.setUp()
        self.addCleanup(fixture.tearDown)
        spec=json.loads(fixture.spec.read_text());spec.update(task_key='other-session',source=self.source.read_text())
        fixture.spec.write_text(json.dumps(spec))
        task=fixture.run_cmd('init','--project',str(fixture.project),'--state-root',str(fixture.state_root),
                            '--intake',str(fixture.spec),'--claude',str(original.FAKE),'--conversation','session-b').stdout.strip()
        cp=self.base/'foreign.json';cp.write_text(json.dumps({'status':'active','next_step':'Do the work','operations':[],
                                                             'evidence':[],'council_task':task}))
        outcome=self.run_cmd('update','--session','session-a','--cwd',str(self.cwd),'--checkpoint',str(cp),ok=False)
        self.assertIn('different or unrecorded native conversation',outcome.stderr)
