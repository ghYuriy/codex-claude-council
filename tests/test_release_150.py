"""Behavioural coverage for 1.5.0: the helper's environment and usage accounting."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_controller as original
import test_bidirectional as bidirectional


def module(name):
    spec = importlib.util.spec_from_file_location(name + '_under_test', original.SCRIPT.with_name(name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def isolation():
    return module('council_isolation')


HOST = {'PATH': '/bin', 'HOME': '/home/u', 'USER': 'u', 'LANG': 'C.UTF-8', 'LC_CTYPE': 'C.UTF-8', 'TMPDIR': '/tmp/u',
        'HTTPS_PROXY': 'http://proxy', 'SSL_CERT_FILE': '/etc/ca.pem',
        'CODEX_THREAD_ID': 'host-thread', 'CODEX_COMPANION_SESSION_ID': 'host-session',
        'CLAUDE_CODE_SESSION_ID': 'host-claude', 'CLAUDECODE': '1', 'CLAUDE_CODE_MESSAGING_TOKEN': 'host-token',
        'CLAUDE_CODE_USER_EMAIL': 'someone@example.invalid',
        'UNRELATED_SECRET': 'value', 'PROJECT_TOKEN': 'value'}

# Variables that choose another sign-in method; the host running the tests may have set some of them.
SIGN_IN_SWITCHES = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_CUSTOM_HEADERS',
                    'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY', 'OPENAI_API_KEY',
                    'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'OPENAI_BASE_URL')


class HelperEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.iso = isolation()

    def test_the_helper_gets_only_the_base_set_and_its_saved_login(self):
        source = dict(HOST, CLAUDE_CONFIG_DIR='/home/u/.claude', CODEX_HOME='/home/u/.codex')
        for provider, login in (('claude', 'CLAUDE_CONFIG_DIR'), ('codex', 'CODEX_HOME')):
            env, names = self.iso.helper_env(provider, source, [])
            self.assertEqual(set(env), {'PATH', 'HOME', 'USER', 'LANG', 'LC_CTYPE', 'TMPDIR', 'HTTPS_PROXY',
                                        'SSL_CERT_FILE', login})
            self.assertEqual(names, sorted(env))
            self.assertEqual(env['HOME'], '/home/u')

    def test_other_ways_of_signing_in_never_reach_the_helper_and_cannot_be_added(self):
        # The subscription login is the only way: a key, a cloud provider or another address would move away from it.
        other = {'ANTHROPIC_API_KEY': 'k', 'ANTHROPIC_AUTH_TOKEN': 't', 'ANTHROPIC_BASE_URL': 'https://gateway.example',
                 'ANTHROPIC_CUSTOM_HEADERS': 'X: y', 'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_USE_VERTEX': '1',
                 'CLAUDE_CODE_USE_FOUNDRY': '1', 'ANTHROPIC_FOUNDRY_API_KEY': 'k', 'OPENAI_API_KEY': 'k',
                 'OPENAI_BASE_URL': 'https://gateway.example', 'CODEX_API_KEY': 'k', 'CODEX_ACCESS_TOKEN': 't'}
        source = dict(HOST, **other)
        for provider in ('claude', 'codex'):
            env, _ = self.iso.helper_env(provider, source, list(other), once=list(other))
            self.assertFalse(set(other) & set(env), provider)
        self.assertEqual(self.iso.refused_names(list(other)), list(other))

    def test_host_session_identifiers_never_pass_even_when_named(self):
        env, _ = self.iso.helper_env('claude', HOST, ['PROJECT_TOKEN'], once=['UNRELATED_SECRET'])
        self.assertIn('PROJECT_TOKEN', env)
        self.assertIn('UNRELATED_SECRET', env)
        for name in ('CODEX_THREAD_ID', 'CODEX_COMPANION_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CLAUDECODE',
                     'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_CODE_BRIDGE_SESSION_ID', 'CLAUDE_CODE_REMOTE_SESSION_ID',
                     'CLAUDE_CODE_CLOUD_SESSION_ID', 'CLAUDE_CODE_REMOTE_SESSION_UUID', 'CLAUDE_CODE_SESSION_ACCESS_TOKEN',
                     'CODEX_SESSION_ID'):
            self.assertEqual(self.iso.refused_names([name]), [name])
            source = dict(HOST, **{name: 'host-value'})
            self.assertNotIn(name, self.iso.helper_env('claude', source, [name], once=[name])[0])

    def test_names_must_be_plain_environment_names(self):
        self.assertEqual(self.iso.refused_names(['GOOD_NAME', 'bad-name', '', '1X', 'A=B']), ['bad-name', '', '1X', 'A=B'])

    def test_variables_of_the_clis_own_namespaces_cannot_be_added(self):
        # Each release of a CLI may read new variables of its own namespace (another sign-in method, a session
        # identifier), so none of them can be added, known or not; nor can a Bedrock sign-in token outside them.
        own = ['CLAUDE_CODE_USE_MANTLE', 'CLAUDE_CODE_USE_ANTHROPIC_AWS', 'CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD',
               'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_AWS_API_KEY', 'ANTHROPIC_AWS_BASE_URL', 'ANTHROPIC_FOUNDRY_AUTH_TOKEN',
               'CLAUDE_RUNNER_SESSION_ID', 'CLAUDE_RUNNER_SESSION_UUID', 'OPENAI_FEDERATION_RULE_ID',
               'OPENAI_IDENTITY_TOKEN_FILE', 'CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION', 'CODEX_TEST_SESSION_ID',
               'ANTHROPIC_MODEL', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'AWS_BEARER_TOKEN_BEDROCK']
        self.assertEqual(self.iso.refused_names(own + ['PROJECT_TOKEN', 'MY_CLAUDE_NOTES', 'AWS_PROFILE']), own)
        source = dict(HOST, **{name: 'v' for name in own})
        for provider, kept in (('claude', {'ANTHROPIC_MODEL', 'CLAUDE_CONFIG_DIR'}), ('codex', {'CODEX_HOME'})):
            env, _ = self.iso.helper_env(provider, source, own, once=own)
            self.assertEqual(set(own) & set(env), kept, provider)

    def test_the_session_bus_passes_for_a_login_kept_in_the_system_keyring(self):
        source = dict(HOST, DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1/bus')
        for provider in ('claude', 'codex'):
            self.assertIn('DBUS_SESSION_BUS_ADDRESS', self.iso.helper_env(provider, source, [])[0])

    def test_settings_each_cli_reads_for_its_own_work_pass(self):
        own = {'claude': ['CLAUDE_CODE_CLIENT_CERT', 'CLAUDE_CODE_CLIENT_KEY', 'CLAUDE_CODE_CLIENT_KEY_PASSPHRASE',
                          'CLAUDE_CODE_CERT_STORE', 'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
                          'CLAUDE_CODE_EFFORT_LEVEL', 'DISABLE_TELEMETRY', 'DO_NOT_TRACK',
                          'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'],
               'codex': ['CODEX_CA_CERTIFICATE']}
        source = dict(HOST, **{name: 'v' for names in own.values() for name in names})
        for provider, names in own.items():
            env, _ = self.iso.helper_env(provider, source, [])
            for name in names:
                self.assertIn(name, env, (provider, name))
            for name in own['codex' if provider == 'claude' else 'claude']:
                self.assertNotIn(name, env, (provider, name))

    def test_a_task_created_before_1_5_0_inherits_everything_as_before(self):
        env, names = self.iso.helper_env('claude', HOST, None)
        self.assertEqual(env, HOST)
        self.assertEqual(names, sorted(HOST))

    def test_names_for_one_call_come_from_council_pass_env(self):
        self.assertEqual(self.iso.once_names({'COUNCIL_PASS_ENV': 'A_ONE, B_TWO,,A_ONE'}), ['A_ONE', 'B_TWO'])
        self.assertEqual(self.iso.once_names({}), [])


class HelperEnvironmentControllerTest(unittest.TestCase):
    """The controller applies the profile only to helper launches and records names, never values."""
    def setUp(self):
        self.f = original.CouncilTest()
        self.patch = mock.patch.dict('os.environ', {'COUNCIL_HOST_ONLY': 'host-value-9c2e', 'CODEX_THREAD_ID': 'host-thread'})
        self.patch.start()
        for name in SIGN_IN_SWITCHES:
            os.environ.pop(name, None)
        self.addCleanup(self.patch.stop)
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.log = self.f.base / 'claude.log'

    def launches(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def review(self, **kwargs):
        with mock.patch.dict('os.environ', {'COUNCIL_CLAUDE_LOG': str(self.log), **kwargs}):
            self.f.check(); self.f.call()

    def test_a_new_task_records_the_names_of_each_helper_and_uses_version_six(self):
        state = self.f.state()
        self.assertEqual(state['version'], 6)
        self.assertEqual(state['helper_env']['claude'], {'extra_names': []})

    def test_a_key_or_another_address_in_the_environment_never_reaches_the_reviewer(self):
        self.review(ANTHROPIC_API_KEY='key-value-5a1b', ANTHROPIC_BASE_URL='https://api.anthropic.com',
                    CLAUDE_CODE_USE_BEDROCK='1')
        names = self.launches()[-1]['env_names']
        for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_USE_BEDROCK'):
            self.assertNotIn(name, names)
        self.assertEqual(self.f.state()['runs'][-1]['env']['mode'], 'filtered')
        self.assertNotIn('key-value-5a1b', (self.f.task / 'state.json').read_text())

    def test_the_reviewer_gets_the_base_set_and_named_variables_but_not_the_rest(self):
        self.review()
        names = self.launches()[-1]['env_names']
        self.assertNotIn('COUNCIL_HOST_ONLY', names)
        self.assertNotIn('CODEX_THREAD_ID', names)
        self.assertIn('COUNCIL_FAKE_MODE', names)
        self.assertIn('HOME', names)
        run = self.f.state()['runs'][-1]
        self.assertEqual(run['env']['mode'], 'filtered')
        # macOS itself adds __CF_USER_TEXT_ENCODING to every child process.
        self.assertEqual(run['env']['names'], [n for n in names if n != '__CF_USER_TEXT_ENCODING'])
        self.assertNotIn('host-value-9c2e', (self.f.task / 'state.json').read_text())

    def test_names_added_for_one_call_are_recorded_for_that_call_only(self):
        self.review(COUNCIL_EXTRA_ONE='x', COUNCIL_PASS_ENV=os.environ['COUNCIL_PASS_ENV'] + ',COUNCIL_EXTRA_ONE')
        self.assertIn('COUNCIL_EXTRA_ONE', self.launches()[-1]['env_names'])
        self.assertIn('COUNCIL_EXTRA_ONE', self.f.state()['runs'][-1]['env']['once_names'])
        self.assertEqual(self.f.state()['helper_env']['claude']['extra_names'], [])

    def test_env_adds_a_name_to_an_existing_task_without_new_init_or_new_session(self):
        self.review()
        before = self.f.state()
        self.f.cmd('env', '--pass-env', 'COUNCIL_HOST_ONLY')
        after = self.f.state()
        self.assertEqual(after['helper_env']['claude']['extra_names'], ['COUNCIL_HOST_ONLY'])
        self.assertEqual(after['counters'], before['counters'])
        self.assertEqual(after['sessions'], before['sessions'])
        self.assertEqual([(e['name'], e['via']) for e in after['helper_env_log']], [('COUNCIL_HOST_ONLY', 'env')])
        with mock.patch.dict('os.environ', {'COUNCIL_CLAUDE_LOG': str(self.log)}):
            self.f.call(goal='Review again with the added name')
        self.assertIn('COUNCIL_HOST_ONLY', self.launches()[-1]['env_names'])
        self.assertEqual(self.f.state()['sessions'], after['sessions'])
        self.assertNotIn('host-value-9c2e', (self.f.task / 'state.json').read_text())

    def test_init_takes_names_for_the_whole_task(self):
        spec = json.loads(self.f.spec.read_text()); spec['task_key'] = 'with-names'; self.f.spec.write_text(json.dumps(spec))
        r = self.f.run_cmd('init', '--project', str(self.f.project), '--state-root', str(self.f.state_root),
                           '--intake', str(self.f.spec), '--claude', str(original.FAKE), '--pass-env', 'COUNCIL_HOST_ONLY')
        self.f.task = Path(r.stdout.strip())
        self.assertEqual(self.f.state()['helper_env']['claude']['extra_names'], ['COUNCIL_HOST_ONLY'])
        self.review()
        self.assertIn('COUNCIL_HOST_ONLY', self.launches()[-1]['env_names'])

    def test_host_session_identifiers_other_sign_in_and_malformed_names_are_refused_everywhere(self):
        for name in ('CODEX_THREAD_ID', 'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'OPENAI_FEDERATION_RULE_ID'):
            refused = self.f.cmd('env', '--pass-env', name, ok=False)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn(name, refused.stdout + refused.stderr)
        self.assertNotEqual(self.f.cmd('env', '--pass-env', 'not-a-name', ok=False).returncode, 0)
        spec = json.loads(self.f.spec.read_text()); spec['task_key'] = 'refused-at-init'; self.f.spec.write_text(json.dumps(spec))
        tasks = sorted(self.f.state_root.iterdir())
        refused = self.f.run_cmd('init', '--project', str(self.f.project), '--state-root', str(self.f.state_root),
                                 '--intake', str(self.f.spec), '--claude', str(original.FAKE),
                                 '--pass-env', 'CLAUDE_CODE_OAUTH_TOKEN', ok=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN', refused.stdout + refused.stderr)
        self.assertEqual(sorted(self.f.state_root.iterdir()), tasks, 'a refused name must stop init before the task is created')
        self.f.check()
        launches = self.f.state()['counters']['launches']
        with mock.patch.dict('os.environ', {'COUNCIL_PASS_ENV': 'CLAUDECODE'}):
            self.assertNotEqual(self.f.call(ok=False).returncode, 0)
        self.assertEqual(self.f.state()['counters']['launches'], launches, 'a refused name must stop the call before launch')
        self.assertEqual(self.f.state()['helper_env']['claude']['extra_names'], [])

    def test_status_shows_the_mode_and_names_without_values(self):
        status = self.f.cmd('status').stdout
        self.assertEqual(json.loads(status)['helper_env']['claude'], {'mode': 'filtered', 'extra_names': []})
        self.assertNotIn('host-value-9c2e', status)

    def test_a_changed_value_reaches_the_helper_and_no_value_is_written_by_the_controller(self):
        self.f.cmd('env', '--pass-env', 'COUNCIL_ROTATING')
        values = ('rotating-one-31f6', 'rotating-two-82c4')
        for index, value in enumerate(values):
            with mock.patch.dict('os.environ', {'COUNCIL_ROTATING': value, 'COUNCIL_FAKE_ECHO_NAME': 'COUNCIL_ROTATING'}):
                self.f.check() if index == 0 else None
                self.f.call(goal='Review with value ' + str(index))
        run = self.f.state()['runs'][-1]
        self.assertIn(values[1], (self.f.task / run['directory'] / 'stderr.txt').read_text())
        # Raw CLI output may hold values; everything the controller writes holds names only.
        for file in self.f.task.rglob('*'):
            if file.is_file() and file.name not in ('stderr.txt', 'stdout.json'):
                text = file.read_bytes().decode('utf-8', 'replace')
                for value in values:
                    self.assertNotIn(value, text, file)
        self.assertNotIn(values[1], self.f.cmd('status').stdout)

    def test_a_sign_in_failure_under_the_filter_names_the_way_to_add_a_variable(self):
        self.f.check()
        self.f.call('auth', ok=False)
        failure = json.loads(self.f.cmd('status').stdout)['last_failure']
        self.assertEqual(failure['error_kind'], 'AUTH')
        for part in ('/login', 'codex login', 'env --task', '--pass-env'):
            self.assertIn(part, failure['hint'])

    def old_task(self):
        state = self.f.state()
        state['version'] = 5
        state.pop('helper_env'); state.pop('helper_env_log', None)
        (self.f.task / 'state.json').write_text(json.dumps(state))

    def test_an_older_task_ignores_names_for_one_call_as_before(self):
        self.old_task()
        # The test harness drops CODEX_THREAD_ID from every command, so another host identifier stands in for it.
        self.review(CLAUDECODE='1', COUNCIL_PASS_ENV=os.environ['COUNCIL_PASS_ENV'] + ',CLAUDECODE,not-a-name')
        self.assertIn('CLAUDECODE', self.launches()[-1]['env_names'])
        self.assertEqual(self.f.state()['runs'][-1]['env']['mode'], 'inherited')

    def test_sign_in_errors_the_installed_clis_print_are_classified_as_sign_in_failures(self):
        sys.path.insert(0, str(original.SCRIPT.parent)); self.addCleanup(sys.path.remove, str(original.SCRIPT.parent))
        council = module('council')
        for text in ('Not logged in \u00b7 Run /login', 'Please run /login \u00b7 API Error: 401',
                     'Your session has expired. Please run /login to sign in again.', 'Invalid API key',
                     "Not signed in. Please run 'codex login' to sign in", 'Your access token could not be refreshed',
                     'Error: No API key found', 'Missing environment variable: `ACME_API_KEY`.',
                     'Could not load credentials from any providers', 'Missing credentials. Please pass one of apiKey'):
            self.assertEqual(council.classify_error(text), 'AUTH', text)
        self.assertNotEqual(council.classify_error('ECONNRESET while loading /login'), 'AUTH')

    def test_no_cli_is_run_to_decide_the_sign_in(self):
        # A CLI that would hang on any extra command must not delay or block the launch.
        with mock.patch.dict('os.environ', {'COUNCIL_FAKE_SIGN_IN': 'hang'}):
            self.review()
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual(self.f.state()['runs'][-1]['env']['mode'], 'filtered')

    def test_an_older_task_keeps_full_inheritance_and_refuses_env(self):
        self.old_task()
        self.assertNotEqual(self.f.cmd('env', '--pass-env', 'COUNCIL_HOST_ONLY', ok=False).returncode, 0)
        self.review()
        self.assertIn('COUNCIL_HOST_ONLY', self.launches()[-1]['env_names'])
        self.assertEqual(self.f.state()['runs'][-1]['env']['mode'], 'inherited')
        self.assertEqual(self.f.state()['version'], 5)

    def test_the_previous_release_refuses_to_open_a_version_six_task(self):
        root = Path(original.SCRIPT).parents[1]
        archive = subprocess.run(['git', '-C', str(root), 'archive', 'v1.4.5', 'scripts'], capture_output=True)
        if archive.returncode:
            self.skipTest('the v1.4.5 tag is not in this checkout')
        with tempfile.TemporaryDirectory() as old:
            subprocess.run(['tar', '-x', '-C', old], input=archive.stdout, check=True)
            p = subprocess.run([sys.executable, str(Path(old) / 'scripts/council.py'), 'status', '--task', str(self.f.task)],
                               capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Unsupported state version', p.stdout + p.stderr)


class CodexHelperEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest()
        self.patch = mock.patch.dict('os.environ', {'COUNCIL_HOST_ONLY': 'host-value-9c2e'})
        self.patch.start()
        for name in SIGN_IN_SWITCHES:
            os.environ.pop(name, None)
        self.addCleanup(self.patch.stop)
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.log = self.f.base / 'native.log'

    def test_the_codex_helper_gets_the_filtered_environment(self):
        b = bidirectional.BidirectionalTest('test_reverse_uses_identical_acceptance_and_native_resume')
        b.f = self.f
        with mock.patch.dict('os.environ', {'COUNCIL_NATIVE_LOG': str(self.log)}):
            b.reverse(); self.f.check(); self.f.call()
        launch = [json.loads(line)['launch'] for line in self.log.read_text().splitlines() if '"launch"' in line][-1]
        self.assertNotIn('COUNCIL_HOST_ONLY', launch['env_names'])
        self.assertIn('COUNCIL_FAKE_MODE', launch['env_names'])
        self.assertEqual(self.f.state()['runs'][-1]['env']['mode'], 'filtered')

    def test_the_launch_record_lists_what_council_passes_to_codex(self):
        # On macOS the Node runtime adds __CF_USER_TEXT_ENCODING to its own environment; the system adds it, not Council.
        b = bidirectional.BidirectionalTest('test_reverse_uses_identical_acceptance_and_native_resume')
        b.f = self.f
        with mock.patch.dict('os.environ', {'COUNCIL_NATIVE_LOG': str(self.log)}):
            b.reverse(); self.f.check(); self.f.call()
        launch = [json.loads(line)['launch'] for line in self.log.read_text().splitlines() if '"launch"' in line][-1]
        self.assertEqual(self.f.state()['runs'][-1]['env']['names'],
                         [n for n in launch['env_names'] if n != '__CF_USER_TEXT_ENCODING'])

    def test_a_codex_key_in_the_environment_never_reaches_codex(self):
        b = bidirectional.BidirectionalTest('test_reverse_uses_identical_acceptance_and_native_resume')
        b.f = self.f
        with mock.patch.dict('os.environ', {'COUNCIL_NATIVE_LOG': str(self.log), 'OPENAI_API_KEY': 'key-value-5a1b'}):
            b.reverse(); self.f.check(); self.f.call()
        self.assertEqual(self.f.state()['runs'][-1]['env']['mode'], 'filtered')
        launch = [json.loads(line)['launch'] for line in self.log.read_text().splitlines() if '"launch"' in line][-1]
        self.assertNotIn('OPENAI_API_KEY', launch['env_names'])
        self.assertNotIn('COUNCIL_HOST_ONLY', launch['env_names'])


CLAUDE_ENVELOPE = {'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': 's',
                   'usage': {'input_tokens': 4, 'cache_creation_input_tokens': 887, 'cache_read_input_tokens': 16278,
                             'output_tokens': 415, 'service_tier': 'standard'},
                   'modelUsage': {'model-a': {'inputTokens': 6, 'outputTokens': 453}}}
TOTAL_1 = {'totalTokens': 30410, 'inputTokens': 30364, 'cachedInputTokens': 0, 'outputTokens': 46, 'reasoningOutputTokens': 14}
TOTAL_2 = {'totalTokens': 64726, 'inputTokens': 64608, 'cachedInputTokens': 30208, 'outputTokens': 118, 'reasoningOutputTokens': 45}


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.usage = module('council_usage')

    def test_a_claude_run_takes_the_usage_of_that_call_and_keeps_the_session_total_apart(self):
        run = self.usage.claude_run(json.dumps(CLAUDE_ENVELOPE).encode())
        self.assertEqual(run['tokens'], {'input_tokens': 4, 'cache_creation_input_tokens': 887,
                                         'cache_read_input_tokens': 16278, 'output_tokens': 415})
        self.assertEqual(run['session'], CLAUDE_ENVELOPE['modelUsage'])
        self.assertEqual(run['reported'], CLAUDE_ENVELOPE['usage'])
        self.assertTrue(run['complete'])

    def test_claude_usage_with_missing_counters_keeps_what_was_reported_but_sums_nothing(self):
        envelope = dict(CLAUDE_ENVELOPE, usage={'input_tokens': 4, 'output_tokens': 415, 'service_tier': 'standard'})
        run = self.usage.claude_run(json.dumps(envelope).encode())
        self.assertIsNone(run['tokens'])
        self.assertEqual((run['reported'], run['session']), (envelope['usage'], CLAUDE_ENVELOPE['modelUsage']))
        self.assertEqual(self.usage.summarize([('claude', run)])['claude']['unknown_runs'], 1)

    def test_the_session_total_is_kept_even_without_the_call_usage(self):
        for usage in (None, 'absent'):
            envelope = dict(CLAUDE_ENVELOPE, usage=usage)
            if usage == 'absent':
                envelope.pop('usage')
            run = self.usage.claude_run(json.dumps(envelope).encode())
            self.assertEqual((run['tokens'], run['session']), (None, CLAUDE_ENVELOPE['modelUsage']))

    def test_claude_usage_that_was_not_received_is_unknown_not_zero(self):
        for raw in (b'', b'{', b'\xff', json.dumps({'type': 'result'}).encode(),
                    json.dumps({'type': 'result', 'usage': 'x'}).encode()):
            self.assertIsNone(self.usage.claude_run(raw), raw)
        self.assertIsNone(self.usage.claude_run(json.dumps(dict(CLAUDE_ENVELOPE, usage={'input_tokens': 'x'})).encode())['tokens'])

    def test_a_codex_run_is_the_difference_of_the_session_totals(self):
        run = self.usage.codex_run({'thread_id': 't', 'start': TOTAL_1, 'end': TOTAL_2})
        self.assertEqual(run['tokens'], {'totalTokens': 34316, 'inputTokens': 34244, 'cachedInputTokens': 30208,
                                         'outputTokens': 72, 'reasoningOutputTokens': 31})
        self.assertEqual(run['session'], TOTAL_2)
        self.assertFalse(run['complete'], 'a turn that did not complete gives only a lower bound')
        self.assertTrue(self.usage.codex_run({'start': TOTAL_1, 'end': TOTAL_2, 'completed': True})['complete'])

    def test_codex_usage_without_a_known_start_or_end_or_after_a_reset_is_unknown(self):
        for record in (None, {'start': None, 'end': TOTAL_2}, {'start': TOTAL_1, 'end': None},
                       {'start': TOTAL_2, 'end': TOTAL_1}, {'start': {}, 'end': TOTAL_2}):
            self.assertIsNone(self.usage.codex_run(record), record)
        self.assertIsNone(self.usage.codex_run({'start': TOTAL_1, 'end': TOTAL_2, 'went_back': True}))

    def test_a_codex_run_cannot_start_below_what_the_same_session_already_reported(self):
        record = {'thread_id': 't', 'start': TOTAL_1, 'end': TOTAL_2, 'completed': True}
        self.assertIsNone(self.usage.codex_run(record, earlier=[TOTAL_2]))
        self.assertEqual(self.usage.codex_run(record, earlier=[TOTAL_1])['tokens']['totalTokens'], 34316)

    def test_the_summary_says_at_least_how_much_and_how_many_runs_are_unknown(self):
        claude = self.usage.claude_run(json.dumps(CLAUDE_ENVELOPE).encode())
        codex = self.usage.codex_run({'start': TOTAL_1, 'end': TOTAL_2, 'completed': True})
        partial = self.usage.codex_run({'start': TOTAL_1, 'end': TOTAL_2})
        summary = self.usage.summarize([('claude', claude), ('claude', claude), ('claude', None), ('codex', codex),
                                        ('codex', partial)])
        self.assertEqual(summary['claude'], {'known_runs': 2, 'partial_runs': 0, 'unknown_runs': 1,
                                             'at_least': {f: 2 * v for f, v in claude['tokens'].items()}})
        self.assertEqual(summary['codex'], {'known_runs': 1, 'partial_runs': 1, 'unknown_runs': 0,
                                            'at_least': {f: 2 * v for f, v in codex['tokens'].items()}})


class UsageControllerTest(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_a_claude_review_records_its_usage_and_status_sums_it(self):
        self.f.check(); self.f.call()
        run = self.f.state()['runs'][-1]
        self.assertEqual(run['usage']['tokens']['output_tokens'], 11)
        status = json.loads(self.f.cmd('status').stdout)
        self.assertEqual(status['usage']['claude']['known_runs'], 1)
        self.assertEqual(status['usage']['claude']['at_least']['output_tokens'], 11)

    def test_an_interrupted_claude_run_has_unknown_usage(self):
        self.f.check(); self.f.call('hang_ignore_term', ok=False)
        self.assertIsNone(self.f.state()['runs'][-1]['usage'])
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['usage']['claude']['unknown_runs'], 1)

    def test_a_rejected_report_still_records_what_the_call_used(self):
        self.f.check(); self.f.call('report_errors', ok=False)
        self.assertEqual(self.f.state()['runs'][-1]['usage']['tokens']['output_tokens'], 11)


class CodexUsageTest(unittest.TestCase):
    def setUp(self):
        self.f = original.CouncilTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.totals = self.f.base / 'fake-usage.json'
        self.b = bidirectional.BidirectionalTest('test_reverse_uses_identical_acceptance_and_native_resume')
        self.b.f = self.f

    def calls(self, *modes):
        with mock.patch.dict('os.environ', {'COUNCIL_FAKE_USAGE': str(self.totals)}):
            self.b.reverse(); self.f.check()
            for index, mode in enumerate(modes):
                with mock.patch.dict('os.environ', {'COUNCIL_FAKE_USAGE_MODE': mode}):
                    self.f.call(goal='Review implementation, round ' + str(index), ok=False)
        return [r.get('usage') for r in self.f.state()['runs']]

    def test_a_new_thread_starts_from_zero_and_a_resume_from_the_replayed_total(self):
        first, second = self.calls('normal', 'normal')
        self.assertTrue(first['complete'] and second['complete'])
        self.assertEqual(first['tokens']['totalTokens'], 110)
        self.assertEqual(second['tokens']['totalTokens'], 110)
        self.assertEqual(second['session']['totalTokens'], 220)

    def test_a_resume_without_a_replayed_total_has_unknown_usage(self):
        self.assertIsNone(self.calls('normal', 'no_replay')[1])

    def test_a_counter_that_went_back_gives_unknown_usage(self):
        self.assertIsNone(self.calls('normal', 'reset')[1])

    def test_a_counter_that_went_back_inside_the_turn_gives_unknown_usage(self):
        self.assertEqual(self.calls('mid_reset', 'late_repeat'), [None, None])

    def test_a_resume_that_starts_below_the_previous_run_gives_unknown_usage(self):
        self.assertIsNone(self.calls('normal', 'replay_low')[1])

    def test_a_replayed_total_that_goes_back_before_the_turn_gives_unknown_usage(self):
        self.assertIsNone(self.calls('normal', 'replay_twice_lower')[1])

    def test_the_last_total_of_a_run_with_unknown_usage_still_bounds_the_next_start(self):
        self.assertEqual(self.calls('normal', 'no_replay', 'replay_low')[1:], [None, None])

    def test_an_event_of_another_thread_before_ours_is_known_is_not_counted(self):
        self.assertEqual(self.calls('foreign_before_thread')[0]['tokens']['totalTokens'], 110)

    def test_repeated_events_and_other_threads_are_not_counted(self):
        first, second = self.calls('duplicate', 'other_thread')
        self.assertEqual(first['tokens']['totalTokens'], 110)
        self.assertEqual(second['tokens']['totalTokens'], 110)

    def recover(self):
        # The worker holds the task lock until it has stopped the helper's process group.
        for _ in range(100):
            result = self.f.cmd('recover', ok=False)
            if result.returncode == 0:
                return
            self.assertIn('locked', result.stderr)
            time.sleep(0.05)
        self.fail('the task stayed locked: ' + result.stderr)

    def test_a_controller_killed_after_the_usage_event_keeps_that_usage_through_recover(self):
        with mock.patch.dict('os.environ', {'COUNCIL_FAKE_USAGE': str(self.totals), 'COUNCIL_FAKE_USAGE_MODE': 'then_hang'}):
            self.b.reverse(); self.f.check()
            p = subprocess.Popen([sys.executable, str(original.SCRIPT), 'call', '--task', str(self.f.task), '--phase', 'REVIEW',
                                  '--stage', 'implementation', '--goal', 'Review implementation'],
                                 env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(200):
                if self.totals.exists():
                    break
                time.sleep(0.05)
            else:
                p.kill(); self.fail('the helper never reported usage')
            time.sleep(0.3)
            p.kill(); p.communicate(timeout=10)
        self.recover()
        run = self.f.state()['runs'][-1]
        self.assertEqual(run['status'], 'INTERRUPTED')
        self.assertEqual(run['usage']['tokens']['totalTokens'], 110)
        self.assertFalse(run['usage']['complete'])
        summary = json.loads(self.f.cmd('status').stdout)['usage']['codex']
        self.assertEqual((summary['known_runs'], summary['partial_runs']), (0, 1))

    def test_a_controller_killed_before_any_usage_event_records_unknown_usage(self):
        with mock.patch.dict('os.environ', {'COUNCIL_FAKE_PID_FILE': str(self.f.base / 'fake.pid')}):
            self.b.reverse(); self.f.check()
            p = subprocess.Popen([sys.executable, str(original.SCRIPT), 'call', '--task', str(self.f.task), '--phase', 'REVIEW',
                                  '--stage', 'implementation', '--goal', 'Review implementation'],
                                 env=dict(os.environ, COUNCIL_FAKE_MODE='hang_ignore_term'),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(200):
                if (self.f.base / 'fake.pid').exists():
                    break
                time.sleep(0.05)
            p.kill(); p.communicate(timeout=10)
        self.recover()
        self.assertIsNone(self.f.state()['runs'][-1]['usage'])

    def test_usage_is_kept_apart_from_the_native_session_checkpoint(self):
        self.calls('normal', 'normal')
        run = self.f.state()['runs'][-1]
        checkpoint = json.loads((self.f.task / run['directory'] / 'native-session.json').read_text())
        self.assertEqual(set(checkpoint), {'id', 'task_id', 'session_id'})
        self.assertTrue((self.f.task / run['directory'] / 'usage.json').is_file())

    def test_a_delegated_repair_records_its_usage(self):
        with mock.patch.dict('os.environ', {'COUNCIL_FAKE_USAGE': str(self.totals)}):
            self.b.reverse(); self.b.worsen()
            self.f.cmd('repair', '--stage', 'implementation', '--plan', self.b.plan())
        repair = self.f.state()['interventions']['implementation']['repairs'][-1]
        self.assertEqual(repair['usage']['tokens']['totalTokens'], 110)
        self.assertEqual(json.loads(self.f.cmd('status').stdout)['usage']['codex']['known_runs'], 3)


class EstablishedSessionStartupTest(unittest.TestCase):
    """A call that provably never reached the model may be repeated as is, also in a conversation that already exists."""

    def codex(self):
        import test_architecture as architecture
        a = architecture.ArchitectureTest()
        a.setUp()
        self.addCleanup(a.doCleanups)
        node = a.f.base / 'node-wrapper'
        real = shutil.which('node')
        source = '#!' + sys.executable + '\nimport os,sys\nos.execv(' + repr(real) + ',[' + repr(real) + ',*sys.argv[1:]])\n'
        node.write_text(source); node.chmod(0o755)
        a.init(node=node); a.f.check(); a.f.call()
        return a.f, node, source

    def session(self, f):
        return f.state()['sessions']['REVIEW:implementation']

    def test_codex_spawn_failure_in_an_established_conversation_can_repeat_the_same_call(self):
        f, node, source = self.codex()
        native = self.session(f)['native_id']
        node.unlink()
        f.call(goal='Second look', ok=False)
        failed = f.state()['runs'][-1]
        self.assertIs(failed['process']['cli_started'], False)
        self.assertTrue(failed['session_not_started'])
        self.assertEqual(self.session(f)['native_id'], native)
        self.assertTrue(self.session(f)['started'])
        self.assertNotIn('unstarted_request', self.session(f))
        node.write_text(source); node.chmod(0o755)
        f.call(goal='Second look')
        state = f.state()
        self.assertEqual(state['runs'][-1]['status'], 'COMPLETE')
        self.assertEqual(state['counters']['launches'], 3)
        self.assertEqual(state['runs'][-1]['argv'][state['runs'][-1]['argv'].index('--resume') + 1], native)
        self.assertEqual(self.session(f)['native_id'], native)

    def test_codex_resume_that_fails_before_the_turn_is_sent_can_repeat_the_same_call(self):
        f, _, _ = self.codex()
        native = self.session(f)['native_id']
        f.call('resume_auth', goal='Second look', ok=False)
        failed = f.state()['runs'][-1]
        checkpoint = json.loads((f.task / failed['directory'] / 'native-session.json').read_text())
        self.assertEqual(checkpoint['status'], 'NOT_STARTED')
        self.assertEqual(checkpoint['request_id'], failed['request_id'])
        self.assertTrue(failed['session_not_started'])
        self.assertEqual(self.session(f)['native_id'], native)
        f.call(goal='Second look')
        state = f.state()
        self.assertEqual(state['runs'][-1]['status'], 'COMPLETE')
        self.assertEqual(state['runs'][-1]['argv'][state['runs'][-1]['argv'].index('--resume') + 1], native)
        self.assertEqual(self.session(f)['native_id'], native)

    def test_changed_pre_turn_checkpoint_of_an_established_conversation_blocks_the_repeat(self):
        f, _, _ = self.codex()
        f.call('resume_auth', goal='Second look', ok=False)
        failed = f.state()['runs'][-1]
        (f.task / failed['directory'] / 'native-session.json').write_text('{}')
        result = f.call(goal='Second look', ok=False)
        self.assertIn('checkpoint missing or changed', result.stderr)
        self.assertEqual(f.state()['counters']['launches'], 2)

    def test_codex_failure_after_the_turn_was_sent_still_needs_a_distinguishing_call(self):
        f, _, _ = self.codex()
        f.call('turn_network', goal='Second look', ok=False)
        self.assertFalse(f.state()['runs'][-1].get('session_not_started', False))
        result = f.call(goal='Second look', ok=False)
        self.assertIn('Identical request', result.stderr)
        self.assertEqual(f.state()['counters']['launches'], 2)

    def test_codex_spawn_failure_saved_by_an_earlier_release_can_repeat_the_same_call(self):
        # 1.5.0 and earlier kept the proof (cli_started false) but did not mark the call when the conversation existed.
        f, node, source = self.codex()
        native = self.session(f)['native_id']
        node.unlink()
        f.call(goal='Second look', ok=False)
        state = f.state()
        state['runs'][-1].pop('session_not_started')
        (f.task / 'state.json').write_text(json.dumps(state))
        node.write_text(source); node.chmod(0o755)
        f.call(goal='Second look')
        self.assertEqual(f.state()['runs'][-1]['status'], 'COMPLETE')
        self.assertEqual(self.session(f)['native_id'], native)

    def test_recover_keeps_the_pre_turn_proof_of_an_established_codex_conversation(self):
        f, node, source = self.codex()
        native = self.session(f)['native_id']
        real = shutil.which('node')
        node.write_text('#!' + sys.executable + '\nimport subprocess,sys,time\nsubprocess.run([' + repr(real) +
                        ',*sys.argv[1:]])\ntime.sleep(300)\n'); node.chmod(0o755)
        p = subprocess.Popen([sys.executable, '-B', str(original.SCRIPT), 'call', '--task', str(f.task), '--phase', 'REVIEW',
                              '--stage', 'implementation', '--goal', 'Second look'],
                             env=dict(os.environ, COUNCIL_FAKE_MODE='resume_auth'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            first = f.state()['runs'][0]['directory']
            for _ in range(150):
                if [c for c in (f.task / 'reviews').glob('*/native-session.json') if c.parent.name != Path(first).name]:
                    break
                time.sleep(.02)
            else:
                self.fail('No pre-turn checkpoint')
            p.kill(); p.communicate(timeout=5)
            for _ in range(100):
                result = f.cmd('recover', ok=False)
                if result.returncode == 0:
                    break
                self.assertIn('locked', result.stderr); time.sleep(.03)
            self.assertEqual(result.returncode, 0, result.stderr)
            interrupted = f.state()['runs'][-1]
            self.assertEqual(interrupted['status'], 'INTERRUPTED')
            self.assertTrue(interrupted['session_not_started'])
            self.assertEqual(self.session(f)['native_id'], native)
            node.write_text(source); node.chmod(0o755)
            f.call(goal='Second look')
            state = f.state()
            self.assertEqual(state['runs'][-1]['status'], 'COMPLETE')
            self.assertEqual(state['runs'][-1]['argv'][state['runs'][-1]['argv'].index('--resume') + 1], native)
            self.assertEqual(state['counters']['launches'], 3)
        finally:
            if p.poll() is None:
                p.kill(); p.communicate(timeout=5)

    def test_delegated_repair_that_never_started_in_an_established_conversation_can_repeat_the_same_plan(self):
        import test_architecture as architecture
        a = architecture.ArchitectureTest()
        a.setUp()
        self.addCleanup(a.doCleanups)
        f = a.f
        node = f.base / 'node-wrapper'
        real = shutil.which('node')
        source = '#!' + sys.executable + '\nimport os,sys\nos.execv(' + repr(real) + ',[' + repr(real) + ',*sys.argv[1:]])\n'
        node.write_text(source); node.chmod(0o755)
        a.init(node=node, repair_calls=4)
        a.b.worsen()
        f.cmd('repair', '--stage', 'implementation', '--plan', a.b.plan()); f.check()
        f.call('growth3', goal='Inspect the delegated repair', ok=False)
        self.assertEqual(f.state()['interventions']['implementation']['status'], 'FAILED')
        native = f.state()['sessions']['REPAIR:implementation']['native_id']
        decision = f.base / 'decision.txt'
        decision.write_text('Continue with one more repair of the same boundary.\n')
        node.unlink()
        f.cmd('repair', '--stage', 'implementation', '--plan', a.b.plan(), '--user-decision', str(decision), ok=False)
        failed = f.state()['interventions']['implementation']['repairs'][-1]
        self.assertIs(failed['process']['cli_started'], False)
        self.assertEqual(f.state()['sessions']['REPAIR:implementation']['native_id'], native)
        launches = f.state()['counters']['launches']
        node.write_text(source); node.chmod(0o755)
        # The earlier repair already changed the file; the repeated one disputes instead of editing it again.
        f.cmd('repair', '--stage', 'implementation', '--plan', a.b.plan(), '--user-decision', str(decision), mode='dispute_repair')
        state = f.state()
        repeated = state['interventions']['implementation']['repairs'][-1]
        self.assertEqual(repeated['argv'][repeated['argv'].index('--resume') + 1], native)
        self.assertEqual(state['counters']['launches'], launches + 1)

    def claude(self):
        import test_release_132 as release132
        r = release132.Release132Test()
        r.setUp()
        self.addCleanup(r.doCleanups)
        native = r.startup_transport()
        r.f.check(); r.f.call()
        return r.f, native

    def test_claude_sign_in_failure_with_zero_usage_in_an_established_conversation_can_repeat_the_same_call(self):
        f, _ = self.claude()
        sid = self.session(f)['id']
        f.call('auth_before_start', goal='Second look', ok=False)
        failed = f.state()['runs'][-1]
        self.assertIn('--resume', failed['argv'])
        self.assertEqual(failed['startup_proof']['kind'], 'ZERO_USAGE_AUTH')
        self.assertTrue(failed['session_not_started'])
        self.assertTrue(self.session(f)['started'])
        self.assertNotIn('unstarted_request', self.session(f))
        f.call(goal='Second look')
        state = f.state()
        self.assertEqual(state['runs'][-1]['status'], 'COMPLETE')
        self.assertEqual(state['runs'][-1]['argv'][state['runs'][-1]['argv'].index('--resume') + 1], sid)
        self.assertEqual(state['counters']['launches'], 3)

    def test_claude_lost_conversation_is_not_treated_as_a_call_that_can_be_repeated(self):
        f, native = self.claude()
        native.unlink()
        f.call(goal='Second look', ok=False)
        self.assertNotIn('startup_proof', f.state()['runs'][-1])
        self.assertTrue(self.session(f)['started'])
        result = f.call(goal='Second look', ok=False)
        self.assertIn('Identical request', result.stderr)


    def test_claude_spawn_failure_after_a_collision_keeps_resuming_the_existing_conversation(self):
        import test_release_132 as release132
        r = release132.Release132Test()
        r.setUp()
        self.addCleanup(r.doCleanups)
        f = r.f
        r.new_task(policy=dict(design_calls=2, launches=8, per_call_timeout=3, review_calls=6, task_seconds=120, transport_retries=1))
        native = r.startup_transport(); f.check()
        f.call('auth_before_start', ok=False)
        native.write_text(json.dumps({'session_id': self.session(f)['id']}))
        f.call(ok=False)
        self.assertEqual(f.state()['runs'][-1]['startup_proof']['kind'], 'SESSION_IN_USE')
        self.assertTrue(self.session(f)['started'])
        state = f.state(); wrapper = state['claude']
        state['claude'] = str(f.base / 'missing-claude')
        (f.task / 'state.json').write_text(json.dumps(state))
        f.call(ok=False)
        self.assertIs(f.state()['runs'][-1]['process']['cli_started'], False)
        self.assertTrue(self.session(f)['started'])
        state = f.state(); state['claude'] = wrapper
        (f.task / 'state.json').write_text(json.dumps(state))
        f.call()
        state = f.state()
        self.assertIn('--resume', state['runs'][-1]['argv'])
        self.assertEqual([r['status'] for r in state['runs']][-1], 'COMPLETE')
        self.assertEqual(state['counters']['launches'], 4)

    def test_claude_refusal_that_used_tokens_in_an_established_conversation_still_blocks_the_repeat(self):
        f, _ = self.claude()
        wrapper = Path(f.state()['claude'])
        wrapper.write_text(wrapper.read_text().replace("'modelUsage':{}", "'modelUsage':{'m':{'inputTokens':5}}"))
        f.call('auth_before_start', goal='Second look', ok=False)
        self.assertNotIn('startup_proof', f.state()['runs'][-1])
        result = f.call(goal='Second look', ok=False)
        self.assertIn('Identical request', result.stderr)

    def test_only_a_proof_about_the_conversation_decides_whether_it_exists(self):
        sys.path.insert(0, str(original.SCRIPT.parent))
        self.addCleanup(sys.path.remove, str(original.SCRIPT.parent))
        exists = module('council').claude_conversation_exists
        def attempt(kind, flag):
            return {'argv': ['claude', '-p', flag, 'id'], **({'startup_proof': {'kind': kind}} if kind else {})}
        auth_new = attempt('ZERO_USAGE_AUTH', '--session-id')
        collision = attempt('SESSION_IN_USE', '--session-id')
        spawn = attempt(None, '--resume')
        # A history an earlier release could save: after the collision it tried the new UUID again.
        self.assertTrue(exists([auth_new, collision, spawn, auth_new]))
        self.assertTrue(exists([auth_new, attempt('ZERO_USAGE_AUTH', '--resume')]))
        self.assertFalse(exists([auth_new, collision, attempt('SESSION_ABSENT', '--resume'), auth_new]))
        self.assertFalse(exists([auth_new, spawn]))
        self.assertFalse(exists([]))

if __name__ == '__main__':
    unittest.main()
