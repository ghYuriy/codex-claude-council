"""Behavioural coverage for 1.5.2: analysis facets and units, report format 3 and state v7."""
import hashlib
import json
from pathlib import Path
import unittest

import test_controller as original
import test_bidirectional as bidirectional

POLICY = {'design_calls': 6, 'consult_calls': 4, 'review_calls': 4, 'launches': 16, 'transport_retries': 1,
          'per_call_timeout': 5, 'task_seconds': 300}


class Fixture:
    def __init__(self, test):
        self.test = test
        self.f = original.CouncilTest()
        self.f.setUp()
        test.addCleanup(self.f.tearDown)

    def wide(self, **changes):
        """A task with enough budget for several discussion calls."""
        def transform(data):
            data['policy'] = dict(POLICY)
            for key, value in changes.items():
                data[key] = value
        self.f.new_task(transform)

    def analysis(self, *units, name='analysis.json'):
        path = self.f.base / name
        path.write_text(json.dumps({'units': list(units)}))
        return str(path)

    def unit(self, uid='U-probe', facet='probe', obj='The value is read once at import', obligation='required', **extra):
        return dict({'id': uid, 'facet': facet, 'object': obj, 'requirement_ids': ['R-001'], 'materials': [],
                     'obligation': obligation}, **extra)

    def design(self, mode='clear', goal='Design the value handling', analysis=None, ok=True, phase='DESIGN'):
        extra = ('--analysis', analysis) if analysis else ()
        return self.f.cmd('call', '--phase', phase, '--stage', 'implementation', '--goal', goal, *extra, mode=mode, ok=ok)

    def request(self, index=-1):
        run = self.f.state()['runs'][index]
        return json.loads((self.f.task / run['directory'] / 'request.json').read_text())

    def units(self):
        return {u['id']: u for u in self.f.state()['analysis_units']}

    def status(self):
        return json.loads(self.f.cmd('status').stdout)


