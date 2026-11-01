"""Install only Council's user-level hooks through Codex's normal config API."""
import copy
import json
import os
from pathlib import Path
import queue
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

EVENTS = {'SessionStart': 'sessionStart', 'UserPromptSubmit': 'userPromptSubmit',
          'Stop': 'stop', 'Interrupt': 'interrupt'}
ALIASES = {'SessionStart': 'session_start', 'UserPromptSubmit': 'user_prompt_submit',
           'Stop': 'stop', 'Interrupt': 'interrupt'}
LABEL = 'Council conversation continuity'


class ConfigClient:
    def __init__(self, executable=None):
        self.executable = executable or os.environ.get('CODEX_APP_RUNTIME_CLI_PATH') or shutil.which('codex')
        if not self.executable:
            raise RuntimeError('Codex is unavailable; preserve the existing installation and resolve its executable.')

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='council-config-')
        self.process = subprocess.Popen([self.executable, '--no-daemon', 'app-server', '--stdio'],
            cwd=self.directory.name, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.messages = queue.Queue(); self.sequence = 0
        def read():
            for line in self.process.stdout:
                try: self.messages.put(json.loads(line))
                except ValueError: continue
            self.messages.put(None)
        threading.Thread(target=read, daemon=True).start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'council-hook-install', 'version': '1'},
                                     'capabilities': {'experimentalApi': True}})
            self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'initialized'}) + '\n')
            self.process.stdin.flush()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def call(self, method, params):
        self.sequence += 1; ident = self.sequence
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush(); deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try: item = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty: break
            if item is None:
                raise RuntimeError('Codex config host exited before replying; inspect local host permissions. No model was called.')
            if item.get('id') == ident:
                if 'error' in item: raise RuntimeError('Codex config API: ' + str(item['error']))
                return item['result']
        raise RuntimeError('Codex config API timed out: ' + method)

    def __exit__(self, *args):
        self.process.terminate()
        try: self.process.wait(timeout=5)
        except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
        self.process.stdin.close(); self.process.stdout.close(); self.directory.cleanup()


def definitions(script, root):
    command = shlex.join([sys.executable, '-B', str(Path(script).resolve()), '--root', str(Path(root).resolve()), 'hook'])
    result = {}
    for event in EVENTS:
        handler = {'type': 'command', 'command': command, 'timeout': 3, 'statusMessage': LABEL}
        if event in ('SessionStart', 'UserPromptSubmit'): handler['additionalContextLimit'] = 4000
        group = {'hooks': [handler]}
        if event == 'SessionStart': group['matcher'] = 'compact|resume'
        result[event] = group
    return result


def user_layer(client):
    reply = client.call('config/read', {'includeLayers': True})
    if reply['config'].get('features', {}).get('hooks') is False:
        raise RuntimeError('Hooks are disabled by existing configuration or policy; do not bypass that setting.')
    layers = [x for x in reply.get('layers', []) if x['name']['type'] == 'user' and x['name'].get('profile') is None]
    if len(layers) != 1: raise RuntimeError('Cannot identify the native user configuration layer.')
    return layers[0]


def configure(script, root, executable=None, client_factory=ConfigClient):
    wanted = definitions(script, root)
    with client_factory(executable) as client:
        layer = user_layer(client); file = layer['name']['file']; before = layer['config']
        expected = copy.deepcopy(before); hooks = expected.setdefault('hooks', {}); edits = []
        for event, group in wanted.items():
            aliases = [key for key in (event, ALIASES[event]) if key in hooks]
            if len(aliases) > 1: raise RuntimeError('Ambiguous existing hook aliases: ' + event)
            key = aliases[0] if aliases else event
            groups = hooks.setdefault(key, [])
            if not isinstance(groups, list): raise RuntimeError('Invalid existing hook groups: ' + key)
            matches = []
            for index, candidate in enumerate(groups):
                for handler in candidate.get('hooks', []):
                    if handler.get('statusMessage') != LABEL: continue
                    try: args = shlex.split(handler.get('command', ''))
                    except ValueError: continue
                    if str(Path(script).resolve()) in args: matches.append(index)
            if len(matches) > 1: raise RuntimeError('Duplicate Council hooks need reconciliation before installation.')
            if matches:
                index = matches[0]
                if len(groups[index].get('hooks', [])) != 1:
                    raise RuntimeError('Council shares a matcher with another handler; preserve it for explicit migration.')
                previous = shlex.split(groups[index]['hooks'][0]['command'])
                if previous[1:] != shlex.split(group['hooks'][0]['command'])[1:]:
                    raise RuntimeError('Existing Council hook uses different record arguments; preserve its records.')
                groups[index] = group
            else: groups.append(group)
            if groups != before.get('hooks', {}).get(key):
                edits.append({'keyPath': 'hooks.' + key, 'value': groups, 'mergeStrategy': 'replace'})
        if edits:
            client.call('config/batchWrite', {'edits': edits, 'filePath': file,
                'expectedVersion': layer['version'], 'reloadUserConfig': True})
        layer = user_layer(client)
        if layer['config'] != expected: raise RuntimeError('User configuration changed concurrently; inspect before continuing.')
        listed = client.call('hooks/list', {})
        command = wanted['Stop']['hooks'][0]['command']
        own = [h for row in listed['data'] for h in row['hooks']
               if h.get('sourcePath') == file and h.get('command') == command]
        if len(own) != 4 or {h['eventName'] for h in own} != set(EVENTS.values()):
            raise RuntimeError('The native host did not load all four user-level Council hooks.')
        edits = []
        for h in own:
            if not h['enabled'] or h.get('isManaged'):
                raise RuntimeError('A Council hook is disabled or policy-managed; do not override that control.')
            state = expected['hooks'].setdefault('state', {}).setdefault(h['key'], {})
            if state.get('trusted_hash') != h['currentHash']:
                state['trusted_hash'] = h['currentHash']
                edits.append({'keyPath': 'hooks.state.' + json.dumps(h['key']) + '.trusted_hash',
                              'value': h['currentHash'], 'mergeStrategy': 'replace'})
        if edits:
            client.call('config/batchWrite', {'edits': edits, 'filePath': file,
                'expectedVersion': layer['version'], 'reloadUserConfig': True})
        if user_layer(client)['config'] != expected:
            raise RuntimeError('Unrelated user configuration changed during installation; inspect the concurrent change.')
        loaded = client.call('hooks/list', {})
        verified = [h for row in loaded['data'] for h in row['hooks'] if h['key'] in {x['key'] for x in own}]
        if len(verified) != 4 or not all(h['enabled'] and h['trustStatus'] == 'trusted' for h in verified):
            raise RuntimeError('Global Council hook trust was not confirmed by the native host.')
        return {'scope': 'user', 'config_file': file, 'record_root': str(Path(root).resolve()),
                'verification_scope': 'fresh-config-host', 'current_thread_verified': False,
                'hooks': [{k: h[k] for k in ('key', 'eventName', 'currentHash', 'trustStatus', 'enabled')} for h in verified],
                'project_configuration_required': False, 'real_model_calls': 0}
