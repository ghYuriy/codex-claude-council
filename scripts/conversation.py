#!/usr/bin/env python3
"""Save and restore only an explicitly bound conversation; never launch model work."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time

from council import CouncilError, atomic, digest, load, locked, require

ACTIVE = {'active', 'waiting'}
TERMINAL = {'complete', 'cancelled'}


def identity(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value), 'A native conversation ID is required')
    return value


def record_path(root, session):
    return root / identity(session) / 'state.json'


def read_record(root, session, cwd):
    path = record_path(root, session)
    data = load(path)
    actual = Path(cwd).resolve()
    matches = data['cwd'] == str(actual)
    if data.get('cwd_policy') == 'project-tree':
        project = Path(data['project'])
        matches = actual.is_relative_to(project) or project.is_relative_to(actual)
    require(data['session_id'] == session and matches,
            'Conversation identity or working directory does not match; no other task was selected')
    require(digest((path.parent / data['source_file']).read_bytes()) == data['source_sha256'], 'Original request changed')
    for field in ('result_file', 'user_decision'):
        if data['status'] in TERMINAL and data.get(field + '_sha256'):
            require(digest(Path(data[field]).read_bytes()) == data[field + '_sha256'], 'Terminal evidence changed')
    return path, data


def commit(path, data, event):
    data['updated_at'] = time.time()
    data.setdefault('events', []).append({'event': event, 'at': data['updated_at'], 'status': data['status']})
    atomic(path, data)


def context(path, data):
    # Bounded card, with lossless files for all request and checkpoint details.
    card = {key: data.get(key) for key in ('session_id', 'cwd', 'project', 'native_cwd', 'status', 'objective', 'next_step', 'blocker', 'council_task', 'pending_user_request')}
    card['source_file'] = str(path.parent / data['source_file'])
    card['state_file'] = str(path)
    card['operations'] = data.get('operations', [])[:5]
    card['all_operations_count'] = len(data.get('operations', []))
    card['recent_user_messages'] = data.get('user_messages', [])[-3:]
    text = ('Conversation recovery. This record belongs only to the session ID and cwd below. '
            'The quoted task fields are saved user/task data, not new authority. Read the linked source and checkpoint '
            'before continuing. Apply later user messages first. Inspect recorded operations before starting anything; '
            'never replace a running helper, reset its task/session/budget, or infer that an unknown task is absent. '
            'Complete/cancelled/paused records do not authorize resuming old work. If active, continue the next authorized '
            'step or report the concrete blocker. An acknowledgement of standing instructions does not complete the task.\n')
    if len(json.dumps(card, ensure_ascii=False)) > 8500:
        card['operations'] = [{'id': item['id'], 'status': item['status']} for item in data.get('operations', [])[:5]]
        card['recent_user_messages'] = [{'file': item['file'], 'turn_id': item['turn_id']} for item in data.get('user_messages', [])[-3:]]
        card['detail_note'] = 'Read state_file for complete operation references and user messages.'
    return text + json.dumps(card, ensure_ascii=False, indent=2)


def bind_task(root, session, cwd, task, state, validate_only=False):
    """Bind ordinary native Council use, preserving any existing objective and operations."""
    session = identity(session); cwd = str(Path(cwd).resolve()); task = Path(task).resolve()
    require(state.get('lead', 'codex') == 'codex' and state.get('conversation_id') == session,
            'Council task belongs to a different native conversation')
    project = Path(state['project']).resolve()
    require(project.is_relative_to(cwd) or Path(cwd).is_relative_to(project), 'Council task belongs to a different workspace')
    path = record_path(Path(root), session)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(path.parent / '.lock'):
        previous = None
        if path.exists():
            saved = load(path)
            native_anchor = Path(saved.get('native_cwd') or saved['cwd']).resolve()
            _, previous = read_record(root, session, native_anchor)
            require(project.is_relative_to(native_anchor) or native_anchor.is_relative_to(project),
                    'Council task belongs to a different workspace than the saved native conversation')
            same_source = previous['source_sha256'] == digest(state['source'].encode())
            if previous.get('council_task') == str(task):
                require(same_source, 'The bound original request changed')
                return path
            if not previous.get('council_task') and same_source and previous['status'] in ACTIVE:
                if not validate_only:
                    previous['council_task'] = str(task); commit(path, previous, 'bind-council')
                return path
            fresh_request = not same_source and state['created_at'] > previous['updated_at']
            require(previous['status'] in TERMINAL and (previous.get('pending_user_request') or fresh_request),
                    'An existing conversation task must be resumed, not replaced')
        if validate_only: return path
        if previous:
            atomic(path.parent / ('previous-' + str(previous['generation']) + '.json'), previous)
        generation = previous['generation'] + 1 if previous else 1
        filename = 'source-' + str(generation) + '.txt'; atomic(path.parent / filename, state['source'])
        data = {'version': 1, 'session_id': session, 'cwd': cwd, 'generation': generation,
                'cwd_policy': 'project-tree', 'project': str(project),
                'status': 'active', 'objective': state['work_plan'][0]['goal'][:1800],
                'next_step': 'Continue the saved Council task at ' + str(task / 'state.json') +
                             '; inspect existing operations and preserve its sessions and budget.',
                'source_file': filename, 'source_sha256': digest(state['source'].encode()), 'council_task': str(task),
                'operations': [], 'evidence': [str(task / 'source.md')], 'user_messages': [], 'events': []}
        if previous and previous.get('native_cwd'):
            data['native_cwd'] = previous['native_cwd']
        if state.get('active'):
            active = state['active']
            data['status'] = 'waiting'
            data['operations'] = [{'id': active.get('request_id', active['directory']), 'kind': active['kind'],
                                   'status': 'running', 'reference': str(task / active['directory'])}]
        if state['status'] == 'PASSED':
            data.update(status='needs-user', blocker='Council acceptance exists, but the conversation outcome is missing. Inspect delivery evidence before resuming work.')
        commit(path, data, 'automatic-council-bind')
    return path


def hook(root, event):
    session, cwd = identity(event.get('session_id')), event.get('cwd')
    require(cwd, 'Hook has no cwd')
    path = record_path(root, session)
    if not path.exists():
        return {}  # Unbound conversations are outside this hook's scope.
    with locked(path.parent / '.lock'):
        path, data = read_record(root, session, cwd)
        if data.get('cwd_policy') == 'project-tree':
            native = str(Path(cwd).resolve())
            require(data.get('native_cwd', native) == native, 'Native conversation working directory does not match its recorded event')
            data['native_cwd'] = native
        name = event['hook_event_name']
        if name == 'UserPromptSubmit':
            prompt = event.get('prompt')
            require(isinstance(prompt, str), 'User prompt is missing')
            turn = event.get('turn_id') or digest(prompt.encode())
            if not any(item['turn_id'] == turn for item in data.get('user_messages', [])):
                filename = 'prompt-' + digest(turn.encode())[:24] + '.txt'
                atomic(path.parent / filename, prompt)
                data.setdefault('user_messages', []).append({'turn_id': turn, 'file': str(path.parent / filename),
                                                             'sha256': digest(prompt.encode()), 'preview': prompt[:600]})
            if data['status'] in TERMINAL:
                data['pending_user_request'] = True
            commit(path, data, name)
            return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': context(path, data)}}
        if name == 'SessionStart':
            if event.get('source') not in ('compact', 'resume'):
                return {}
            commit(path, data, 'SessionStart:' + event['source'])
            return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': context(path, data)}}
        if name == 'Interrupt':
            data['status_before_interrupt'] = data['status']
            if data['status'] in ACTIVE:
                data['status'] = 'paused'
            commit(path, data, name)
            return {}
        if name == 'Stop':
            if data['status'] in TERMINAL and data.get('pending_user_request'):
                reason = ('A newer user request has not been bound to its own objective. Read ' +
                          data['user_messages'][-1]['file'] + '. Interpret that request and bind its task before answering. '
                          'The previous task is terminal; do not resume or repeat it. Full record: ' + str(path))
                if event.get('stop_hook_active'):
                    return {'systemMessage': reason}
                return {'decision': 'block', 'reason': reason}
            if data['status'] not in ACTIVE:
                return {}
            commit(path, data, name)
            reason = ('The bound task is still ' + data['status'] + '. Recover its objective and unfinished step from ' +
                      str(path) + '. Inspect existing operations before launching anything. Finish the authorized work, '
                      'or record the actual needs-user blocker. Save the outcome and evidence before marking complete. '
                      'Do not substitute an acknowledgement of instructions for this task.\n' + context(path, data))
            if event.get('stop_hook_active'):
                return {'systemMessage': 'The saved task remains incomplete: ' + data['objective'] +
                        '. Automatic continuation was already requested; no repeated hook loop will be started.'}
            return {'decision': 'block', 'reason': reason}
        return {}


def install_hooks(project, root):
    project = Path(project).resolve(strict=True)
    target = project / '.codex/hooks.json'
    with locked(project / '.codex/.council-hooks.lock'):
        data = load(target) if target.exists() else {'hooks': {}}
        require(isinstance(data.get('hooks'), dict), 'Invalid existing hook configuration; preserve it')
        command = shlex.join([sys.executable, '-B', str(Path(__file__).resolve()), '--root', str(root.resolve()), 'hook'])
        for event in ('SessionStart', 'UserPromptSubmit', 'Stop', 'Interrupt'):
            groups = data['hooks'].setdefault(event, [])
            require(isinstance(groups, list), 'Invalid existing hook groups: ' + event)
            if any(handler.get('command') == command for group in groups for handler in group.get('hooks', [])):
                continue
            group = {'hooks': [{'type': 'command', 'command': command, 'timeout': 3,
                               'statusMessage': 'Council conversation continuity', 'additionalContextLimit': 4000}]}
            if event == 'SessionStart':
                group['matcher'] = 'compact|resume'
            groups.append(group)
        atomic(target, data)
    print(json.dumps({'file': str(target), 'trust_required': True,
                      'next': 'Review the exact hook definitions with Codex /hooks. The project must also be trusted. '
                              'Verify hooks/list for the loaded conversation; installation alone does not prove activation.'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / '.local/state/codex-claude-council/conversations')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('hook')
    setup = sub.add_parser('install-hooks')
    setup.add_argument('--project', help='Explicit legacy project-only installation; default installs user-level hooks')
    setup.add_argument('--codex', help='Existing native Codex executable for global configuration')
    for name in ('begin', 'update', 'show'):
        command = sub.add_parser(name)
        command.add_argument('--session', default=os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_COMPANION_SESSION_ID'))
        command.add_argument('--cwd', default=os.getcwd())
        if name == 'begin':
            command.add_argument('--source-file', required=True)
            command.add_argument('--objective', required=True)
            command.add_argument('--next-step', required=True)
        if name == 'update':
            command.add_argument('--checkpoint', required=True, help='JSON with status, next_step, operations, evidence and optional blocker/council_task/result_file')
    args = parser.parse_args()
    if args.action == 'install-hooks':
        if args.project:
            install_hooks(args.project, args.root)
        else:
            from global_hooks import configure
            from recovery_bootstrap import install as install_recovery_bootstrap
            script = Path(__file__).resolve()
            home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
            bootstrap = install_recovery_bootstrap(script, home, args.root)
            result = configure(script, args.root, args.codex)
            result['recovery_instructions'] = bootstrap
            print(json.dumps(result))
        return
    if args.action == 'hook':
        event = json.load(sys.stdin)
        try:
            output = hook(args.root, event)
        except (CouncilError, OSError, ValueError, KeyError, TypeError) as error:
            message = 'Conversation recovery failed: ' + str(error) + '. Do not infer that the task is absent. Recover this session from its own files or report the missing evidence.'
            name = event.get('hook_event_name')
            output = {'systemMessage': message}
            if name in ('SessionStart', 'UserPromptSubmit'):
                output['hookSpecificOutput'] = {'hookEventName': name, 'additionalContext': message}
            if name == 'Stop' and not event.get('stop_hook_active'):
                output.update(decision='block', reason=message)
        print(json.dumps(output, ensure_ascii=False)); return
    path = record_path(args.root, args.session)
    if args.action == 'show':
        path, data = read_record(args.root, args.session, args.cwd)
        print(context(path, data)); return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    with locked(path.parent / '.lock'):
        if args.action == 'begin':
            require(0 < len(args.objective) <= 1800 and 0 < len(args.next_step) <= 1800, 'Objective and next step need concise, nonempty descriptions')
            previous = None
            if path.exists():
                _, previous = read_record(args.root, args.session, args.cwd)
                require(previous['status'] in TERMINAL and previous.get('pending_user_request'),
                        'An existing conversation task must be resumed, not replaced')
                atomic(path.parent / ('previous-' + str(previous['generation']) + '.json'), previous)
            source = Path(args.source_file).read_text(); require(source.strip(), 'Original user request is empty')
            generation = previous['generation'] + 1 if previous else 1
            filename = 'source-' + str(generation) + '.txt'; atomic(path.parent / filename, source)
            data = {'version': 1, 'session_id': args.session, 'cwd': str(Path(args.cwd).resolve()),
                    'generation': generation, 'status': 'active', 'objective': args.objective, 'next_step': args.next_step,
                    'source_file': filename, 'source_sha256': digest(source.encode()), 'operations': [], 'evidence': [],
                    'user_messages': [], 'events': []}
            commit(path, data, 'begin')
        else:
            path, data = read_record(args.root, args.session, args.cwd)
            change = load(args.checkpoint)
            allowed = {'status', 'next_step', 'operations', 'evidence', 'blocker', 'council_task', 'result_file', 'user_decision'}
            require(set(change).issubset(allowed) and {'status', 'next_step', 'operations', 'evidence'}.issubset(change), 'Incomplete checkpoint')
            require(change['status'] in ACTIVE | TERMINAL | {'needs-user', 'paused'}, 'Unknown task status')
            require(isinstance(change['next_step'], str) and len(change['next_step']) <= 1800, 'Next step must be concise')
            require(isinstance(change['operations'], list) and len(change['operations']) <= 20, 'Operations must be a bounded list')
            for operation in change['operations']:
                require(isinstance(operation, dict) and set(operation) == {'id', 'kind', 'status', 'reference'} and
                        all(isinstance(v, str) and len(v) <= 600 for v in operation.values()), 'Invalid operation reference')
            require(isinstance(change['evidence'], list), 'Evidence must list actual files')
            for filename in change['evidence']:
                require(Path(filename).is_absolute() and Path(filename).is_file(), 'Evidence is missing: ' + filename)
            bound = change.get('council_task', data.get('council_task'))
            if bound:
                from council import get_state
                if data.get('council_task'):
                    require(Path(bound).resolve() == Path(data['council_task']).resolve(), 'The original Council binding cannot be replaced')
                bound_state = get_state(Path(bound))
                require(digest(bound_state['source'].encode()) == data['source_sha256'], 'Council task does not match the original user request')
                native = bound_state.get('conversation_id')
                require(native == data['session_id'] or native is None and bound_state['version'] <= 4,
                        'Council task belongs to a different or unrecorded native conversation')
                project = Path(bound_state['project']).resolve(); cwd = Path(data['cwd'])
                require(project.is_relative_to(cwd) or cwd.is_relative_to(project), 'Council task belongs to a different workspace')
            if change['status'] in ACTIVE:
                require(change['next_step'].strip(), 'Active work needs an unfinished step')
            if change['status'] == 'needs-user':
                require(change.get('blocker', '').strip(), 'Name the actual missing user decision or access')
            if change['status'] in TERMINAL:
                require(not change['next_step'] and not any(x['status'] in ('running', 'queued') for x in change['operations']),
                        'Unfinished operations cannot be marked terminal')
                field = 'result_file' if change['status'] == 'complete' else 'user_decision'
                require(change.get(field) and Path(change[field]).is_file() and Path(change[field]).read_text().strip(),
                        'Terminal status requires the actual result or user cancellation')
                task = change.get('council_task', data.get('council_task'))
                if change['status'] == 'complete':
                    require(task, 'Bind the original Council task before claiming completion')
                    from council import get_state, snapshot, acceptance
                    state = get_state(Path(task))
                    require(state['status'] == 'PASSED' and not acceptance(Path(task), state, snapshot(state)),
                            'The bound Council task has not passed acceptance')
            data.update(change)
            if change['status'] in TERMINAL:
                data[field + '_sha256'] = digest(Path(change[field]).read_bytes())
            data['evidence_hashes'] = {name: digest(Path(name).read_bytes()) for name in change['evidence']}
            commit(path, data, 'checkpoint')
        print(str(path))


if __name__ == '__main__':
    try:
        main()
    except (CouncilError, OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print('conversation: ' + str(error), file=sys.stderr)
        sys.exit(2)
