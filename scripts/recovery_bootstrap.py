"""Install a bounded recovery entry point in the active global instruction file."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shlex
import tempfile

from council import locked

START = '<!-- codex-claude-council:recovery:start -->'
END = '<!-- codex-claude-council:recovery:end -->'


def block(script, records):
    records = Path(records).resolve()
    command = shlex.join(['python3', '-B', str(Path(script).resolve()), '--root', str(records), 'show'])
    return (START + '\n## Restore an existing Council conversation\n\n'
        'After a context transition, if the current assignment is missing, first check the saved record for '
        'the exact `CODEX_THREAD_ID` at `' + str(records) + '/$CODEX_THREAD_ID/state.json`. '
        'An existing record activates this recovery procedure; a missing record does not activate Council.\n\n'
        'For an existing record, run the read-only command below in the current working directory, then read '
        'its linked source and checkpoint. It never starts a helper.\n\n```sh\n' + command + '\n```\n\n'
        'Read the latest actual user request in this same chat using the available `read_thread` tool with '
        '`threadId` equal to `CODEX_THREAD_ID`. Use it also if another history method fails. A completed record '
        'can describe an earlier request; compare the latest user message before choosing the next action. '
        'Read the current directory\'s `AGENTS.md` for a saved continuation pointer. Continue the unfinished '
        'authorized step and preserve existing helper operations, session IDs and budgets. If recovery remains '
        'unavailable, report the missing evidence; standing communication rules are not a replacement assignment.\n' + END + '\n')


def install(script, codex_home, records=None):
    home = Path(codex_home)
    home.mkdir(parents=True, exist_ok=True)
    override = home / 'AGENTS.override.md'
    target = override if override.exists() or override.is_symlink() else home / 'AGENTS.md'
    if target.is_symlink():
        raise ValueError('Preserve the symlinked global instruction file; resolve its owner before installing recovery.')
    with locked(home / '.council-recovery-instructions.lock'):
        before = target.read_bytes() if target.exists() else b''
        text = before.decode('utf-8')
        if text.count(START) != text.count(END) or text.count(START) > 1:
            raise ValueError('Malformed Council recovery block; preserve the existing instructions.')
        rendered = block(script, records or Path.home() / '.local/state/codex-claude-council/conversations')
        if START in text:
            start = text.index(START); end = text.index(END)
            if end < start:
                raise ValueError('Malformed Council recovery block; preserve the existing instructions.')
            end += len(END)
            after = text[:start] + rendered.rstrip('\n') + text[end:]
        else:
            after = text + ('\n\n' if text else '') + rendered
        encoded = after.encode('utf-8')
        if encoded == before:
            return {'file': str(target), 'changed': False, 'backup': None}
        backup = None
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
            backup = home / 'skill-backups/council-recovery-instructions' / stamp / target.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(before)
        fd, filename = tempfile.mkstemp(prefix='.council-recovery-', dir=home)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded)
            os.chmod(filename, target.stat().st_mode & 0o777 if target.exists() else 0o600)
            os.replace(filename, target)
        finally:
            Path(filename).unlink(missing_ok=True)
        return {'file': str(target), 'changed': True, 'backup': str(backup) if backup else None}
