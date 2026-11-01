"""Verification must retain the program it ran and a usable review inventory."""
import json
from pathlib import Path
import sys
import unittest
import test_controller as original


class EvidenceInputTest(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.script = self.f.base / 'external-check.py'
        self.script.write_text('print("verified")\n')

    def check(self, *options):
        return self.f.cmd('check', '--id', 'unit', '--timeout', '5', *options,
                          '--', sys.executable, '-B', str(self.script))

    def test_changed_external_program_invalidates_previous_pass(self):
        self.check(); self.f.call(); self.f.cmd('finalize')
        self.script.write_text('print("different proof")\n')
        status = json.loads(self.f.cmd('status').stdout)
        self.assertEqual(status['status'], 'STALE', status)

    def test_input_source_is_retained_and_citable(self):
        self.check()
        check = self.f.state()['checks']['unit']
        source = next(item for item in check['inputs'] if item['path'] == str(self.script.absolute()))
        self.assertEqual((self.f.task / source['artifact']).read_bytes(), self.script.read_bytes())
        self.assertIn(source['artifact'], check['artifacts'])
        (self.f.task / source['artifact']).write_text('forged')
        self.f.call(ok=False); self.f.not_passed()

    def test_declared_indirect_input_and_deleted_source_invalidate(self):
        dependency = self.f.base / 'imported.py'; dependency.write_text('value=1\n')
        self.check('--input', str(dependency)); self.f.call(); self.f.cmd('finalize')
        dependency.unlink()
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['status'], 'STALE')

    def test_program_changed_during_check_is_not_a_pass(self):
        self.script.write_text('from pathlib import Path\nPath(__file__).write_text("print(42)\\n")\n')
        result = self.f.cmd('check', '--id', 'unit', '--timeout', '5', '--', sys.executable, '-B', str(self.script), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.f.state()['checks']['unit']['result'], 'failed')

    def test_review_has_small_index_with_check_program(self):
        self.check(); self.f.call()
        run = self.f.state()['runs'][-1]
        request = json.loads((self.f.task / run['directory'] / 'request.json').read_text())
        index = json.loads(Path(request['review_materials']).read_text())
        self.assertIn('full_manifest', index)
        self.assertLess(Path(request['review_materials']).stat().st_size, 10000)
        page = json.loads((Path(request['review_materials']).parent / 'checks-0001.json').read_text())
        inputs=json.loads((Path(request['review_materials']).parent / page[0]['inputs']['first_page']).read_text())
        self.assertEqual(inputs[0]['path'], str(self.script.absolute()))

    def test_large_inventory_is_paged_without_omitting_entries(self):
        import subprocess
        subprocess.run(['git','init','-q',str(self.f.project)],check=True)
        bulk=self.f.project/'bulk';bulk.mkdir()
        for number in range(1100):
            (bulk/('file-'+str(number).zfill(4)+'.txt')).write_text('data')
        self.check(); self.f.call()
        run=self.f.state()['runs'][-1]
        req=json.loads((self.f.task/run['directory']/'request.json').read_text())
        path=Path(req['review_materials']); index=json.loads(path.read_text())
        self.assertLess(path.stat().st_size,10000)
        self.assertGreater(Path(index['full_manifest']).stat().st_size,100000)
        entries=[]
        for number in range(1,index['changed']['pages']+1):
            page=path.parent/f'changed-{number:04}.json'
            self.assertLess(page.stat().st_size,64000)
            entries.extend(json.loads(page.read_text()))
        self.assertEqual(len(entries),1101)
        self.assertEqual(len({x['path'] for x in entries}),1101)

    def test_focused_question_links_original_source_without_inlining_catalogue(self):
        spec=json.loads(self.f.spec.read_text());spec['task_key']='focused-index'
        spec['source']='Original request.\n'+('Preserved detailed catalogue.\n'*1000)
        second=dict(spec['requirements'][0],id='R-002')
        spec['requirements'].append(second)
        spec['work_plan'][0]['steps'][0]['requirement_ids'].append('R-002')
        self.f.spec.write_text(json.dumps(spec))
        self.f.task=Path(self.f.run_cmd('init','--project',str(self.f.project),'--state-root',str(self.f.state_root),
                                      '--intake',str(self.f.spec),'--claude',str(original.FAKE)).stdout.strip())
        self.check()
        self.f.cmd('call','--phase','DESIGN','--stage','implementation','--requirements','R-001','--goal','Inspect only selected obligation')
        run=self.f.state()['runs'][-1];req=json.loads((self.f.task/run['directory']/'request.json').read_text())
        self.assertNotIn('original_task',req)
        self.assertEqual(Path(req['original_task_path']).read_text(),spec['source'])
        self.assertEqual([r['id'] for r in req['requirements']],['R-001'])
        self.assertEqual(len(json.loads(Path(req['all_requirements_path']).read_text())),2)
