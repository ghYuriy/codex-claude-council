#!/usr/bin/env python3
"""Install this skill and global Codex recovery without changing credentials or project settings."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

NAME = 'codex-claude-council'
PARTS = ('SKILL.md', 'agents', 'references', 'scripts', 'tests', 'assets', 'examples', 'reverse', '.claude-plugin')


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def install(source, target, replace=False):
    if target.name != NAME or target.is_symlink():
        raise ValueError('Target must be a regular skill directory named ' + NAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.council-install-', dir=target.parent))
    backup = None
    try:
        for part in PARTS:
            src = source / part
            if src.is_dir():
                shutil.copytree(src, staging / part, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            else:
                shutil.copy2(src, staging / part)
        expected = hashes(staging)
        if target.exists():
            if hashes(target) == expected:
                return target, None
            if not replace:
                raise ValueError('An existing different skill was preserved. Use --replace only for an authorized update; it creates a backup.')
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
            backup = target.parent.parent / 'skill-backups' / NAME / stamp
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
        try:
            staging.rename(target)
        except BaseException:
            if backup and not target.exists():
                backup.rename(target)
            raise
        if hashes(target) != expected:
            raise RuntimeError('Installed content differs from the prepared files')
        return target, backup
    finally:
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills' / NAME
    parser.add_argument('--target', type=Path, default=default)
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--codex', help='Existing native Codex executable for global recovery setup')
    args = parser.parse_args()
    try:
        target, backup = install(Path(__file__).resolve().parents[1], args.target.expanduser().absolute(), args.replace)
        print('Installed: ' + str(target))
        if backup:
            print('Previous skill preserved: ' + str(backup))
        if target == default.expanduser().absolute():
            from recovery_bootstrap import install as install_recovery_bootstrap
            records = Path.home() / '.local/state/codex-claude-council/conversations'
            bootstrap = install_recovery_bootstrap(target / 'scripts/conversation.py', default.parent.parent, records)
            print('Global recovery entry point: ' + bootstrap['file'])
            from global_hooks import configure
            receipt = configure(target / 'scripts/conversation.py', records, args.codex)
            print('Four user-level hooks are trusted in a fresh configuration host. '
                  'This does not verify delivery in an already open chat; verify its actual next context transition.')
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, str(error) + '\n')