class FacetPlanTest(unittest.TestCase):
    def setUp(self):
        self.x = Fixture(self)
        self.x.wide()

    def test_the_first_design_call_asks_for_framing_and_premises_as_required_units(self):
        self.x.design()
        request = self.x.request()
        self.assertEqual(request['report_format'], 3)
        self.assertEqual(request['contract_version'], 3)
        units = request['analysis_units']
        self.assertEqual(sorted(u['facet'] for u in units), ['framing', 'premises'])
        self.assertTrue(all(u['object_source'] == 'auto' and u['obligation'] == 'required' for u in units))
        self.assertTrue(all(u['object'] == 'Design the value handling' for u in units))
        analysis = request['output_schema']['properties']['analysis']
        self.assertEqual(sorted(analysis['required']), ['framing', 'premises'])
        self.assertIn('position_changes', request['output_schema']['required'])
        self.assertIn('framing', request['contract_notes'])
        self.assertNotIn('premortem:', request['contract_notes'])
        self.assertTrue(all(u['status'] == 'DONE' for u in self.x.units().values()))

    def test_a_follow_up_in_the_same_session_does_not_repeat_completed_units(self):
        self.x.design()
        self.x.design(goal='Design the value handling after the answer')
        request = self.x.request()
        self.assertEqual(request['analysis_units'], [])
        self.assertNotIn('analysis', request['output_schema']['properties'])

    def test_a_follow_up_records_a_change_of_an_accepted_position(self):
        self.x.design()
        self.x.design(mode='facet_position_valid', goal='Reconsider after the new measurement')
        request = self.x.request()
        self.assertEqual(request['report_format'], 3)
        self.assertIn('position_changes', request['output_schema']['required'])
        self.assertNotIn('analysis', request['output_schema']['properties'])
        report = json.loads((self.x.f.task / self.x.f.state()['runs'][-1]['directory'] / 'report.json').read_text())
        self.assertEqual(report['position_changes'][0]['prior_request_id'], self.x.f.state()['runs'][0]['request_id'])

    def test_a_session_without_units_keeps_format_2_after_acceptance(self):
        self.x.f.check(); self.x.f.call(); self.x.f.call(goal='Review again after the check')
        self.assertEqual(self.x.request()['report_format'], 2)

    def test_a_review_without_units_keeps_report_format_2(self):
        self.x.f.check(); self.x.f.call()
        request = self.x.request()
        self.assertEqual(request['report_format'], 2)
        self.assertEqual(request['analysis_units'], [])
        self.assertNotIn('analysis', request['output_schema']['properties'])

    def test_a_substantial_block_adds_a_premortem_to_its_design(self):
        f = self.x.f
        (f.project / 'use.py').write_text('import app\nprint(app.value)\n')
        data = json.loads(f.spec.read_text())
        data.update(task_key='substantial-block', policy=dict(POLICY), scope=['app.py', 'use.py'])
        data['work_plan'][0].update(risk='substantial', impact=[{'route': ['app.py', 'use.py'], 'reason': 'use.py reads the value',
                                                               'evidence': ['use.py'], 'checks': ['unit']}])
        f.spec.write_text(json.dumps(data))
        p = f.run_cmd('init', '--project', str(f.project), '--state-root', str(f.state_root),
                      '--intake', str(f.spec), '--claude', str(original.FAKE))
        f.task = Path(p.stdout.strip())
        self.x.design()
        units = self.x.request()['analysis_units']
        self.assertEqual(sorted(u['facet'] for u in units), ['framing', 'premises', 'premortem'])
        premortem = next(u for u in units if u['facet'] == 'premortem')
        self.assertEqual(premortem['object'], data['work_plan'][0]['goal'])
        self.x.f.cmd('call', '--phase', 'CONSULT', '--stage', 'implementation', '--goal', 'Ask about the value')
        self.assertEqual(sorted(u['facet'] for u in self.x.request()['analysis_units']), ['framing', 'premises'])

    def test_a_lead_unit_waits_for_an_accepted_report_and_then_must_be_completed(self):
        self.x.design(analysis=self.x.analysis(self.x.unit()))
        first = self.x.request()
        self.assertNotIn('U-probe', [u['id'] for u in first['analysis_units']])
        self.assertNotIn('deferred_units', first)
        self.assertNotIn('U-probe', json.dumps(first))
        self.assertEqual(self.x.f.state()['runs'][-1]['deferred_units'], ['U-probe'])
        self.assertEqual(self.x.units()['U-probe']['status'], 'OPEN')
        self.assertIn('Required analysis unit is open: U-probe (probe, DESIGN:implementation)',
                      self.x.status()['acceptance_failures'])
        self.x.design(goal='Probe the import behaviour')
        second = self.x.request()
        self.assertEqual([u['id'] for u in second['analysis_units']], ['U-probe'])
        self.assertEqual(second['perspective'], 'follow_up')
        self.assertEqual(self.x.units()['U-probe']['status'], 'DONE')
        self.assertFalse(any('U-probe' in f for f in self.x.status()['acceptance_failures']))

    def test_a_deferred_review_unit_reaches_the_next_review_with_the_same_goal(self):
        self.x.f.check()
        review = self.x.unit(uid='U-claim', facet='support', obj='The value never changes')
        self.x.f.cmd('call', '--phase', 'REVIEW', '--stage', 'implementation', '--goal', 'Review', '--analysis',
                     self.x.analysis(review))
        self.assertEqual(self.x.request()['analysis_units'], [])
        self.x.f.cmd('call', '--phase', 'REVIEW', '--stage', 'implementation', '--goal', 'Review')
        self.assertEqual([u['id'] for u in self.x.request()['analysis_units']], ['U-claim'])
        self.assertEqual(self.x.units()['U-claim']['status'], 'DONE')

    def test_a_user_material_cited_alone_is_current_after_the_project_changed(self):
        self.x.design()
        (self.x.f.project / 'app.py').write_text('value = 1\n# reviewed\n')
        literal = self.x.unit(uid='U-user', facet='support', obj='Keep value equal to one', user_material='source')
        self.x.design(mode='facet_cite_material_only', goal='Check the stated requirement',
                      analysis=self.x.analysis(literal))
        self.assertEqual(self.x.units()['U-user']['status'], 'DONE')

    def test_a_project_file_with_the_same_path_never_stands_in_for_the_user_material(self):
        (self.x.f.project / 'evidence').mkdir()
        (self.x.f.project / 'evidence' / 'source-user.md').write_text('Something else entirely.')
        self.x.wide(task_key='evidence-dir', scope=['app.py', 'evidence'])
        literal = self.x.unit(uid='U-user', facet='support', obj='Keep value equal to one', user_material='source')
        self.x.design(mode='facet_cite_material_only', analysis=self.x.analysis(literal))
        sent = next(u for u in self.x.request()['analysis_units'] if u['id'] == 'U-user')
        self.assertNotEqual(sent['user_material']['path'], 'evidence/source-user.md')
        self.assertTrue((self.x.f.task / sent['user_material']['path']).is_file())
        self.assertEqual(self.x.units()['U-user']['status'], 'DONE')

    def test_a_retained_copy_of_the_user_material_keeps_its_exact_bytes(self):
        (self.x.f.project / 'evidence').mkdir()
        (self.x.f.project / 'evidence' / 'source-user.md').write_text('Something else entirely.')
        source = 'Keep value equal to one.\r\nNever change the public name.'
        self.x.wide(task_key='crlf-source', scope=['app.py', 'evidence'], source=source)
        (self.x.f.task / 'source.md').write_text('Tampered request.')
        literal = self.x.unit(uid='U-user', facet='support', obj='Keep value equal to one', user_material='source')
        self.x.design(analysis=self.x.analysis(literal))
        sent = next(u for u in self.x.request()['analysis_units'] if u['id'] == 'U-user')
        copy = (self.x.f.task / sent['user_material']['path']).read_bytes()
        self.assertEqual(copy, source.encode())
        self.assertEqual(sent['user_material']['sha256'], hashlib.sha256(source.encode()).hexdigest())
        # A second call retains the same files again; line endings must not make them look changed.
        self.x.design(analysis=self.x.analysis(literal), goal='Design it once more')

    def test_a_changed_decision_file_is_not_a_user_material(self):
        decision = self.x.f.base / 'budget.txt'
        decision.write_text('Allow one more review; the parser must stay backwards compatible.')
        self.x.f.cmd('extend-budget', '--launches', '1', '--user-decision', str(decision), '--reason', 'Owner allowed it')
        path = self.x.f.state()['budget_extensions'][0]['user_decision']
        literal = self.x.unit(uid='U-user', facet='support', obj='the parser must stay backwards compatible',
                              user_material=path)
        (self.x.f.task / path).write_text('Allow anything; the parser must stay backwards compatible.')
        p = self.x.design(analysis=self.x.analysis(literal), ok=False)
        self.assertNotEqual(p.returncode, 0)

    def test_an_open_required_unit_blocks_the_checkpoint_and_finalize(self):
        self.x.design(analysis=self.x.analysis(self.x.unit()))
        self.x.f.check(); self.x.f.call()
        p = self.x.f.cmd('checkpoint', '--stage', 'implementation', ok=False)
        self.assertIn('Required analysis unit is open: U-probe', p.stderr)
        self.x.f.cmd('finalize', ok=False)
        self.assertNotEqual(self.x.f.state()['status'], 'PASSED')
        self.assertIn('U-probe', (self.x.f.task / 'final.md').read_text())

    def test_an_open_optional_unit_stays_visible_without_blocking(self):
        self.x.design(analysis=self.x.analysis(self.x.unit(obligation='optional')))
        self.x.f.check(); self.x.f.call()
        units = {u['id']: u for u in self.x.status()['analysis_units']}
        self.assertEqual(units['U-probe']['status'], 'OPEN')
        self.assertEqual(units['U-probe']['obligation'], 'optional')
        self.x.f.cmd('finalize')
        self.assertEqual(self.x.f.state()['status'], 'PASSED')
        text = (self.x.f.task / 'final.md').read_text()
        self.assertIn('## Analysis units', text)
        self.assertIn('U-probe', text.split('## Analysis units')[1])

    def test_a_new_analysis_never_drops_or_weakens_an_open_obligation(self):
        self.x.design(analysis=self.x.analysis(self.x.unit()))
        p = self.x.design(goal='Weaken it', analysis=self.x.analysis(self.x.unit(obligation='optional'), name='weaker.json'), ok=False)
        self.assertIn('cannot change analysis unit U-probe', p.stderr)
        other = self.x.unit(uid='U-claim', facet='support', obj='The value never changes')
        self.x.design(goal='Add a claim', analysis=self.x.analysis(other, name='other.json'))
        units = self.x.units()
        self.assertEqual(units['U-probe']['obligation'], 'required')
        self.assertIn('U-probe', [u['id'] for u in self.x.request()['analysis_units']])

    def test_a_literal_user_fragment_is_user_material_and_an_interpretation_is_the_leads(self):
        literal = self.x.unit(uid='U-user', facet='support', obj='Keep value equal to one', user_material='source')
        read = self.x.unit(uid='U-read', facet='support', obj='Keep value equal to one, as the lead reads it',
                           user_material='source')
        self.x.design(analysis=self.x.analysis(literal, read))
        units = self.x.units()
        self.assertEqual(units['U-user']['object_source'], 'user')
        self.assertEqual(units['U-user']['user_material']['path'], 'source.md')
        self.assertEqual(units['U-read']['object_source'], 'lead')
        sent = [u['id'] for u in self.x.request()['analysis_units']]
        self.assertIn('U-user', sent)
        self.assertNotIn('U-read', sent)

    def test_a_tracked_file_outside_the_task_scope_is_not_a_unit_material(self):
        import subprocess
        f = self.x.f
        (f.project / 'other.py').write_text('positions = "earlier answer"\n')
        for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base']):
            subprocess.run(['git', '-C', str(f.project), *args], check=True)
        self.x.wide(task_key='git-scope')
        self.assertIn('other.py', json.loads(f.cmd('snapshot').stdout)['manifest']['files'])
        literal = self.x.unit(uid='U-user', facet='support', obj='Keep value equal to one', user_material='source',
                              materials=['other.py'])
        p = self.x.design(analysis=self.x.analysis(literal), ok=False)
        self.assertIn('Analysis materials must be scoped project files', p.stderr)

    def test_a_recorded_user_decision_used_as_material_can_be_cited(self):
        decision = self.x.f.base / 'budget.txt'
        decision.write_text('Allow one more review; the parser must stay backwards compatible.')
        self.x.f.cmd('extend-budget', '--launches', '1', '--user-decision', str(decision), '--reason', 'Owner allowed it')
        path = self.x.f.state()['budget_extensions'][0]['user_decision']
        literal = self.x.unit(uid='U-user', facet='support', obj='the parser must stay backwards compatible',
                              user_material=path)
        self.x.design(mode='facet_cite_material', analysis=self.x.analysis(literal))
        sent = next(u for u in self.x.request()['analysis_units'] if u['id'] == 'U-user')
        self.assertTrue(sent['user_material']['path'].startswith('evidence/'))
        self.assertIn(sent['user_material']['path'], self.x.f.state()['runs'][-1]['review_artifacts'])
        self.assertEqual(self.x.units()['U-user']['status'], 'DONE')

    def test_unit_materials_are_scoped_project_files_and_must_be_read(self):
        for bad in ('state.json', '../state/x', 'reviews/x/report.json'):
            with self.subTest(bad=bad):
                p = self.x.design(analysis=self.x.analysis(self.x.unit(materials=[bad])), ok=False)
                self.assertIn('Analysis materials must be scoped project files', p.stderr)
        self.assertEqual(self.x.f.state()['runs'], [])
        literal = self.x.unit(uid='U-user', facet='support', obj='Keep value equal to one', user_material='source',
                              materials=['app.py'])
        self.x.design(analysis=self.x.analysis(literal))
        self.assertIn('app.py', self.x.request()['required_files'])

    def test_an_analysis_file_is_checked_before_any_launch(self):
        cases = [
            ({'units': [self.x.unit(requirement_ids=['R-404'])]}, 'Analysis requirement_ids must come from this call'),
            ({'units': [self.x.unit(uid='bad id')]}, 'Analysis unit IDs must match'),
            ({'units': [self.x.unit(), self.x.unit()]}, 'Duplicate analysis unit ID'),
            ({'units': [self.x.unit(facet='opinion')]}, 'Unknown facet'),
            ({'units': [self.x.unit(obligation='maybe')]}, 'obligation must be required or optional'),
            ({'units': [dict(self.x.unit(), extra=1)]}, 'Unknown analysis unit fields'),
            ({'units': [self.x.unit(obj='  ')]}, 'needs an object'),
            ({'units': [self.x.unit(user_material='evidence/none.txt')]}, 'Unknown user material'),
            ({'plan': []}, 'Analysis file must be an object with units'),
        ]
        for data, message in cases:
            with self.subTest(message=message):
                path = self.x.f.base / 'case.json'
                path.write_text(json.dumps(data))
                p = self.x.design(analysis=str(path), ok=False)
                self.assertIn(message, p.stderr)
        self.assertEqual(self.x.f.state()['runs'], [])
        path = self.x.f.base / 'review.json'
        path.write_text(json.dumps({'units': [self.x.unit(facet='framing')]}))
        self.x.f.check()
        p = self.x.f.cmd(
            'call', '--phase', 'REVIEW', '--stage', 'implementation', '--goal', 'Review', '--analysis', str(path), ok=False)
        self.assertIn('Facet framing is not used in REVIEW', p.stderr)


