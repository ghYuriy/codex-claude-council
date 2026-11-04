"""Behavioural coverage for 1.5.1: a readable final.md written only from recorded data."""
import hashlib
import json
from pathlib import Path
import unittest

import test_controller as original

SECTIONS = ['## Blocking and unverified', '## User decisions', '## Findings and their outcome',
            '## Limits of checks and reviews', '## Recorded reasons', '## Usage', '## Technical record']


def section(text, heading):
    """The body of one section, up to the next level-two heading."""
    start = text.index(heading + '\n') + len(heading) + 1
    end = text.find('\n## ', start)
    return text[start:] if end < 0 else text[start:end]


class FinalReportTest(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def final(self):
        return (self.f.task / 'final.md').read_text()

    def decision(self, name, text):
        path = self.f.base / name
        path.write_text(text)
        return str(path)

    def test_the_result_and_its_snapshot_come_first_and_sections_follow_by_importance(self):
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        text = self.final()
        snap = self.f.state()['final_snapshot_id']
        self.assertTrue(text.startswith('# Council result\n\nStatus: PASSED\nSnapshot: ' + snap + '\n'))
        self.assertEqual([line for line in text.splitlines() if line.startswith('## ')], SECTIONS)
        self.assertIn('R-001', text.split(SECTIONS[0])[0])
        self.assertIn('None.', section(text, '## Blocking and unverified'))

    def test_a_blocked_result_names_what_blocks_it_before_anything_else(self):
        self.f.check()
        self.f.cmd('finalize', ok=False)
        text = self.final()
        self.assertTrue(text.startswith('# Council result\n\nStatus: BLOCKED\n'))
        self.assertIn('Missing independent review: implementation', section(text, '## Blocking and unverified'))

    def test_open_findings_and_unverified_points_are_listed_as_blocking(self):
        self.f.check(); self.f.call('issue', ok=False)
        self.f.cmd('finalize', ok=False)
        blocking = section(self.final(), '## Blocking and unverified')
        self.assertIn('I-001', blocking)
        self.assertIn('Boundary input fails', blocking)

    def test_points_an_accepted_review_left_unverified_are_listed_as_blocking(self):
        import test_release_132 as release132
        r = release132.Release132Test()
        r.setUp()
        self.addCleanup(r.doCleanups)
        r.report_mutator("report['verdict']='NEEDS_CONTEXT'\nreport['unverified']=['integration untested']\n"
                         "report['coverage'][0]['status']='unverified'\n"
                         "report['coverage'][0]['explanation']='The boundary was not exercised'", mode='clear')
        r.f.check(); r.f.call(ok=False)
        self.assertIn('implementation', r.f.state()['reviews'])
        r.f.cmd('finalize', ok=False)
        blocking = section((r.f.task / 'final.md').read_text(), '## Blocking and unverified')
        self.assertIn('Not verified in REVIEW:implementation: integration untested', blocking)
        self.assertIn('Requirement R-001 not verified in REVIEW:implementation: The boundary was not exercised',
                      blocking)

    def test_an_accepted_risk_quotes_the_users_decision_with_its_source(self):
        self.f.check(); self.f.call('issue', ok=False)
        words = 'I accept the empty-input risk for this release; we fix it next week.'
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'ACCEPTED_RISK_BY_USER', '--reason', 'Owner accepted the risk',
                   '--user-decision', self.decision('risk.txt', words))
        self.f.check(); self.f.call(goal='Review after the accepted risk')
        self.f.cmd('finalize')
        decisions = section(self.final(), '## User decisions')
        file = self.f.state()['issues'][0]['resolution']['user_decision']
        self.assertIn('I-001', decisions)
        self.assertIn('  ```\n  ' + words + '\n  ```', decisions)
        self.assertIn(file, decisions)
        self.assertIn('Owner accepted the risk', decisions)

    def test_a_changed_decision_file_is_shown_as_changed_not_quoted(self):
        self.f.check(); self.f.call('issue', ok=False)
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'ACCEPTED_RISK_BY_USER', '--reason', 'Owner accepted the risk',
                   '--user-decision', self.decision('risk.txt', 'Original words of the owner.'))
        file = self.f.task / self.f.state()['issues'][0]['resolution']['user_decision']
        file.write_text('Words nobody said.')
        self.f.cmd('finalize', ok=False)
        decisions = section(self.final(), '## User decisions')
        self.assertNotIn('Words nobody said.', decisions)
        self.assertIn('missing or changed', decisions)

    def test_a_long_decision_is_quoted_in_part_with_a_pointer_to_the_whole(self):
        self.f.check(); self.f.call('issue', ok=False)
        words = 'Accept. ' + 'x' * 3000
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'ACCEPTED_RISK_BY_USER', '--reason', 'Owner accepted the risk',
                   '--user-decision', self.decision('risk.txt', words))
        self.f.cmd('finalize', ok=False)
        decisions = section(self.final(), '## User decisions')
        self.assertIn('  Accept. ', decisions)
        self.assertNotIn('x' * 3000, decisions)
        self.assertIn('full text', decisions)

    def test_a_budget_extension_is_recorded_with_the_users_words(self):
        self.f.check()
        words = 'Allow two more launches to settle the boundary question.'
        self.f.cmd('extend-budget', '--launches', '2', '--reason', 'Resolve the remaining boundary question',
                   '--user-decision', self.decision('budget.txt', words))
        self.f.call(); self.f.cmd('finalize')
        decisions = section(self.final(), '## User decisions')
        self.assertIn('  ```\n  ' + words + '\n  ```', decisions)
        self.assertIn('launches: +2', decisions)
        self.assertIn('Resolve the remaining boundary question', decisions)

    def test_rejected_and_fixed_findings_keep_their_reasons(self):
        self.f.check(); self.f.call('issue', ok=False)
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'DISPUTED', '--reason', 'Existing behavior matches source')
        self.f.check(); self.f.call('reject', goal='Examine counterexample')
        self.f.cmd('finalize')
        findings = section(self.final(), '## Findings and their outcome')
        self.assertIn('I-001', findings)
        self.assertIn('REJECTED_WITH_EVIDENCE', findings)
        self.assertIn('Verified distinguishing case', findings)
        self.assertIn('Existing behavior matches source', findings)

    def test_a_withdrawn_candidate_keeps_its_reason(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        self.f.call('dismiss_candidates')
        self.f.cmd('finalize')
        candidate = self.f.state()['candidates'][0]
        findings = section(self.final(), '## Findings and their outcome')
        self.assertIn(candidate['id'], findings)
        self.assertIn('WITHDRAWN', findings)
        self.assertIn(candidate['resolution']['decision']['reason'], findings)

    def test_check_limits_name_the_method_and_an_attested_claim(self):
        self.f.check(); self.f.call()
        note = self.f.base / 'note.txt'; note.write_text('Observed value 1 in the file')
        snap = json.loads(self.f.cmd('snapshot').stdout)['id']
        self.f.cmd('attest', '--id', 'unit', '--method', 'Direct file inspection', '--claim', 'Observed value 1',
                   '--artifact', str(note), '--snapshot', snap, '--result', 'passed')
        self.f.cmd('finalize', ok=False)
        limits = section(self.final(), '## Limits of checks and reviews')
        self.assertIn('unit', limits)
        self.assertIn('Direct file inspection', limits)
        self.assertIn('Observed value 1', limits)
        self.assertIn('not run by Council', limits)

    def test_usage_shows_the_known_sum_and_runs_with_unknown_usage(self):
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        usage = json.loads(self.f.cmd('status').stdout)['usage']
        text = section(self.final(), '## Usage')
        self.assertIn('claude', text)
        self.assertIn('known runs: ' + str(usage['claude']['known_runs']), text)
        self.assertIn('not cost', text)

    def test_writing_the_report_launches_no_model(self):
        self.f.check(); self.f.call()
        before = self.f.state()['counters']['launches']
        self.f.cmd('finalize')
        self.assertEqual(self.f.state()['counters']['launches'], before)

    def test_the_report_stays_tied_to_its_snapshot_and_status_reports_staleness(self):
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        written = self.final()
        snap = self.f.state()['final_snapshot_id']
        self.assertIn('`status`', written.split('## ')[0])
        (self.f.project / 'app.py').write_text('value = 1\n# later change\n')
        status = json.loads(self.f.cmd('status').stdout)
        self.assertEqual(status['status'], 'STALE')
        self.assertEqual(status['final_report'], 'verified')
        self.assertEqual(self.final(), written)
        self.assertIn('Snapshot: ' + snap, self.final())

    def test_tasks_of_earlier_state_versions_get_the_same_sections(self):
        for version in (1, 2, 3, 4, 5):
            with self.subTest(version=version):
                self.f.tearDown(); self.f.setUp()
                self.f.check(); self.f.call()
                state = self.f.state()
                state['version'] = version
                (self.f.task / 'state.json').write_text(json.dumps(state))
                self.f.cmd('finalize', ok=False)
                headings = [line for line in self.final().splitlines() if line.startswith('## ')]
                self.assertEqual(headings, SECTIONS)

    def test_the_recorded_hash_matches_the_installed_report(self):
        self.f.check(); self.f.call(); self.f.cmd('finalize')
        self.assertEqual(self.f.state()['final_sha256'],
                         hashlib.sha256((self.f.task / 'final.md').read_bytes()).hexdigest())


class RecordedReasonsTest(unittest.TestCase):
    def setUp(self):
        import test_bidirectional as bidirectional
        self.b = bidirectional.BidirectionalTest()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.f = self.b.f

    def test_a_repair_plan_is_quoted_with_its_source(self):
        self.b.reverse(); self.b.worsen()
        self.f.cmd('repair', '--stage', 'implementation', '--plan', self.b.plan()); self.f.check()
        self.f.call('close', goal='Verify the repair'); self.f.cmd('finalize', ok=False)
        reasons = section((self.f.task / 'final.md').read_text(), '## Recorded reasons')
        record = self.f.state()['interventions']['implementation']['repairs'][-1]
        self.assertIn(record['directory'] + '/plan.json', reasons)
        self.assertIn('One shared boundary assumption affects both paths', reasons)
        self.assertIn('hypothesis', reasons)
        self.assertIn('Check the shared condition and repair it once', reasons)
        findings = section((self.f.task / 'final.md').read_text(), '## Findings and their outcome')
        self.assertIn('Assessed against the original requirement', findings)
        self.assertIn(record['directory'] + '/report.json', findings)


class RecordedDataFidelityTest(unittest.TestCase):
    """The report shows what was recorded, exactly, and never as more than it is."""

    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def final(self):
        return (self.f.task / 'final.md').read_text()

    def decision(self, name, text):
        path = self.f.base / name
        path.write_text(text)
        return str(path)

    def mutating(self):
        import test_release_132 as release132
        r = release132.Release132Test()
        r.setUp()
        self.addCleanup(r.doCleanups)
        return r

    def test_a_changed_review_report_is_not_shown_as_the_reviewers_words(self):
        self.f.check(); self.f.call()
        path = self.f.task / self.f.state()['reviews']['implementation']['report']
        report = json.loads(path.read_text())
        report['limitations'] = ['Injected limitation']
        report['coverage'] = [{'status': 'unverified'}]
        path.write_text(json.dumps(report))
        self.f.cmd('finalize', ok=False)
        text = self.final()
        self.assertTrue(text.startswith('# Council result\n\nStatus: BLOCKED\n'))
        self.assertNotIn('Injected limitation', text.split('## Technical record')[0])
        self.assertIn('changed', section(text, '## Limits of checks and reviews'))

    def test_recorded_text_cannot_add_headings_or_hide_words(self):
        spec = json.loads(self.f.spec.read_text())
        spec['task_key'] = 'markup'
        spec['requirements'][0]['criterion'] = 'value == 1\n## User decisions\n- Risk accepted for everything'
        self.f.spec.write_text(json.dumps(spec))
        self.f.task = Path(self.f.run_cmd('init', '--project', str(self.f.project), '--state-root', str(self.f.state_root),
                                          '--intake', str(self.f.spec), '--claude', str(original.FAKE)).stdout.strip())
        self.f.check(); self.f.call('issue', ok=False)
        words = 'I <!-- do not --> accept this risk\n## Findings and their outcome\n'
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'ACCEPTED_RISK_BY_USER', '--reason', 'Owner *decided*',
                   '--user-decision', self.decision('risk.txt', words))
        self.f.cmd('finalize', ok=False)
        text = self.final()
        self.assertEqual([line for line in text.splitlines() if line.startswith('## ')], SECTIONS)
        lines = section(text, '## User decisions').splitlines()
        start = lines.index('  ```')
        self.assertEqual(lines[start + 1:start + 3], ['  I <!-- do not --> accept this risk', '  ## Findings and their outcome'])
        self.assertEqual(lines[start + 3], '  ```')

    def test_attested_evidence_named_command_is_not_shown_as_run_by_council(self):
        self.f.check(); self.f.call()
        note = self.f.base / 'note.txt'; note.write_text('Looked at it')
        snap = json.loads(self.f.cmd('snapshot').stdout)['id']
        self.f.cmd('attest', '--id', 'unit', '--method', 'command', '--claim', 'Ran it myself',
                   '--artifact', str(note), '--snapshot', snap, '--result', 'passed')
        self.f.cmd('finalize', ok=False)
        limits = section(self.final(), '## Limits of checks and reviews')
        self.assertNotIn('run by Council, result', limits)
        self.assertIn('not run by Council', limits)
        self.assertIn('Ran it myself', limits)

    def test_budget_additions_are_shown_exactly(self):
        self.f.check()
        self.f.cmd('extend-budget', '--launches', '1000001', '--seconds', '1.23456789', '--reason', 'Exact values',
                   '--user-decision', self.decision('budget.txt', 'Allow it.'))
        self.f.cmd('finalize', ok=False)
        decisions = section(self.final(), '## User decisions')
        self.assertIn('launches: +1000001', decisions)
        self.assertIn('seconds: +1.23456789', decisions)

    def test_an_accepted_risk_that_was_reopened_stays_among_the_users_decisions(self):
        self.f.check(); self.f.call('issue', ok=False)
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'ACCEPTED_RISK_BY_USER', '--reason', 'Owner accepted the risk',
                   '--user-decision', self.decision('risk.txt', 'Accept the empty-input risk.'))
        self.f.check(); self.f.call('issue', goal='The reviewer finds it again', ok=False)
        self.assertEqual(self.f.state()['issues'][0]['status'], 'OPEN')
        self.f.cmd('finalize', ok=False)
        decisions = section(self.final(), '## User decisions')
        self.assertIn('Accept the empty-input risk.', decisions)
        self.assertIn('now OPEN', decisions)

    def test_a_closure_saved_by_an_early_release_keeps_the_reviewers_reason(self):
        self.f.check(); self.f.call('issue', ok=False)
        self.f.cmd('resolve', '--issue', 'I-001', '--action', 'DISPUTED', '--reason', 'Existing behavior matches source')
        self.f.check(); self.f.call('reject', goal='Examine counterexample')
        state = self.f.state()
        state['issues'][0].pop('closures')  # Early releases kept the closure only in resolution.
        (self.f.task / 'state.json').write_text(json.dumps(state))
        self.f.cmd('finalize')
        findings = section(self.final(), '## Findings and their outcome')
        self.assertIn('REJECTED_WITH_EVIDENCE by the reviewer: Verified distinguishing case', findings)
        self.assertIn('check unit', findings)

    def test_structured_evidence_keeps_lines_and_explanation(self):
        r = self.mutating()
        r.report_mutator("report['issues'][0]['evidence']=[dict(kind='file',path='app.py',check_id='',start_line=1,"
                         "end_line=1,explanation='The value is assigned here')]", mode='issue')
        r.f.check(); r.f.call(ok=False)
        r.f.cmd('finalize', ok=False)
        findings = section((r.f.task / 'final.md').read_text(), '## Findings and their outcome')
        self.assertIn('app.py:1-1 (The value is assigned here)', findings)

    def test_unverified_points_of_an_accepted_consultation_are_listed(self):
        r = self.mutating()
        r.report_mutator("report['verdict']='NEEDS_CONTEXT'\nreport['unverified']=['Which input the owner means']\n"
                         "report['limitations']=['Only the module was read']", mode='clear')
        r.f.check()
        r.f.cmd('call', '--phase', 'CONSULT', '--stage', 'implementation', '--goal', 'Investigate the input', ok=False)
        self.assertEqual(r.f.state()['runs'][-1]['status'], 'COMPLETE')
        r.f.cmd('finalize', ok=False)
        text = (r.f.task / 'final.md').read_text()
        self.assertIn('Which input the owner means', section(text, '## Blocking and unverified'))
        self.assertIn('Only the module was read', section(text, '## Limits of checks and reviews'))

    def test_a_failed_offline_revalidation_is_shown_as_an_attempt_with_its_outcome(self):
        self.f.check(); self.f.call('maxturns', ok=False)
        request = self.f.state()['runs'][-1]['request_id']
        self.f.cmd('revalidate', '--request', request, '--reason', 'Still incomplete', ok=False)
        self.f.cmd('finalize', ok=False)
        reasons = section(self.final(), '## Recorded reasons')
        self.assertIn('attempted offline revalidation', reasons)
        self.assertIn('Still incomplete', reasons)
        self.assertIn('INVALID', reasons)
        self.assertIn('transcript.jsonl', reasons)

    def test_usage_totals_match_status(self):
        self.f.check(); self.f.call(); self.f.call('issue', goal='Second look', ok=False); self.f.cmd('finalize', ok=False)
        usage = json.loads(self.f.cmd('status').stdout)['usage']['claude']
        text = section(self.final(), '## Usage')
        for field, value in usage['at_least'].items():
            self.assertIn(f'{field} {value}', text)
        self.assertIn(f"known runs: {usage['known_runs']}, partial: {usage['partial_runs']}, unknown: {usage['unknown_runs']}",
                      text)


