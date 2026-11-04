"""Behavioural coverage for 1.5.3: retained copies keep the exact bytes of what they copy."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

import test_controller as original
import test_release_150
import test_release_152


def council():
    sys.path.insert(0, str(original.SCRIPT.parent))
    try:
        return test_release_150.module('council')
    finally:
        sys.path.remove(str(original.SCRIPT.parent))


class RejectedCopyTest(unittest.TestCase):
    """The Claude CLI writes the helper's answer as it is; a reformatted answer with CRLF line endings is still an
    answer, and its retained copy must stay citable."""

    def setUp(self):
        self.x = test_release_152.Fixture(self)
        self.x.wide()
        self.f = self.x.f

    def rejected_first(self, mode='incomplete_crlf'):
        self.f.check()
        self.f.call(mode, ok=False)
        run = self.f.state()['runs'][-1]
        self.assertEqual(run['status'], 'INVALID')
        original_bytes = (self.f.task / run['directory'] / 'stdout.json').read_bytes()
        self.assertIn(b'\r\n', original_bytes)
        return run, original_bytes, self.f.task / 'evidence' / ('rejected-' + run['request_id']) / 'stdout.json'

    def test_a_rejected_answer_with_crlf_is_copied_byte_for_byte_and_can_be_cited(self):
        run, original_bytes, copy = self.rejected_first()
        self.f.call(goal='Review again after the rejected answer')
        request = self.x.request()
        self.assertEqual(request['report_feedback']['raw_response'], str(copy.relative_to(self.f.task)))
        self.assertEqual(copy.read_bytes(), original_bytes)
        c = council()
        state = self.f.state()
        snap = json.loads(self.f.cmd('snapshot').stdout)
        c.reference_file(self.f.task, state, str(copy.relative_to(self.f.task)), snap)

    def test_an_old_copy_with_normalized_line_endings_is_replaced_by_the_exact_one(self):
        run, original_bytes, copy = self.rejected_first()
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(original_bytes.decode('utf-8').replace('\r\n', '\n').encode('utf-8'))
        self.f.call(goal='Review again after the rejected answer')
        self.assertEqual(copy.read_bytes(), original_bytes)

    def test_a_copy_changed_in_any_other_way_still_stops_the_call(self):
        run, original_bytes, copy = self.rejected_first()
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(original_bytes.replace(b'Read required files', b'Skipped every file'))
        launches = self.f.state()['counters']['launches']
        p = self.f.call(goal='Review again after the rejected answer', ok=False)
        self.assertIn('Retained snapshot artifact changed', p.stderr)
        self.assertEqual(self.f.state()['counters']['launches'], launches)

    def test_the_copy_behind_a_retained_candidate_is_restored_by_its_own_path(self):
        self.f.check()
        self.f.call('report_errors_crlf', ok=False)
        first = self.f.state()['runs'][-1]
        self.assertTrue(self.f.state()['candidates'])
        original_bytes = (self.f.task / first['directory'] / 'stdout.json').read_bytes()
        copy = self.f.task / 'evidence' / ('rejected-' + first['request_id']) / 'stdout.json'
        self.f.call('incomplete', goal='Review once more', ok=False)
        # The next call's feedback names the second answer; only the candidate path can repair the first copy.
        copy.write_bytes(original_bytes.decode('utf-8').replace('\r\n', '\n').encode('utf-8'))
        self.f.call('dismiss_candidates', goal='Resolve the candidates')
        request = self.x.request()
        self.assertNotEqual(request['report_feedback']['raw_response'], str(copy.relative_to(self.f.task)))
        self.assertEqual(copy.read_bytes(), original_bytes)
        self.assertEqual([c['status'] for c in self.f.state()['candidates']], ['WITHDRAWN'])

    def test_a_citation_restores_an_old_copy_even_after_a_report_was_accepted(self):
        run, original_bytes, copy = self.rejected_first()
        self.f.call(goal='Review again after the rejected answer')
        copy.write_bytes(original_bytes.decode('utf-8').replace('\r\n', '\n').encode('utf-8'))
        c = council()
        c.reference_file(self.f.task, self.f.state(), str(copy.relative_to(self.f.task)), json.loads(self.f.cmd('snapshot').stdout))
        self.assertEqual(copy.read_bytes(), original_bytes)
        copy.write_bytes(original_bytes.replace(b'Read required files', b'Skipped every file'))
        with self.assertRaises(c.CouncilError):
            c.reference_file(self.f.task, self.f.state(), str(copy.relative_to(self.f.task)), json.loads(self.f.cmd('snapshot').stdout))

    def test_an_unreadable_retained_text_stops_the_call_with_a_clear_reason(self):
        self.f.check(); self.f.call()
        retained = self.f.task / 'evidence' / 'source-user.md'
        retained.write_bytes(b'\xff not text')
        launches = self.f.state()['counters']['launches']
        p = self.f.call(goal='Review again', ok=False)
        self.assertIn('Retained snapshot artifact changed', p.stderr)
        self.assertEqual(self.f.state()['counters']['launches'], launches)
        self.assertEqual(retained.read_bytes(), b'\xff not text')


class DiffBytesTest(unittest.TestCase):
    """A text file in another encoding gives a Git diff that is not UTF-8; its retained copy must match its hash."""

    def setUp(self):
        self.x = test_release_152.Fixture(self)
        f = self.f = self.x.f
        (f.project / 'legacy.txt').write_bytes(b'cafe\n')
        for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base']):
            subprocess.run(['git', '-C', str(f.project), *args], check=True)
        (f.project / 'legacy.txt').write_bytes(b'caf\xe9\r\n')
        self.x.wide(task_key='latin-diff', scope=['app.py', 'legacy.txt'])
        self.snap = json.loads(f.cmd('snapshot').stdout)
        self.diff = f.task / 'evidence' / ('git-context-' + self.snap['git_context_id']) / 'diff.patch'
        self.raw = subprocess.run(['git', '-C', str(f.project), 'diff', '--no-ext-diff', '--no-textconv', '--binary', 'HEAD'],
                                  capture_output=True, check=True).stdout
        self.assertIn(b'\xe9', self.raw)

    def test_the_retained_diff_is_the_exact_git_output_and_can_be_cited(self):
        self.assertEqual(self.diff.read_bytes(), self.raw)
        c = council()
        c.reference_file(self.f.task, self.f.state(), str(self.diff.relative_to(self.f.task)), self.snap)

    def test_an_old_diff_with_replaced_bytes_is_replaced_by_the_exact_one(self):
        self.diff.write_bytes(self.raw.decode('utf-8', 'replace').encode('utf-8'))
        self.f.check(); self.f.call()
        self.assertEqual(self.diff.read_bytes(), self.raw)

    def test_a_diff_changed_in_any_other_way_still_stops_the_call(self):
        self.diff.write_bytes(self.raw.replace(b'cafe', b'tea!'))
        p = self.f.cmd('call', '--phase', 'REVIEW', '--stage', 'implementation', '--goal', 'Review', ok=False)
        self.assertIn('Retained snapshot artifact changed', p.stderr)

    def test_a_citation_of_the_current_diff_restores_an_old_copy_before_any_retain(self):
        self.diff.write_bytes(self.raw.decode('utf-8', 'replace').encode('utf-8'))
        c = council()
        c.reference_file(self.f.task, self.f.state(), str(self.diff.relative_to(self.f.task)), self.snap)
        self.assertEqual(self.diff.read_bytes(), self.raw)

    def test_a_diff_that_no_longer_matches_the_snapshot_is_never_saved(self):
        c = council()
        state = self.f.state()
        (self.f.project / 'legacy.txt').write_bytes(b'coffee\r\n')
        snap = c.snapshot(state)
        (self.f.project / 'legacy.txt').write_bytes(b'tea\r\n')
        with self.assertRaises(c.CouncilError) as raised:
            c.snapshot_artifacts(self.f.task, state, snap)
        self.assertIn('changed while its snapshot was being recorded', str(raised.exception))
        self.assertFalse((self.f.task / 'evidence' / ('snapshot-' + snap['id'])).exists())
        self.assertFalse((self.f.task / 'evidence' / ('git-context-' + snap['git_context_id'])).exists())

    def test_init_refused_for_a_changing_diff_can_simply_be_repeated(self):
        c = council()
        real, seen = c.git_diff, []
        def changing(state):
            seen.append(1)
            return real(state) if len(seen) == 1 else real(state) + b'+changed meanwhile\n'
        root = self.f.base / 'second-state'
        data = json.loads(self.f.spec.read_text())
        data['task_key'] = 'init-race'
        self.f.spec.write_text(json.dumps(data))
        args = c.parser().parse_args(['init', '--project', str(self.f.project), '--state-root', str(root),
                                      '--intake', str(self.f.spec), '--claude', str(original.FAKE),
                                      '--conversation-root', str(self.f.base / 'conversations')])
        c.git_diff = changing
        with self.assertRaises(c.CouncilError):
            c.initialize(args)
        self.assertEqual([p.name for p in root.iterdir() if not p.name.startswith('.')], [])
        c.git_diff = real
        with contextlib.redirect_stdout(io.StringIO()):
            c.initialize(args)
        self.assertEqual(len([p for p in root.iterdir() if not p.name.startswith('.')]), 1)

    def test_a_protocol_3_snapshot_diff_keeps_its_bytes_too(self):
        c = council()
        state = self.f.state()
        state['version'] = 3
        snap = c.snapshot(state)
        directory = Path(c.snapshot_artifacts(self.f.task, state, snap))
        self.assertEqual((directory / 'diff.patch').read_bytes(), self.raw)
        (directory / 'diff.patch').write_bytes(self.raw.decode('utf-8', 'replace').encode('utf-8'))
        c.snapshot_artifacts(self.f.task, state, snap)
        self.assertEqual((directory / 'diff.patch').read_bytes(), self.raw)


    def test_a_git_error_after_init_stops_the_call_instead_of_sending_an_empty_diff(self):
        self.f.check()
        (self.f.project / 'legacy.txt').write_bytes(b'tea\r\n')
        launches = self.f.state()['counters']['launches']
        with mock.patch.dict(os.environ, {'GIT_CONFIG_PARAMETERS': "'core.bigFileThreshold'='bogus'"}):
            p = self.f.call(goal='Review again', ok=False)
        self.assertIn('Cannot read the Git diff', p.stderr)
        self.assertEqual(self.f.state()['counters']['launches'], launches)

    def test_a_snapshot_never_records_a_failed_git_diff_as_an_empty_one(self):
        c = council()
        with mock.patch.dict(os.environ, {'GIT_CONFIG_PARAMETERS': "'core.bigFileThreshold'='bogus'"}):
            with self.assertRaises(c.CouncilError) as raised:
                c.snapshot(self.f.state())
        self.assertIn('Cannot read the Git diff', str(raised.exception))


    def test_a_repository_git_cannot_open_is_an_error_not_a_project_without_git(self):
        c = council()
        self.f.check()
        (self.f.project / 'legacy.txt').write_bytes(b'tea\r\n')
        launches = self.f.state()['counters']['launches']
        with mock.patch.dict(os.environ, {'GIT_CONFIG_PARAMETERS': 'invalid-format'}):
            with self.assertRaises(c.CouncilError) as raised:
                c.snapshot(self.f.state())
            p = self.f.call(goal='Review again', ok=False)
        self.assertIn('Cannot inspect the Git repository', str(raised.exception))
        self.assertIn('Cannot inspect the Git repository', p.stderr)
        self.assertEqual(self.f.state()['counters']['launches'], launches)

    def test_only_a_missing_head_lets_a_failed_comparison_count_as_empty(self):
        c = council()
        state = self.f.state()
        state.pop('base_ref', None)
        real = c.git
        for head_code, empty in ((1, True), (128, False)):
            def fake(project, *args, check=True):
                if args[0] == 'diff':
                    return subprocess.CompletedProcess(args, 128, b'', b'fatal: simulated')
                if args[:3] == ('rev-parse', '-q', '--verify'):
                    return subprocess.CompletedProcess(args, head_code, b'', b'')
                return real(project, *args, check=check)
            with self.subTest(head_code=head_code), mock.patch.object(c, 'git', fake):
                self.assertEqual(c.unborn_head(self.f.project, state), empty)
                if empty:
                    self.assertEqual(c.git_diff(state), b'')
                else:
                    with self.assertRaises(c.CouncilError):
                        c.git_diff(state)
        state['base_ref'] = 'HEAD'
        with mock.patch.object(c, 'git', fake):
            self.assertFalse(c.unborn_head(self.f.project, state))


    def test_a_task_created_with_git_stays_a_git_task_when_its_git_data_lives_elsewhere(self):
        c = council()
        self.assertTrue(self.f.state()['git_worktree'])
        self.f.check()
        moved = self.f.base / 'outside.git'
        shutil.move(str(self.f.project / '.git'), str(moved))
        relocated = {'GIT_DIR': str(moved), 'GIT_WORK_TREE': str(self.f.project)}
        (self.f.project / 'legacy.txt').write_bytes(b'tea\r\n')
        with mock.patch.dict(os.environ, relocated):
            self.assertTrue(c.snapshot(self.f.state())['git_context'])
        launches = self.f.state()['counters']['launches']
        with mock.patch.dict(os.environ, dict(relocated, GIT_CONFIG_PARAMETERS='invalid-format')):
            with self.assertRaises(c.CouncilError) as raised:
                c.snapshot(self.f.state())
            p = self.f.call(goal='Review again', ok=False)
        self.assertIn('Cannot inspect the Git repository', str(raised.exception))
        self.assertIn('Cannot inspect the Git repository', p.stderr)
        self.assertEqual(self.f.state()['counters']['launches'], launches)
        with mock.patch.dict(os.environ, relocated):
            self.f.check()
            self.f.call(goal='Review again')

    def test_git_that_cannot_open_the_project_is_no_git_only_without_any_sign_of_a_repository(self):
        c = council()
        failing = lambda project, *args, check=True: subprocess.CompletedProcess(args, 128, b'', b'fatal: simulated')
        nothing = lambda d: None
        for name, prepare, recorded, env, refused in (
                ('plain directory', nothing, {'git_worktree': False}, {}, False),
                ('old task without the record', nothing, {}, {}, False),
                ('.git directory', lambda d: (d / '.git').mkdir(), {'git_worktree': False}, {}, True),
                ('.git file', lambda d: (d / '.git').write_text('gitdir: elsewhere\n'), {'git_worktree': False}, {}, True),
                ('dangling .git link', lambda d: (d / '.git').symlink_to(d / 'missing'), {'git_worktree': False}, {}, True),
                ('task created with Git', nothing, {'git_worktree': True}, {}, True),
                ('old task with a base commit', nothing, {'base_ref': '0' * 40}, {}, True),
                ('GIT_DIR in the environment', nothing, {'git_worktree': False}, {'GIT_DIR': 'elsewhere'}, True),
                ('only GIT_WORK_TREE in the environment', nothing, {}, {'GIT_WORK_TREE': 'elsewhere'}, False),
                ('empty GIT_DIR', nothing, {'git_worktree': False}, {'GIT_DIR': ''}, False),
                ('empty GIT_DIR beside .git', lambda d: (d / '.git').mkdir(), {}, {'GIT_DIR': ''}, True),
                ('empty GIT_DIR in a task created with Git', nothing, {'git_worktree': True}, {'GIT_DIR': ''}, True)):
            with self.subTest(name), mock.patch.object(c, 'git', failing), mock.patch.dict(os.environ, env):
                for variable in ('GIT_DIR', 'GIT_WORK_TREE'):
                    if variable not in env:
                        os.environ.pop(variable, None)
                project = self.f.base / ('probe-' + name.replace(' ', '-').replace('.', ''))
                project.mkdir()
                prepare(project)
                state = dict(recorded, project=str(project))
                if refused:
                    with self.assertRaises(c.CouncilError):
                        c.git_toplevel(state)
                else:
                    self.assertIsNone(c.git_toplevel(state))

    def init_args(self, c, key):
        data = json.loads(self.f.spec.read_text())
        data['task_key'] = key
        self.f.spec.write_text(json.dumps(data))
        root = self.f.base / ('state-' + key)
        return root, c.parser().parse_args(['init', '--project', str(self.f.project), '--state-root', str(root),
                                            '--intake', str(self.f.spec), '--claude', str(original.FAKE),
                                            '--conversation-root', str(self.f.base / 'conversations')])

    def test_init_with_relocated_git_data_refuses_while_git_is_broken_and_succeeds_after(self):
        c = council()
        moved = self.f.base / 'outside.git'
        shutil.move(str(self.f.project / '.git'), str(moved))
        relocated = {'GIT_DIR': str(moved), 'GIT_WORK_TREE': str(self.f.project)}
        root, args = self.init_args(c, 'relocated-init')
        with mock.patch.dict(os.environ, dict(relocated, GIT_CONFIG_PARAMETERS='invalid-format')):
            with self.assertRaises(c.CouncilError) as raised:
                c.initialize(args)
        self.assertIn('Cannot inspect the Git repository', str(raised.exception))
        self.assertEqual([p.name for p in root.iterdir() if not p.name.startswith('.')], [])
        with mock.patch.dict(os.environ, relocated), contextlib.redirect_stdout(io.StringIO()):
            c.initialize(args)
        task = next(p for p in root.iterdir() if not p.name.startswith('.'))
        self.assertTrue(json.loads((task / 'state.json').read_text())['git_worktree'])

    def test_the_review_index_refuses_when_git_fails_after_the_snapshot(self):
        c = council()
        state = self.f.state()
        snap = c.snapshot(state)
        real = c.git
        def failing_root(project, *args, check=True):
            if args[:2] == ('rev-parse', '--show-toplevel'):
                return subprocess.CompletedProcess(args, 128, b'', b'fatal: simulated')
            return real(project, *args, check=check)
        with mock.patch.object(c, 'git', failing_root), self.assertRaises(c.CouncilError) as raised:
            c.review_index(self.f.task, state, snap, 'probe', [], {})
        self.assertIn('Cannot inspect the Git repository', str(raised.exception))



class GitAppearsLaterTest(unittest.TestCase):
    def test_a_task_created_without_git_refuses_once_relocated_git_data_appears_and_breaks(self):
        c = council()
        x = test_release_152.Fixture(self)
        x.wide()
        f = x.f
        self.assertFalse(f.state()['git_worktree'])
        outside = f.base / 'outside.git'
        relocated = {'GIT_DIR': str(outside), 'GIT_WORK_TREE': str(f.project)}
        with mock.patch.dict(os.environ, relocated):
            for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base']):
                subprocess.run(['git', *args], cwd=f.project, check=True)
            (f.project / 'app.py').write_text('value = 2\n')
            self.assertTrue(c.snapshot(f.state())['git_context'])
        with mock.patch.dict(os.environ, dict(relocated, GIT_CONFIG_PARAMETERS='invalid-format')):
            with self.assertRaises(c.CouncilError) as raised:
                c.snapshot(f.state())
        self.assertIn('Cannot inspect the Git repository', str(raised.exception))


    def test_a_work_tree_variable_alone_does_not_turn_a_project_without_git_into_a_broken_repository(self):
        x = test_release_152.Fixture(self)
        f = x.f
        with mock.patch.dict(os.environ, {'GIT_WORK_TREE': str(f.project)}):
            os.environ.pop('GIT_DIR', None)
            x.wide()
            self.assertFalse(f.state()['git_worktree'])
            f.check(); f.call()


class UnbornHeadTest(unittest.TestCase):
    def test_a_repository_without_a_first_commit_keeps_an_empty_diff(self):
        x = test_release_152.Fixture(self)
        subprocess.run(['git', '-C', str(x.f.project), 'init', '-q'], check=True)
        x.wide()
        c = council()
        snap = c.snapshot(x.f.state())
        self.assertEqual(snap['git_context']['diff_hash'], c.digest(b''))
        x.f.check(); x.f.call()



class NoGitSnapshotTest(unittest.TestCase):
    def test_a_snapshot_taken_without_git_keeps_an_empty_diff_after_a_repository_appears(self):
        c = council()
        for version in (7, 3):
            with self.subTest(version=version):
                x = test_release_152.Fixture(self)
                x.wide()
                f = x.f
                state = f.state()
                state['version'] = version
                (f.project / 'app.py').write_text('value = 1\n# ' + str(version) + '\n')
                snap = c.snapshot(state)
                for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base']):
                    subprocess.run(['git', '-C', str(f.project), *args], check=True)
                (f.project / 'app.py').write_text('value = 2\n')
                directory = Path(c.snapshot_artifacts(f.task, state, snap))
                diff = directory / 'diff.patch' if version < 4 else \
                    f.task / 'evidence' / ('git-context-' + snap['git_context_id']) / 'diff.patch'
                self.assertEqual(diff.read_bytes(), b'')
                c.reference_file(f.task, state, str(diff.relative_to(f.task)), snap)

if __name__ == '__main__':
    unittest.main()
