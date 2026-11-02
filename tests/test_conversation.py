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
        checkpoint=dict(status=status, next_step='Inspect existing review' if status in ('active','waiting') else '',
                        operations=[], evidence=[])
        checkpoint.update(kwargs);cp.write_text(json.dumps(checkpoint))
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
        self.assertEqual(self.event('Stop',stop_hook_active=False)['decision'],'block')

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

    def test_empty_result_does_not_finish_standalone_work(self):
        self.begin();empty=self.base/'empty.txt';empty.write_text('')
        cp=self.base/'done.json';cp.write_text(json.dumps({'status':'complete','next_step':'','operations':[],'evidence':[],'result_file':str(empty)}))
        self.assertNotEqual(self.run_cmd('update','--session','session-a','--cwd',str(self.cwd),'--checkpoint',str(cp),ok=False).returncode,0)
        self.assertEqual(self.event('Stop',stop_hook_active=False)['decision'],'block')

    def test_standalone_completion_rejects_unfinished_steps_or_operations(self):
        self.begin();result=self.base/'result.txt';result.write_text('Partial result')
        for pending in [dict(next_step='Finish the remaining check',operations=[]),
                        dict(next_step='',operations=[{'id':'running-check','kind':'check',
                                                      'status':'running','reference':'existing process'}])]:
            cp=self.base/'unfinished.json';cp.write_text(json.dumps(dict(
                status='complete',evidence=[],result_file=str(result),**pending)))
            outcome=self.run_cmd('update','--session','session-a','--cwd',str(self.cwd),'--checkpoint',str(cp),ok=False)
            self.assertNotEqual(outcome.returncode,0)
            self.assertEqual(self.event('Stop',stop_hook_active=False)['decision'],'block')

    def test_questions_after_completion_do_not_require_or_create_a_task(self):
        self.begin();result=self.base/'done.txt';result.write_text('Delivered the original task')
        self.update('complete',result_file=str(result))
        before=json.loads((self.root/'session-a/state.json').read_text())
        for number, question in enumerate(['Почему это работает?', 'Can you explain the limitation?',
                                            'Как отличить завершённую работу от нового вопроса?']):
            self.assertEqual(self.event('UserPromptSubmit',prompt=question,turn_id='question-'+str(number)),{})
            self.assertEqual(self.event('Stop',stop_hook_active=False,last_assistant_message='The requested explanation.'),{})
            self.assertEqual(self.event('Stop',stop_hook_active=True),{})
        saved=json.loads((self.root/'session-a/state.json').read_text())
        for key in ('status','generation','objective','council_task','operations','result_file_sha256'):
            self.assertEqual(saved[key],before[key])
        self.assertFalse((self.root/'session-a/previous-1.json').exists())
        recovered=self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext']
        self.assertIn('Ordinary questions need no new record',recovered)
        self.assertIn('Как отличить',recovered)

    def test_question_after_cancellation_does_not_require_registration(self):
        self.begin();decision=self.base/'cancel.txt';decision.write_text('Cancel the original work.')
        self.update('cancelled',user_decision=str(decision))
        self.assertEqual(self.event('UserPromptSubmit',prompt='What was cancelled?',turn_id='question'),{})
        self.assertEqual(self.event('Stop',stop_hook_active=False),{})

    def test_followup_question_does_not_erase_waiting_work_after_compaction(self):
        self.begin()
        operation={'id':'existing-123','kind':'review','status':'running','reference':'saved task'}
        self.update('waiting',operations=[operation],next_step='Read the existing operation result')
        self.event('UserPromptSubmit',prompt='Почему проверка ещё идёт?',turn_id='status-question')
        out=self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext']
        self.assertIn('Finish release A',out);self.assertIn('Read the existing operation result',out)
        self.assertIn('existing-123',out);self.assertIn('Почему проверка',out)
        saved=json.loads((self.root/'session-a/state.json').read_text())
        self.assertEqual(saved['status'],'waiting');self.assertEqual(saved['operations'],[operation])
        self.assertEqual(self.event('Stop',stop_hook_active=False)['decision'],'block')
        self.assertNotIn('decision',self.event('Stop',stop_hook_active=True))

    def test_new_explicit_work_after_question_is_recoverable(self):
        self.begin();result=self.base/'done.txt';result.write_text('Delivered the original task')
        self.update('complete',result_file=str(result))
        self.event('UserPromptSubmit',prompt='Explain the outcome',turn_id='question')
        self.assertEqual(self.event('Stop',stop_hook_active=False),{})
        self.event('UserPromptSubmit',prompt='Now fix the next issue',turn_id='new-work')
        self.source.write_text('Now fix the next issue')
        self.begin(objective='Fix the next issue')
        restored=self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext']
        self.assertIn('Fix the next issue',restored)
        self.assertEqual(self.event('Stop',stop_hook_active=False)['decision'],'block')
        self.assertEqual(json.loads((self.root/'session-a/state.json').read_text())['generation'],2)
        self.assertEqual(json.loads((self.root/'session-a/previous-1.json').read_text())['status'],'complete')

    def test_standalone_work_can_finish_without_manufacturing_a_council_review(self):
        self.begin();result=self.base/'answer.txt';result.write_text('Verified the requested standalone change.')
        self.update('complete',council_task=None,result_file=str(result))
        self.assertEqual(self.event('Stop',stop_hook_active=False),{})
        saved=json.loads((self.root/'session-a/state.json').read_text())
        self.assertIsNone(saved.get('council_task'));self.assertEqual(saved['status'],'complete')

    def test_bound_unfinished_review_cannot_be_detached_to_claim_completion(self):
        self.begin();fixture=original.CouncilTest()
        with patch.object(tempfile,'tempdir',str(self.cwd)):fixture.setUp()
        self.addCleanup(fixture.tearDown)
        spec=json.loads(fixture.spec.read_text());spec.update(task_key='unfinished-review',source=self.source.read_text())
        fixture.spec.write_text(json.dumps(spec))
        task=fixture.run_cmd('init','--project',str(fixture.project),'--state-root',str(fixture.state_root),
                            '--intake',str(fixture.spec),'--claude',str(original.FAKE),'--conversation','session-a').stdout.strip()
        self.update('active',council_task=task)
        result=self.base/'claim.txt';result.write_text('A claim without the required review.')
        for extra in ({}, {'council_task':None}):
            cp=self.base/'invalid-completion.json';cp.write_text(json.dumps(dict(
                status='complete',next_step='',operations=[],evidence=[],result_file=str(result),**extra)))
            outcome=self.run_cmd('update','--session','session-a','--cwd',str(self.cwd),'--checkpoint',str(cp),ok=False)
            self.assertNotEqual(outcome.returncode,0)
            saved=json.loads((self.root/'session-a/state.json').read_text())
            self.assertEqual(saved['status'],'active');self.assertEqual(saved['council_task'],task)

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

    def test_moved_completion_evidence_warns_without_forcing_continuations(self):
        self.begin();result=self.base/'done.txt';result.write_text('Release delivered')
        self.update('complete',result_file=str(result));result.rename(self.base/'moved.txt')
        out=self.event('UserPromptSubmit',prompt='A short question',turn_id='turn-q')
        self.assertIn('unverified',out['systemMessage']);self.assertNotIn('decision',out)
        self.assertEqual(self.event('Stop',stop_hook_active=False),{})
        shown=self.run_cmd('show','--session','session-a','--cwd',str(self.cwd)).stdout
        self.assertIn('unverified',shown)

    def test_context_transition_in_a_turn_gets_one_bounded_completion_check(self):
        self.begin();result=self.base/'done.txt';result.write_text('Release delivered')
        self.update('complete',result_file=str(result))
        self.event('UserPromptSubmit',prompt='Теперь опубликуй релиз',turn_id='turn-publish')
        card=self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext']
        self.assertIn('Теперь опубликуй релиз',card);self.assertNotIn('not an unfinished task',card)
        self.assertIn('latest user',card)
        blocked=self.event('Stop',last_assistant_message='Принял. Буду общаться по-русски.',stop_hook_active=False)
        self.assertEqual(blocked['decision'],'block');self.assertIn('context transition',blocked['reason'])
        self.assertEqual(self.event('Stop',stop_hook_active=True),{})
        self.assertEqual(self.event('Stop',stop_hook_active=False),{})
        self.event('UserPromptSubmit',prompt='Спасибо',turn_id='turn-thanks')
        self.assertEqual(self.event('Stop',stop_hook_active=False),{})

    def test_card_fits_the_native_context_limit(self):
        self.run_cmd('begin','--session','session-a','--cwd',str(self.cwd),'--source-file',str(self.source),
                     '--objective','О'*1800,'--next-step','Ш'*1800)
        cp=self.base/'long.json';cp.write_text(json.dumps({'status':'waiting','next_step':'Ш'*1800,'blocker':'Б'*1000,
            'operations':[{'id':'op-'+str(i),'kind':'review','status':'running','reference':'/'+'r'*590} for i in range(5)],
            'evidence':[]}))
        self.run_cmd('update','--session','session-a','--cwd',str(self.cwd),'--checkpoint',str(cp))
        for i in range(3): self.event('UserPromptSubmit',prompt='Сообщение '*80,turn_id='turn-'+str(i))
        card=self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext']
        self.assertLessEqual(len(card),3900);self.assertIn(str(self.root/'session-a/state.json'),card)
        self.assertIn('source-1.txt',card)

    def test_warning_and_card_together_fit_the_native_limit(self):
        self.run_cmd('begin','--session','session-a','--cwd',str(self.cwd),'--source-file',str(self.source),
                     '--objective','О'*1800,'--next-step','Ш'*1800)
        result=self.base/('r'*120+'.txt');result.write_text('Delivered')
        self.update('complete',result_file=str(result));result.rename(self.base/'moved.txt')
        for i in range(3): self.event('UserPromptSubmit',prompt='Сообщение '*80,turn_id='turn-'+str(i))
        out=self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext']
        self.assertLessEqual(len(out),3900);self.assertIn('unverified',out)

    def test_paused_work_after_a_user_message_stays_protected_without_guessing_intent(self):
        self.begin();self.event('Interrupt');self.event('Interrupt')
        state=self.root/'session-a/state.json'
        self.assertEqual(json.loads(state.read_text())['status_before_interrupt'],'active')
        card=self.event('UserPromptSubmit',prompt='продолжай',turn_id='turn-1')['hookSpecificOutput']['additionalContext']
        self.assertIn('asks to continue it, continue',card)
        self.assertEqual(json.loads(state.read_text())['status'],'paused')
        self.assertEqual(self.event('Stop',stop_hook_active=False),{})
        self.event('SessionStart',source='compact')
        blocked=self.event('Stop',last_assistant_message='Принял.',stop_hook_active=False)
        self.assertEqual(blocked['decision'],'block');self.assertIn('context transition',blocked['reason'])

    def test_compaction_check_is_requested_even_after_another_stop_hook(self):
        self.begin();result=self.base/'done.txt';result.write_text('Release delivered')
        self.update('complete',result_file=str(result))
        self.event('UserPromptSubmit',prompt='Опубликуй',turn_id='turn-p');self.event('SessionStart',source='compact')
        self.assertEqual(self.event('Stop',stop_hook_active=True)['decision'],'block')
        self.assertEqual(self.event('Stop',stop_hook_active=True),{})

    def test_repeated_text_without_turn_ids_keeps_message_order(self):
        self.begin()
        for text in ['A','B','A']: self.event('UserPromptSubmit',prompt=text)
        saved=json.loads((self.root/'session-a/state.json').read_text())['user_messages']
        self.assertEqual([m['preview'] for m in saved],['A','B','A'])

    def test_cancelled_card_does_not_claim_the_work_was_done(self):
        self.begin();decision=self.base/'cancel.txt';decision.write_text('User cancelled this task')
        self.update('cancelled',user_decision=str(decision))
        card=self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext']
        self.assertIn('cancelled by the user and may be unfinished',card);self.assertNotIn('was completed',card)

    def test_pause_card_marks_messages_sent_before_the_interrupt(self):
        self.begin();self.event('UserPromptSubmit',prompt='продолжай',turn_id='turn-0');self.event('Interrupt')
        card=self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext']
        self.assertIn('"after_interrupt": false',card);self.assertIn('sent before the interrupt does not resume',card)
        card=self.event('UserPromptSubmit',prompt='статус?',turn_id='turn-1')['hookSpecificOutput']['additionalContext']
        self.assertIn('"after_interrupt": true',card)
        reason=self.event('SessionStart',source='compact') and self.event('Stop',stop_hook_active=False)['reason']
        self.assertIn('only on a request sent after the interrupt',reason)

    def test_any_warning_with_the_card_stays_within_the_limit(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import conversation
        out=conversation.with_warning({'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':'c'*3900}},
                                      'SessionStart','w'*5000)
        self.assertLessEqual(len(out['hookSpecificOutput']['additionalContext']),3900)

    def test_objective_of_a_very_long_plan_names_the_blocks_it_omits(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import conversation
        objective=conversation.macro_objective({'task_key':'k','work_plan':[{'goal':'G'*200} for _ in range(60)]})
        self.assertLessEqual(len(objective),1800);self.assertRegex(objective,r'\+\d+ more blocks in the Council work plan$')

    def test_a_second_interrupt_moves_the_boundary(self):
        self.begin();self.event('Interrupt');self.event('UserPromptSubmit',prompt='продолжай',turn_id='turn-0');self.event('Interrupt')
        card=self.event('SessionStart',source='compact')['hookSpecificOutput']['additionalContext']
        self.assertIn('"after_interrupt": false',card);self.assertNotIn('"after_interrupt": true',card)