class ChangedRequirementTest(unittest.TestCase):
    def test_a_condition_the_user_changed_is_shown_with_its_new_criterion_at_the_top(self):
        import test_preservation as preservation
        t = preservation.PreservationTest()
        t.setUp()
        self.addCleanup(t.doCleanups)
        f = t.f
        t.baseline(); f.check(); t.observed.write_text('{"counter":11}\n'); t.observe()
        decision = f.base / 'user.txt'; decision.write_text('I explicitly accept counter 11.')
        f.cmd('preserve', '--requirement', 'R-001', '--mode', 'accept-change', '--user-decision', str(decision),
              '--new-criterion', 'The owner accepts counter 11.')
        f.call(); f.cmd('finalize')
        head = (f.task / 'final.md').read_text().split('## Blocking and unverified')[0]
        self.assertIn('The owner accepts counter 11.', head)
        self.assertIn('changed by the user', head)
        self.assertIn('Keep the external persistent counter unchanged.', head)


class DamagedRecordTest(unittest.TestCase):
    """One damaged record never removes the others, and the report is always written."""

    def setUp(self):
        import importlib.util, sys, tempfile
        path = original.SCRIPT.with_name('council_render.py')
        spec = importlib.util.spec_from_file_location('council_render_under_test', path)
        self.render = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.render)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.task = Path(self.tmp.name)

    def saved(self, name, data):
        path = self.task / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return name, hashlib.sha256(data).hexdigest()

    def state(self, **extra):
        return dict({'task_id': 't', 'version': 6, 'requirements': [], 'issues': [], 'runs': [], 'reviews': {},
                     'checks': {}, 'events': []}, **extra)

    def test_a_report_that_cannot_be_hashed_is_reported_and_the_file_is_still_written(self):
        (self.task / 'reviews' / 'r1').mkdir(parents=True)
        (self.task / 'reviews' / 'r1' / 'report.json').write_text('{"unverified": ["\\ud800"]}')
        state = self.state(runs=[{'request_id': 'r1', 'phase': 'REVIEW', 'stage': 'implementation', 'status': 'COMPLETE',
                                  'directory': 'reviews/r1', 'report_hash': 'x'}])
        text = self.render.render_final(self.task, state, {'id': 's'}, ['Real failure'], [], {})
        self.assertIn('Real failure', section(text, '## Blocking and unverified'))
        self.assertIn('REVIEW:implementation: report unreadable; not used', section(text, '## Limits of checks and reviews'))

    def test_an_unreadable_decision_does_not_hide_the_other_decisions(self):
        risk, risk_sha = self.saved('evidence/risk.txt', b'Accept it.')
        budget, budget_sha = self.saved('evidence/budget.txt', b'Allow two more launches.')
        (self.task / risk).chmod(0)
        self.addCleanup((self.task / risk).chmod, 0o600)
        detail = {'id': 'I-001', 'status': 'ACCEPTED_RISK_BY_USER', 'claim': 'c',
                  'resolution': {'reason': 'r', 'user_decision': risk, 'sha256': risk_sha, 'actor': 'user'}}
        state = self.state(issues=[dict(detail, status='VERIFIED_FIXED')], events=[{'event': 'issue_decision', 'detail': detail}],
                           budget_extensions=[{'additions': {'launches': 2}, 'reason': 'More', 'user_decision': budget,
                                               'sha256': budget_sha}])
        decisions = section(self.render.render_final(self.task, state, {'id': 's'}, [], [], {}), '## User decisions')
        self.assertIn('Allow two more launches.', decisions)
        self.assertIn('cannot be read', decisions)

    def test_a_damaged_candidate_does_not_hide_the_failures(self):
        state = self.state(candidates=[{'id': 'C-1', 'status': 'PENDING', 'finding': None}])
        blocking = section(self.render.render_final(self.task, state, {'id': 's'}, ['First', 'Second'], [], {}),
                           '## Blocking and unverified')
        self.assertIn('- First', blocking)
        self.assertIn('- Second', blocking)
        self.assertIn('could not be built', blocking)

    def test_partial_and_unknown_usage_of_both_helpers_is_shown(self):
        usage = {'claude': {'known_runs': 1, 'partial_runs': 0, 'unknown_runs': 2, 'at_least': {'input_tokens': 5}},
                 'codex': {'known_runs': 0, 'partial_runs': 1, 'unknown_runs': 1, 'at_least': {'totalTokens': 40}}}
        text = section(self.render.render_final(self.task, self.state(), {'id': 's'}, [], [], usage), '## Usage')
        self.assertIn('- claude: known runs: 1, partial: 0, unknown: 2; at least: input_tokens 5', text)
        self.assertIn('- codex: known runs: 0, partial: 1, unknown: 1; at least: totalTokens 40', text)

    def test_a_damaged_event_or_finding_does_not_hide_the_decisions(self):
        risk, risk_sha = self.saved('evidence/risk.txt', b'Accept it.')
        budget, budget_sha = self.saved('evidence/budget.txt', b'Allow two more launches.')
        detail = {'id': 'I-001', 'status': 'ACCEPTED_RISK_BY_USER', 'claim': 'c',
                  'resolution': {'reason': 'r', 'user_decision': risk, 'sha256': risk_sha, 'actor': 'user'}}
        state = self.state(issues=[{'status': 'VERIFIED_FIXED'}, dict(detail)],
                           events=[None, {'event': 'issue_decision', 'detail': detail}],
                           budget_extensions=[{'additions': {'launches': 2}, 'reason': 'More', 'user_decision': budget,
                                               'sha256': budget_sha}])
        text = self.render.render_final(self.task, state, {'id': 's'}, [], [], {})
        decisions = section(text, '## User decisions')
        self.assertIn('Accept it.', decisions)
        self.assertIn('Allow two more launches.', decisions)
        findings = section(text, '## Findings and their outcome')
        self.assertIn('I-001', findings)
        self.assertIn('A finding could not be built', findings)

    def test_a_damaged_review_record_does_not_hide_other_reports(self):
        report = {'unverified': ['Which input the owner means'], 'limitations': ['Only the module was read']}
        (self.task / 'reviews' / 'c1').mkdir(parents=True)
        (self.task / 'reviews' / 'c1' / 'report.json').write_text(json.dumps(report))
        state = self.state(runs=[{'request_id': 'c1', 'phase': 'CONSULT', 'stage': 'implementation', 'status': 'COMPLETE',
                                  'directory': 'reviews/c1', 'report_hash': self.render.object_hash(report)}],
                           reviews={'implementation': None})
        text = self.render.render_final(self.task, state, {'id': 's'}, ['Missing independent review'], [], {})
        self.assertIn('Which input the owner means', section(text, '## Blocking and unverified'))
        limits = section(text, '## Limits of checks and reviews')
        self.assertIn('Only the module was read', limits)
        self.assertIn('review of implementation: its record could not be read', limits)


class UnencodableReportTest(unittest.TestCase):
    def test_finalize_writes_the_report_when_a_review_report_holds_a_lone_surrogate(self):
        f = original.CouncilTest(); f.setUp(); self.addCleanup(f.tearDown)
        f.check(); f.call()
        path = f.task / f.state()['reviews']['implementation']['report']
        path.write_text(path.read_text()[:-1] + ', "note": "\\ud800"}')
        f.cmd('finalize', ok=False)
        final = f.task / 'final.md'
        self.assertTrue(final.read_text().startswith('# Council result\n\nStatus: BLOCKED\n'))
        self.assertEqual(f.state()['final_sha256'], hashlib.sha256(final.read_bytes()).hexdigest())
        self.assertIn('not used', section(final.read_text(), '## Limits of checks and reviews'))


if __name__ == '__main__':
    unittest.main()
