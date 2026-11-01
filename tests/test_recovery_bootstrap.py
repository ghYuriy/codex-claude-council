from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import recovery_bootstrap as bootstrap


class RecoveryBootstrapTest(unittest.TestCase):
    def test_preserves_user_bytes_and_mode_and_repeated_install_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); target = home / 'AGENTS.md'
            original = 'Мои правила\r\nБез завершающей новой строки'.encode()
            target.write_bytes(original); target.chmod(0o640)
            first = bootstrap.install(home / 'skills/council/scripts/conversation.py', home)
            installed = target.read_bytes()
            self.assertTrue(installed.startswith(original))
            self.assertEqual(Path(first['backup']).read_bytes(), original)
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            again = bootstrap.install(home / 'skills/council/scripts/conversation.py', home)
            self.assertFalse(again['changed']); self.assertEqual(target.read_bytes(), installed)
            self.assertEqual(len(list((home / 'skill-backups').rglob('AGENTS.md'))), 1)

    def test_updates_only_owned_block_in_active_override(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); (home / 'AGENTS.md').write_text('Inactive rules')
            target = home / 'AGENTS.override.md'; target.write_text('Override rules')
            bootstrap.install(home / 'old path/conversation.py', home)
            with target.open('a') as stream: stream.write('\nUser addition\n')
            bootstrap.install(home / 'new path/conversation.py', home)
            text = target.read_text()
            self.assertTrue(text.startswith('Override rules')); self.assertTrue(text.endswith('\nUser addition\n'))
            self.assertNotIn('old path', text); self.assertIn('new path', text)
            self.assertEqual((home / 'AGENTS.md').read_text(), 'Inactive rules')

    def test_malformed_markers_and_symlinks_preserve_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); target = home / 'AGENTS.md'
            for text in [bootstrap.START, bootstrap.END + bootstrap.START,
                         bootstrap.START + bootstrap.END + bootstrap.START + bootstrap.END]:
                target.write_text(text)
                with self.assertRaises(ValueError): bootstrap.install(home / 'script.py', home)
                self.assertEqual(target.read_text(), text)
            target.unlink(); other = home / 'owned.md'; other.write_text('Preserve')
            target.symlink_to(other)
            with self.assertRaises(ValueError): bootstrap.install(home / 'script.py', home)
            self.assertEqual(other.read_text(), 'Preserve')


if __name__ == '__main__':
    unittest.main()
