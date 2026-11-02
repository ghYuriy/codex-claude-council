"""Global installation and normal Council use must require no project setup."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import test_controller as original


class GlobalRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.records = self.f.base / 'conversations'
        self.env = dict(os.environ, CODEX_THREAD_ID='native-new-project')
        self.env.pop('CODEX_COMPANION_SESSION_ID', None)

    def initialize(self, key='automatic', source='Complete the work in this new project.', cwd=None):
        data=json.loads(self.f.spec.read_text());data.update(task_key=key,source=source)
        self.f.spec.write_text(json.dumps(data))
        return subprocess.run([sys.executable,'-B',str(original.SCRIPT),'init','--project',str(self.f.project),
            '--state-root',str(self.f.state_root),'--intake',str(self.f.spec),'--claude',str(original.FAKE),
            '--conversation-root',str(self.records)],env=self.env,cwd=cwd or self.f.project,capture_output=True,text=True)

    def test_init_automatically_binds_native_conversation_without_project_files(self):
        result=self.initialize();self.assertEqual(result.returncode,0,result.stderr)
        task=Path(result.stdout.strip());record=json.loads((self.records/'native-new-project/state.json').read_text())
        self.assertEqual(record['council_task'],str(task));self.assertEqual(record['status'],'active')
        self.assertEqual((self.records/'native-new-project'/record['source_file']).read_text(),json.loads(self.f.spec.read_text())['source'])
        self.assertFalse((self.f.project/'.codex').exists())
        event={'session_id':'native-new-project','cwd':str(self.f.project.resolve()),'hook_event_name':'SessionStart','source':'compact'}
        output=subprocess.run([sys.executable,'-B',str(original.SCRIPT.with_name('conversation.py')),'--root',str(self.records),'hook'],
                              input=json.dumps(event),capture_output=True,text=True,check=True)
        self.assertIn(str(task),json.loads(output.stdout)['hookSpecificOutput']['additionalContext'])

    def test_existing_active_objective_is_not_replaced_by_another_init(self):
        first=self.initialize();self.assertEqual(first.returncode,0,first.stderr)
        before=(self.records/'native-new-project/state.json').read_bytes()
        second=self.initialize('conflict','Another unrelated objective.')
        self.assertNotEqual(second.returncode,0);self.assertIn('existing conversation task',second.stderr.lower())
        self.assertEqual((self.records/'native-new-project/state.json').read_bytes(),before)

    def test_recover_keeps_the_existing_objective_and_waiting_operation(self):
        result=self.initialize();self.assertEqual(result.returncode,0,result.stderr)
        record=self.records/'native-new-project/state.json';data=json.loads(record.read_text())
        data['status']='waiting';data['next_step']='Read the saved operation, do not relaunch'
        data['operations']=[{'id':'waiting-one','kind':'review','status':'running','reference':'saved-run'}]
        record.write_text(json.dumps(data));before=record.read_bytes()
        recovered=subprocess.run([sys.executable,'-B',str(original.SCRIPT),'recover','--task',result.stdout.strip()],
                                  env=self.env,capture_output=True,text=True)
        self.assertEqual(recovered.returncode,0,recovered.stderr);self.assertEqual(record.read_bytes(),before)

    def test_new_task_after_completed_generation_binds_without_manual_begin(self):
        first=self.initialize();self.assertEqual(first.returncode,0,first.stderr);task=first.stdout.strip()
        event={'session_id':'native-new-project','cwd':str(self.f.base.resolve()),'hook_event_name':'SessionStart','source':'compact'}
        subprocess.run([sys.executable,'-B',str(original.SCRIPT.with_name('conversation.py')),'--root',str(self.records),'hook'],
                       input=json.dumps(event),capture_output=True,text=True,check=True)
        for action in [('check','--id','unit','--timeout','5','--',sys.executable,'-B','-c','assert True'),
                       ('call','--phase','REVIEW','--stage','implementation','--goal','Review the completed task'),('finalize',)]:
            result=subprocess.run([sys.executable,'-B',str(original.SCRIPT),action[0],'--task',task,*action[1:]],
                                  env=self.env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
        outcome=self.f.base/'result.txt';outcome.write_text('Verified delivery of the first task.')
        checkpoint=self.f.base/'done.json';checkpoint.write_text(json.dumps({'status':'complete','next_step':'','operations':[],
            'evidence':[],'result_file':str(outcome),'council_task':task}))
        result=subprocess.run([sys.executable,'-B',str(original.SCRIPT.with_name('conversation.py')),'--root',str(self.records),
            'update','--cwd',str(self.f.project),'--checkpoint',str(checkpoint)],env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.f.project=self.f.base/'next-project';self.f.project.mkdir();(self.f.project/'app.py').write_text('value=1\n')
        second=self.initialize('next','A new requested outcome after completed delivery.')
        self.assertEqual(second.returncode,0,second.stderr)
        record=json.loads((self.records/'native-new-project/state.json').read_text())
        self.assertEqual(record['generation'],2);self.assertEqual(record['council_task'],second.stdout.strip())
        self.assertEqual(json.loads((self.records/'native-new-project/previous-1.json').read_text())['status'],'complete')


    def test_passed_council_closes_its_conversation_and_card_follows_progress(self):
        data=json.loads(self.f.spec.read_text());data['work_plan'][0]['goal']='Preserve the numeric value'
        self.f.spec.write_text(json.dumps(data))
        result=self.initialize();self.assertEqual(result.returncode,0,result.stderr);task=result.stdout.strip()
        record=self.records/'native-new-project/state.json'
        self.assertIn('Preserve the numeric value',json.loads(record.read_text())['objective'])
        event={'session_id':'native-new-project','cwd':str(self.f.project.resolve()),'hook_event_name':'SessionStart','source':'compact'}
        hook=lambda: json.loads(subprocess.run([sys.executable,'-B',str(original.SCRIPT.with_name('conversation.py')),'--root',
            str(self.records),'hook'],input=json.dumps(event),capture_output=True,text=True,check=True).stdout)
        self.assertIn('council_progress',hook()['hookSpecificOutput']['additionalContext'])
        for action in [('check','--id','unit','--timeout','5','--',sys.executable,'-B','-c','assert True'),
                       ('call','--phase','REVIEW','--stage','implementation','--goal','Review the task'),('finalize',)]:
            outcome=subprocess.run([sys.executable,'-B',str(original.SCRIPT),action[0],'--task',task,*action[1:]],
                                   env=self.env,capture_output=True,text=True)
            self.assertEqual(outcome.returncode,0,outcome.stderr)
        saved=json.loads(record.read_text())
        self.assertEqual(saved['status'],'complete');self.assertEqual(saved['result_file'],str(Path(task)/'final.md'))
        prompt=dict(event,hook_event_name='UserPromptSubmit',prompt='Thanks',turn_id='turn-after-pass')
        subprocess.run([sys.executable,'-B',str(original.SCRIPT.with_name('conversation.py')),'--root',str(self.records),'hook'],
                       input=json.dumps(prompt),capture_output=True,text=True,check=True)
        stop=dict(event,hook_event_name='Stop',stop_hook_active=False)
        stopped=subprocess.run([sys.executable,'-B',str(original.SCRIPT.with_name('conversation.py')),'--root',str(self.records),'hook'],
                               input=json.dumps(stop),capture_output=True,text=True,check=True)
        self.assertNotIn('decision',json.loads(stopped.stdout))

    def run_council(self, task, *actions):
        for action in actions:
            outcome=subprocess.run([sys.executable,'-B',str(original.SCRIPT),action[0],'--task',task,*action[1:]],
                                   env=self.env,capture_output=True,text=True)
            self.assertEqual(outcome.returncode,0,outcome.stderr)
        return outcome

    def test_finalize_closes_only_unfinished_work_and_marks_its_own_runs(self):
        result=self.initialize();self.assertEqual(result.returncode,0,result.stderr);task=result.stdout.strip()
        self.run_council(task,('check','--id','unit','--timeout','5','--',sys.executable,'-B','-c','assert True'),
                         ('call','--phase','REVIEW','--stage','implementation','--goal','Review the task'))
        request=json.loads((Path(task)/'state.json').read_text())['runs'][-1]['request_id']
        record=self.records/'native-new-project/state.json';data=json.loads(record.read_text())
        data.update(status='waiting',operations=[{'id':request,'kind':'REVIEW','status':'running','reference':task}])
        record.write_text(json.dumps(data))
        outcome=self.run_council(task,('finalize',))
        saved=json.loads(record.read_text())
        self.assertEqual(saved['status'],'complete');self.assertEqual(saved['operations'][0]['status'],'finished')

    def test_finalize_leaves_a_record_waiting_for_the_user_open(self):
        result=self.initialize();self.assertEqual(result.returncode,0,result.stderr);task=result.stdout.strip()
        record=self.records/'native-new-project/state.json';data=json.loads(record.read_text())
        data.update(status='needs-user',blocker='Approval of the release wording is needed');record.write_text(json.dumps(data))
        outcome=self.run_council(task,('check','--id','unit','--timeout','5','--',sys.executable,'-B','-c','assert True'),
                                 ('call','--phase','REVIEW','--stage','implementation','--goal','Review the task'),('finalize',))
        self.assertIn('Conversation record left open',outcome.stderr)
        saved=json.loads(record.read_text())
        self.assertEqual(saved['status'],'needs-user');self.assertEqual(saved['blocker'],'Approval of the release wording is needed')

    def test_finalize_reports_a_conversation_record_it_could_not_find(self):
        result=self.initialize();self.assertEqual(result.returncode,0,result.stderr);task=result.stdout.strip()
        self.run_council(task,('check','--id','unit','--timeout','5','--',sys.executable,'-B','-c','assert True'),
                         ('call','--phase','REVIEW','--stage','implementation','--goal','Review the task'))
        # An older task saved no record root; its record in a custom root is not found in the default one.
        state=json.loads((Path(task)/'state.json').read_text());state.pop('conversation_recovery')
        (Path(task)/'state.json').write_text(json.dumps(state))
        outcome=self.run_council(task,('finalize',))
        self.assertEqual(json.loads((self.records/'native-new-project/state.json').read_text())['status'],'active')
        self.assertIn('Conversation record left open: No conversation record bound to this task was found',outcome.stderr)

    def test_objective_keeps_every_block_and_accepts_numeric_keys(self):
        sys.path.insert(0,str(original.SCRIPT.parent))
        import conversation
        objective=conversation.macro_objective({'task_key':7,'work_plan':[{'goal':'A'*1800},{'goal':'SECOND block'}]})
        self.assertIn('SECOND block',objective);self.assertLessEqual(len(objective),1800)

    def test_nested_shell_recovers_in_parent_chat_then_pins_native_cwd(self):
        nested=self.f.project/'nested';nested.mkdir()
        result=self.initialize(cwd=nested);self.assertEqual(result.returncode,0,result.stderr)
        def event(cwd,sid='native-new-project'):
            payload={'session_id':sid,'cwd':str(cwd.resolve()),'hook_event_name':'SessionStart','source':'compact'}
            result=subprocess.run([sys.executable,'-B',str(original.SCRIPT.with_name('conversation.py')),'--root',str(self.records),'hook'],
                                  input=json.dumps(payload),capture_output=True,text=True,check=True)
            return json.loads(result.stdout)
        recovered=event(self.f.base)
        self.assertIn(result.stdout.strip(),recovered['hookSpecificOutput']['additionalContext'])
        saved=json.loads((self.records/'native-new-project/state.json').read_text())
        self.assertEqual(saved['native_cwd'],str(self.f.base.resolve()))
        self.assertIn('does not match',event(self.f.project)['systemMessage'])
        unrelated=self.f.base/'unrelated';unrelated.mkdir()
        rejected=event(unrelated);self.assertIn('does not match',rejected['systemMessage'])
        self.assertNotIn(result.stdout.strip(),json.dumps(rejected))
        self.assertEqual(event(self.f.base,'another-native-id'),{})


    def test_old_manual_parent_record_binds_without_requiring_a_cwd_option(self):
        source=self.f.base/'original.txt';source.write_text('Complete the work in this new project.')
        begun=subprocess.run([sys.executable,'-B',str(original.SCRIPT.with_name('conversation.py')),'--root',str(self.records),
            'begin','--cwd',str(self.f.base),'--source-file',str(source),'--objective','Existing objective',
            '--next-step','Continue the existing task'],env=self.env,capture_output=True,text=True)
        self.assertEqual(begun.returncode,0,begun.stderr)
        result=self.initialize();self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads((self.records/'native-new-project/state.json').read_text())
        self.assertEqual(data['objective'],'Existing objective');self.assertEqual(data['cwd'],str(self.f.base.resolve()))
        self.assertNotIn('cwd_policy',data)


class NativeConfigFixture:
    def __init__(self, config):
        import copy
        self.config=copy.deepcopy(config);self.writes=[];self.file='/fixture/user/config.toml'
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def version(self):
        import hashlib
        return hashlib.sha256(json.dumps(self.config,sort_keys=True).encode()).hexdigest()
    def call(self,method,params):
        import copy,hashlib,re
        if method=='config/read':return {'config':copy.deepcopy(self.config),'layers':[
            {'name':{'type':'user','file':self.file,'profile':None},'version':self.version(),'config':copy.deepcopy(self.config)}]}
        if method=='config/batchWrite':
            assert params['expectedVersion']==self.version()
            for edit in params['edits']:
                parts=[json.loads(x) if x.startswith('"') else x for x in re.findall(r'"(?:\\.|[^"\\])*"|[^.]+',edit['keyPath'])]
                target=self.config
                for part in parts[:-1]:target=target.setdefault(part,{})
                target[parts[-1]]=copy.deepcopy(edit['value'])
            self.writes.append(copy.deepcopy(params));return {}
        if method=='hooks/list':
            events=[]
            for name,event in [('SessionStart','sessionStart'),('UserPromptSubmit','userPromptSubmit'),('Stop','stop'),('Interrupt','interrupt'),('PreToolUse','preToolUse')]:
                for i,group in enumerate(self.config.get('hooks',{}).get(name,[])):
                    for j,handler in enumerate(group['hooks']):
                        key=f'{self.file}:{name}:{i}:{j}';digest=hashlib.sha256(json.dumps(group,sort_keys=True).encode()).hexdigest()
                        state=self.config.get('hooks',{}).get('state',{}).get(key,{})
                        events.append({'key':key,'eventName':event,'command':handler['command'],'sourcePath':self.file,
                            'currentHash':digest,'trustStatus':'trusted' if state.get('trusted_hash')==digest else 'untrusted',
                            'enabled':state.get('enabled',True),'isManaged':False})
            return {'data':[{'cwd':'/fixture/project','hooks':events}]}
        raise AssertionError('Unexpected API or model call: '+method)


class GlobalInstallTest(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('global_hooks_under_test',original.SCRIPT.with_name('global_hooks.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

    def test_global_install_preserves_other_settings_and_is_idempotent(self):
        module=self.module();other={'matcher':'danger','hooks':[{'type':'command','command':'existing-guard'}]}
        config={'model':'unchanged-model','features':{'hooks':True},'projects':{'/unrelated':{'trust_level':'untrusted'}},
                'hooks':{'PreToolUse':[other],'Stop':[other],'state':{'unrelated':{'trusted_hash':'keep'}}}}
        client=NativeConfigFixture(config)
        receipt=module.configure('/fixture/skill/scripts/conversation.py','/fixture/records',client_factory=lambda _:client)
        before=json.dumps(client.config,sort_keys=True);writes=len(client.writes)
        again=module.configure('/fixture/skill/scripts/conversation.py','/fixture/records',client_factory=lambda _:client)
        self.assertEqual(json.dumps(client.config,sort_keys=True),before);self.assertEqual(len(client.writes),writes)
        self.assertEqual(receipt,again);self.assertFalse(receipt['project_configuration_required'])
        self.assertFalse(receipt['current_thread_verified'])
        self.assertEqual(receipt['verification_scope'],'fresh-config-host')
        for key in ('model','features','projects'):self.assertEqual(client.config[key],config[key])
        self.assertEqual(client.config['hooks']['Stop'][0],other);self.assertEqual(client.config['hooks']['PreToolUse'],[other])
        self.assertEqual(client.config['hooks']['state']['unrelated'],{'trusted_hash':'keep'})
        for event in ('Stop','Interrupt'):self.assertNotIn('additionalContextLimit',client.config['hooks'][event][-1]['hooks'][0])

    def test_explicit_disabled_hooks_are_not_overridden(self):
        module=self.module();client=NativeConfigFixture({'features':{'hooks':False}})
        with self.assertRaisesRegex(RuntimeError,'disabled'):
            module.configure('/fixture/conversation.py','/fixture/records',client_factory=lambda _:client)
        self.assertEqual(client.writes,[])

    def test_existing_record_root_is_not_silently_replaced(self):
        module=self.module();client=NativeConfigFixture({})
        module.configure('/fixture/conversation.py','/original/records',client_factory=lambda _:client)
        before=json.dumps(client.config,sort_keys=True)
        with self.assertRaisesRegex(RuntimeError,'different record arguments'):
            module.configure('/fixture/conversation.py','/different/records',client_factory=lambda _:client)
        self.assertEqual(json.dumps(client.config,sort_keys=True),before)


    def test_default_installer_and_global_repair_cli_share_idempotent_setup(self):
        import runpy,tempfile
        from unittest.mock import patch
        module=self.module();client=NativeConfigFixture({'model':'unchanged','hooks':{}})
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);source=original.SCRIPT.parent.parent
            (home/'.codex').mkdir();(home/'.codex/AGENTS.md').write_text('User instructions\n')
            actual_get=os.environ.get
            def environment_get(key,default=None):
                return default if key=='CODEX_HOME' else actual_get(key,default)
            with patch.dict(sys.modules,{'global_hooks':module}),patch.object(Path,'home',return_value=home),\
                 patch.object(sys,'path',[str(source/'scripts'),*sys.path]),\
                 patch.object(os.environ,'get',side_effect=environment_get),\
                 patch.object(module.ConfigClient,'__enter__',return_value=client),\
                 patch.object(module.ConfigClient,'__exit__',return_value=None):
                with patch.object(sys,'argv',['install.py','--codex','fixture-config-host']):
                    runpy.run_path(str(source/'scripts/install.py'),run_name='__main__')
                installed=home/'.codex/skills/codex-claude-council'
                self.assertTrue((installed/'scripts/global_hooks.py').is_file())
                instructions=(home/'.codex/AGENTS.md').read_bytes()
                self.assertTrue(instructions.startswith(b'User instructions\n'))
                self.assertIn(str(installed/'scripts/conversation.py').encode(),instructions)
                before=json.dumps(client.config,sort_keys=True);writes=len(client.writes)
                with patch.object(sys,'argv',['install.py','--codex','fixture-config-host']):
                    runpy.run_path(str(source/'scripts/install.py'),run_name='__main__')
                sys.path.insert(0,str(source/'scripts'))
                try:
                    with patch.object(sys,'argv',['conversation.py','install-hooks','--codex','fixture-config-host']):
                        runpy.run_path(str(installed/'scripts/conversation.py'),run_name='__main__')
                finally:sys.path.remove(str(source/'scripts'))
                self.assertEqual(json.dumps(client.config,sort_keys=True),before)
                self.assertEqual(len(client.writes),writes)
                self.assertEqual((home/'.codex/AGENTS.md').read_bytes(),instructions)