class NotApplicableTest(unittest.TestCase):
    def setUp(self):
        self.x = Fixture(self)
        self.x.wide()

    def test_a_required_unit_marked_not_applicable_stays_open_until_the_user_waives_it(self):
        self.x.design(mode='facet_na')
        units = self.x.units()
        self.assertTrue(all(u['status'] == 'OPEN' for u in units.values()))
        self.assertTrue(all(u['not_applicable'][0]['explanation'] for u in units.values()))
        framing = next(uid for uid, u in units.items() if u['facet'] == 'framing')
        self.assertIn('Required analysis unit is open: ' + framing + ' (framing, DESIGN:implementation)',
                      self.x.status()['acceptance_failures'])
        decision = self.x.f.base / 'waive.txt'
        decision.write_text('The agreed task changes: the framing is fixed by the request, so no separate analysis is needed.')
        p = self.x.f.cmd('waive', '--unit', framing, '--reason', 'Owner fixed the framing', ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.x.f.cmd('waive', '--unit', framing, '--reason', 'Owner fixed the framing', '--user-decision', str(decision))
        self.assertEqual(self.x.units()[framing]['status'], 'WAIVED')
        self.assertFalse(any(framing in f for f in self.x.status()['acceptance_failures']))
        self.x.f.cmd('finalize', ok=False)
        units_text = (self.x.f.task / 'final.md').read_text().split('## Analysis units\n')[1].split('\n## ')[0]
        self.assertIn(framing + ' (framing, auto, required) in DESIGN:implementation: WAIVED', units_text)
        self.assertIn('Waived by the user: Owner fixed the framing', units_text)
        self.assertIn('The agreed task changes: the framing is fixed by the request, so no separate analysis is needed.', units_text)
        self.assertIn('Marked not applicable in request ', units_text)
        self.assertIn('the obligation stays open until the user decides otherwise', units_text)
        p = self.x.f.cmd('waive', '--unit', framing, '--reason', 'Again', '--user-decision', str(decision), ok=False)
        self.assertIn('Only an open analysis unit can be waived', p.stderr)
        saved = self.x.f.task / self.x.units()[framing]['waiver']['user_decision']
        saved.write_text('changed')
        self.assertIn('Analysis waiver decision missing or changed: ' + framing, self.x.status()['acceptance_failures'])

    def test_an_optional_unit_marked_not_applicable_is_closed_with_its_explanation(self):
        self.x.design()
        self.x.design(mode='facet_na', goal='Look at the optional claim',
                      analysis=self.x.analysis(self.x.unit(uid='U-claim', facet='support', obj='It is fast', obligation='optional')))
        unit = self.x.units()['U-claim']
        self.assertEqual(unit['status'], 'NOT_APPLICABLE')
        self.assertEqual(unit['result']['status'], 'not_applicable')


class FacetReportTest(unittest.TestCase):
    def setUp(self):
        self.x = Fixture(self)
        self.x.wide()

    def rejected(self, mode, *units, check=False):
        if check:
            self.x.f.check()
        analysis = self.x.analysis(*units) if units else None
        if units:
            self.x.design()
        p = self.x.design(mode=mode, goal='Examine ' + mode, analysis=analysis, ok=False)
        self.assertNotEqual(p.returncode, 0)
        run = self.x.f.state()['runs'][-1]
        self.assertEqual(run['status'], 'INVALID')
        return '\n'.join(run.get('report_errors', run['validation_errors']))

    def test_a_missing_or_repeated_unit_result_is_rejected(self):
        self.assertIn('expected one result per analysis unit', self.rejected('facet_missing'))
        self.assertIn('expected one result per analysis unit', self.rejected('facet_duplicate'))

    def test_a_blocking_framing_gap_cannot_come_with_clear(self):
        self.assertIn('A blocking gap requires NEEDS_CONTEXT or NEEDS_DECISION', self.rejected('facet_false_clear'))

    def test_a_decisive_unverified_premise_must_be_listed_as_unverified(self):
        self.assertIn('A decisive unverified premise must be quoted in unverified', self.rejected('facet_unverified_premise'))

    def test_a_unit_reference_is_checked_like_any_other_evidence(self):
        self.assertIn('analysis.', self.rejected('facet_bad_reference'))

    def test_a_performed_probe_needs_a_recorded_check(self):
        errors = self.rejected('facet_probe_unchecked', self.x.unit())
        self.assertIn('A performed probe must cite a recorded check', errors)

    def test_an_unsupported_claim_needs_a_finding_in_this_report(self):
        errors = self.rejected('facet_support_unsupported', self.x.unit(uid='U-claim', facet='support', obj='It is fast'))
        self.assertIn('An overstated or unsupported claim needs a linked issue in this report', errors)

    def test_a_position_change_must_name_an_accepted_report_of_this_session(self):
        self.assertIn('position_changes[0].prior_request_id', self.rejected('facet_position'))

    def test_a_rejected_format_3_report_keeps_its_finding_as_a_candidate(self):
        self.rejected('facet_issue')
        candidates = self.x.f.state()['candidates']
        self.assertEqual([c['finding']['claim'] for c in candidates], ['Boundary input fails'])
        self.x.f.check()
        self.x.design(mode='dismiss_candidates', goal='Examine facet_issue')
        self.assertEqual(self.x.f.state()['candidates'][0]['status'], 'WITHDRAWN')

    def test_an_added_deferred_unit_also_changes_the_plan(self):
        self.x.design(mode='facet_missing', ok=False)
        self.x.design(mode='facet_missing', ok=False, analysis=self.x.analysis(self.x.unit(obligation='optional')))
        request = self.x.request()
        self.assertEqual(request['report_feedback']['mode'], 'reassess')
        self.assertNotIn('U-probe', json.dumps(request))
        self.assertNotEqual(request['analysis_plan_sha256'], self.x.request(0)['analysis_plan_sha256'])

    def test_an_unverified_premise_or_not_applicable_result_cannot_cite_a_missing_file(self):
        self.assertIn('premises[0].source', self.rejected('facet_unverified_source_bad'))
        self.assertIn('premises[0].source', self.rejected('facet_na_bad_ref'))

    def test_a_malformed_position_change_does_not_hide_other_facet_errors(self):
        errors = self.rejected('facet_bad_prior_type')
        self.assertIn('position_changes[0].prior_request_id', errors)
        self.assertIn('premises[0].source', errors)
        self.assertNotIn('could not be fully checked', errors)

    def test_no_alternative_framing_needs_its_reason(self):
        self.assertIn('Give an alternative framing or the reason there is none', self.rejected('facet_framing_none'))

    def test_wrong_types_are_all_reported_and_the_finding_is_kept(self):
        errors = self.rejected('facet_bad_types')
        self.assertIn('report.unverified must be an array', errors)
        self.assertIn('gaps must be an array', errors)
        self.assertNotIn('TypeError', errors)
        self.assertEqual([c['finding']['claim'] for c in self.x.f.state()['candidates']], ['Boundary input fails'])

    def test_a_changed_unit_plan_turns_a_correction_into_reassessment(self):
        self.x.design(mode='facet_missing', ok=False)
        self.x.design(mode='facet_missing', ok=False)
        self.assertEqual(self.x.request()['report_feedback']['mode'], 'correct_report')
        literal = self.x.unit(uid='U-user', facet='support', obj='Keep value equal to one', user_material='source')
        self.x.design(analysis=self.x.analysis(literal), ok=False, mode='facet_missing')
        self.assertEqual(self.x.request()['report_feedback']['mode'], 'reassess')



class RecordedResultTest(unittest.TestCase):
    def test_a_unit_result_links_findings_by_their_ledger_ids(self):
        import sys
        sys.path.insert(0, str(original.SCRIPT.parent))
        self.addCleanup(sys.path.remove, str(original.SCRIPT.parent))
        import test_release_150
        council = test_release_150.module('council')
        state = {'analysis_units': [{'id': 'U-claim', 'facet': 'support', 'obligation': 'required', 'status': 'OPEN'}]}
        report = {'analysis': {'support': [{'unit_id': 'U-claim', 'status': 'assessed', 'explanation': 'x',
                                             'evidence': [], 'issue_ids': ['I-001']}]}}
        # Another session already used I-001 for a different finding, so this one is I-001-2 in the ledger.
        council.record_analysis(state, report, {'request_id': 'r1', 'issue_ids': {'I-001': 'I-001-2'}})
        unit = state['analysis_units'][0]
        self.assertEqual(unit['status'], 'DONE')
        self.assertEqual(unit['result']['issue_ids'], ['I-001-2'])
        self.assertEqual(report['analysis']['support'][0]['issue_ids'], ['I-001'])


class ResultIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.x = Fixture(self)
        self.x.wide()

    def test_a_unit_closed_by_an_earlier_report_needs_that_report_intact(self):
        self.x.design()
        self.x.design(goal='Design the value handling after the answer')
        first = self.x.f.state()['runs'][0]
        report = self.x.f.task / first['directory'] / 'report.json'
        self.assertFalse(any('Analysis result' in f for f in self.x.status()['acceptance_failures']))
        report.write_text(report.read_text().replace('Checked against', 'Changed against'))
        failures = self.x.status()['acceptance_failures']
        for unit in self.x.units():
            self.assertIn('Analysis result missing or changed: ' + unit, failures)


class RepairHandoffTest(unittest.TestCase):
    def test_the_replacement_reviewer_takes_over_open_units_of_the_review_it_supersedes(self):
        import sys
        sys.path.insert(0, str(original.SCRIPT.parent))
        self.addCleanup(sys.path.remove, str(original.SCRIPT.parent))
        import types
        import test_release_150
        council = test_release_150.module('council')
        unit = {'id': 'U-claim', 'facet': 'support', 'object': 'It is fast', 'object_source': 'lead',
                'requirement_ids': ['R-001'], 'materials': [], 'obligation': 'required', 'session_key': 'REVIEW:implementation',
                'stage': 'implementation', 'phase': 'REVIEW', 'status': 'OPEN'}
        state = {'version': 7, 'analysis_units': [unit], 'scope': [], 'project': '/nonexistent'}
        args = types.SimpleNamespace(analysis=None, phase='REVIEW', stage='implementation', goal='Verify the repair')
        sent, new, deferred = council.plan_analysis(args, None, state, {'manifest': {'files': {}}},
                                                    'REVIEW:implementation:claude-repair', [{'id': 'R-001'}],
                                                    'handoff_verification', False)
        self.assertEqual([u['id'] for u in sent], ['U-claim'])
        self.assertEqual((new, deferred), ([], []))


class SavedSchemaTest(unittest.TestCase):
    def test_a_request_with_a_contract_version_is_checked_against_its_saved_schema(self):
        import sys
        sys.path.insert(0, str(original.SCRIPT.parent))
        self.addCleanup(sys.path.remove, str(original.SCRIPT.parent))
        import test_release_150
        council = test_release_150.module('council')
        saved = {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
        self.assertIs(council.request_schema({'contract_version': 2, 'report_format': 2, 'output_schema': saved}), saved)
        self.assertEqual(council.request_schema({'report_format': 2, 'output_schema': saved}), council.report_schema(2))




class OpenUnitMaterialTest(unittest.TestCase):
    def test_an_open_unit_whose_decision_file_changed_is_refused_before_the_helper_runs(self):
        x = Fixture(self)
        x.wide()
        x.f.check(); x.f.call('issue', ok=False)
        decision = x.f.base / 'risk.txt'
        decision.write_text('I accept the empty-input risk; the public name must never change.')
        x.f.cmd('resolve', '--issue', 'I-001', '--action', 'ACCEPTED_RISK_BY_USER', '--reason', 'Owner accepted it',
                '--user-decision', str(decision))
        path = next(i for i in x.f.state()['issues'] if i['id'] == 'I-001')['resolution']['user_decision']
        literal = x.unit(uid='U-user', facet='support', obj='the public name must never change', user_material=path)
        x.design(mode='facet_na', analysis=x.analysis(literal))
        self.assertEqual(x.units()['U-user']['status'], 'OPEN')
        (x.f.task / path).write_text('I accept every risk.')
        launches = x.f.state()['counters']['launches']
        p = x.design(goal='Look at the claim again', ok=False)
        self.assertIn('User material changed after it was recorded: ' + path, p.stderr)
        self.assertEqual(x.f.state()['counters']['launches'], launches)


class LineEndingTest(unittest.TestCase):
    def test_a_request_with_crlf_line_endings_survives_a_second_call(self):
        x = Fixture(self)
        x.wide(source='Keep value equal to one.\r\nNever change the public name.')
        x.f.check(); x.f.call(); x.f.call(goal='Review again after the first answer')
        self.assertEqual(x.f.state()['runs'][-1]['status'], 'COMPLETE')
        retained = (x.f.task / 'evidence' / 'source-user.md').read_bytes()
        self.assertEqual(retained, b'Keep value equal to one.\r\nNever change the public name.')

    def test_an_unchanged_crlf_source_file_is_accepted_as_evidence(self):
        import sys
        sys.path.insert(0, str(original.SCRIPT.parent))
        self.addCleanup(sys.path.remove, str(original.SCRIPT.parent))
        import test_release_150
        council = test_release_150.module('council')
        x = Fixture(self)
        x.wide(source='Keep value equal to one.\r\nNever change the public name.')
        state = json.loads(x.f.cmd('status').stdout) and x.f.state()
        snap = json.loads(x.f.cmd('snapshot').stdout)
        council.reference_file(x.f.task, state, 'source.md', snap)
        (x.f.task / 'source.md').write_text('Keep value equal to one.\nNever change the public name.')
        with self.assertRaises(council.CouncilError):
            council.reference_file(x.f.task, state, 'source.md', snap)


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.x = Fixture(self)
        self.x.wide()

    def set_version(self, version):
        path = self.x.f.task / 'state.json'
        state = json.loads(path.read_text())
        state['version'] = version
        state.pop('analysis_units', None)
        path.write_text(json.dumps(state))

    def test_new_tasks_are_protocol_7(self):
        self.assertEqual(self.x.f.state()['version'], 7)
        self.assertEqual(self.x.f.state()['analysis_units'], [])

    def test_a_protocol_6_task_keeps_its_rules_and_refuses_analysis(self):
        self.set_version(6)
        p = self.x.design(analysis=self.x.analysis(self.x.unit()), ok=False)
        self.assertIn('Analysis units need protocol 7', p.stderr)
        self.x.design()
        request = self.x.request()
        self.assertEqual(request['report_format'], 2)
        self.assertNotIn('analysis_units', request)
        self.assertNotIn('analysis_units', self.x.f.state())
        p = self.x.f.cmd('waive', '--unit', 'U-x', '--reason', 'r', '--user-decision', str(self.x.f.spec), ok=False)
        self.assertIn('Analysis units need protocol 7', p.stderr)

    def test_an_unknown_newer_version_is_refused(self):
        self.set_version(8)
        p = self.x.f.cmd('status', ok=False)
        self.assertIn('Unsupported state version', p.stderr)


class ClaudeLeadTest(unittest.TestCase):
    def test_a_deferred_unit_blocks_the_checkpoint_until_the_codex_helper_answers_it(self):
        b = bidirectional.BidirectionalTest()
        b.setUp()
        self.addCleanup(b.doCleanups)
        b.reverse(lead='claude')
        x = Fixture.__new__(Fixture)
        x.test, x.f = self, b.f
        x.design(analysis=x.analysis(x.unit()))
        self.assertEqual(x.f.state()['runs'][-1]['provider'], 'codex')
        self.assertEqual(x.f.state()['runs'][-1]['deferred_units'], ['U-probe'])
        x.f.check(); x.f.call()
        p = x.f.cmd('checkpoint', '--stage', 'implementation', ok=False)
        self.assertIn('Required analysis unit is open: U-probe', p.stderr)
        x.design(goal='Probe the import behaviour')
        self.assertEqual(x.units()['U-probe']['status'], 'DONE')
        x.f.call(goal='Review after the probe')
        x.f.cmd('checkpoint', '--stage', 'implementation')


class BothDirectionsTest(unittest.TestCase):
    def setUp(self):
        self.b = bidirectional.BidirectionalTest()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)

    def answered(self, lead):
        self.b.reverse(lead=lead)
        f = self.b.f
        f.cmd('call', '--phase', 'DESIGN', '--stage', 'implementation', '--goal', 'Design the value handling')
        state = f.state()
        self.assertEqual(state['runs'][-1]['provider'], 'claude' if lead == 'codex' else 'codex')
        self.assertEqual(sorted(u['status'] for u in state['analysis_units']), ['DONE', 'DONE'])
        report = json.loads((f.task / state['runs'][-1]['directory'] / 'report.json').read_text())
        self.assertEqual(sorted(report['analysis']), ['framing', 'premises'])

    def test_codex_lead_with_a_claude_helper(self):
        self.answered('codex')

    def test_claude_lead_with_a_codex_helper(self):
        self.answered('claude')

if __name__ == '__main__':
    unittest.main()
