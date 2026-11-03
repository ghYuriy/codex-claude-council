"""Selected documentation must match the code it describes: versions, commands, controller invocations and enumerations."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import council  # noqa: E402

MANIFESTS = ['.claude-plugin/plugin.json', 'reverse/.claude-plugin/plugin.json']


def fenced_blocks(path):
    blocks, current = [], None
    for line in path.read_text().splitlines():
        if line.startswith('```'):
            if current is None:
                current = []
            else:
                blocks.append('\n'.join(current)); current = None
        elif current is not None:
            current.append(line)
    return blocks


def documented_invocations():
    for path in sorted([*ROOT.glob('references/*.md'), *ROOT.glob('docs/*.md'), ROOT / 'SKILL.md']):
        for block in fenced_blocks(path):
            for command in block.replace('\\\n', ' ').splitlines():
                if command.strip().startswith('python3 "$COUNCIL"'):
                    yield path.name, command.strip()


class ConsistencyTest(unittest.TestCase):
    def version(self):
        versions = {json.loads((ROOT / m).read_text())['version'] for m in MANIFESTS}
        versions |= {p['version'] for p in json.loads((ROOT / '.claude-plugin/marketplace.json').read_text())['plugins']}
        self.assertEqual(len(versions), 1, versions)
        return versions.pop()

    def test_version_is_the_same_in_every_manifest(self):
        self.version()

    # README.md is part of the repository but not of the installed skill, so a missing docs/ still fails here.
    @unittest.skipUnless((ROOT / 'README.md').is_file(), 'Release notes ship with the repository, not with the installed skill')
    def test_version_has_release_notes(self):
        self.assertTrue((ROOT / 'docs/releases' / (self.version() + '.md')).is_file())

    @unittest.skipIf(os.environ.get('COUNCIL_INSTALLED_SELF_CHECK'), 'Already running inside an installed copy')
    def test_installed_copy_passes_its_own_consistency_check(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / 'skills' / 'codex-claude-council'
            env = dict(os.environ, HOME=base, COUNCIL_INSTALLED_SELF_CHECK='1')
            install = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/install.py'), '--target', str(target)],
                                     env=env, capture_output=True, text=True)
            self.assertEqual(install.returncode, 0, install.stderr)
            result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'test_consistency'], cwd=target / 'tests',
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_documented_plugin_commands_match_command_files(self):
        manifest = json.loads((ROOT / '.claude-plugin/plugin.json').read_text())
        files = {p.stem for p in (ROOT / manifest['commands']).glob('*.md')}
        line = next(l for l in (ROOT / 'references/claude-lead.md').read_text().splitlines() if 'plugin commands remain available' in l)
        listed = set(re.findall(r'`([a-z-]+)`', line.split(':', 1)[1].split('.', 1)[0]))
        self.assertEqual(listed, files)

    def test_documented_controller_invocations_parse(self):
        found = list(documented_invocations())
        self.assertGreater(len(found), 20)
        for name, command in found:
            argv = shlex.split(command)[2:]
            with self.subTest(document=name, command=command[:80]):
                try:
                    council.parser().parse_args(argv)
                except SystemExit:
                    self.fail('Documented command does not parse: ' + command)

    def test_documented_verdicts_match_the_report_schema(self):
        schema = set(council.report_schema(2)['properties']['verdict']['enum'])
        line = next(l for l in (ROOT / 'references/review-contract.md').read_text().splitlines() if 'verdict `CLEAR`' in l)
        self.assertEqual(set(re.findall(r'`([A-Z_]+)`', line)), schema)

    def test_documented_final_report_states_match_the_code(self):
        text = (ROOT / 'references/protocol.md').read_text()
        paragraph = next(p for p in text.split('\n\n') if 'final_report' in p)
        documented = set(re.findall(r'`(verified|not_installed|integrity_not_recorded|[a-z_]+)` \(', paragraph))
        self.assertEqual(documented, set(council.FINAL_REPORT_STATES))


if __name__ == '__main__':
    unittest.main()
