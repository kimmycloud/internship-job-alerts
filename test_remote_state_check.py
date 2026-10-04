"""Exercise the exact blob comparison called by the GitHub workflow."""

from pathlib import Path
import subprocess
import tempfile
import unittest


CHECK = Path(__file__).resolve().parent / 'scripts/check_remote_alert_state.sh'


class RemoteStateCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git('init', '-q')
        (self.repo / 'README').write_text('initial\n')
        self.git('add', 'README')
        self.commit()
        self.missing = self.git('rev-parse', 'HEAD').stdout.strip()
        state = self.repo / 'internship_alert_state.json'
        state.write_text('{"version":"A"}\n')
        self.git('add', state.name)
        self.commit()
        self.state_a = self.git('rev-parse', 'HEAD').stdout.strip()
        state.write_text('{"version":"B"}\n')
        self.git('add', state.name)
        self.commit()
        self.state_b = self.git('rev-parse', 'HEAD').stdout.strip()
        state.unlink()
        self.git('add', '-u')
        self.commit()
        self.deleted = self.git('rev-parse', 'HEAD').stdout.strip()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, capture_output=True,
                              text=True, check=True)

    def commit(self):
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-qm', 'fixture')

    def compare(self, base, remote):
        return subprocess.run(['bash', str(CHECK), base, remote], cwd=self.repo,
                              capture_output=True, text=True)

    def test_first_initialization_both_missing(self):
        self.assertEqual(self.compare(self.missing, self.deleted).returncode, 0)

    def test_plain_rev_parse_emits_unresolved_path(self):
        spec = f'{self.missing}:internship_alert_state.json'
        result = subprocess.run(['git', 'rev-parse', spec], cwd=self.repo,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), spec)

    def test_remote_state_appears(self):
        self.assertEqual(self.compare(self.missing, self.state_a).returncode, 1)

    def test_same_state_blob(self):
        self.assertEqual(self.compare(self.state_a, self.state_a).returncode, 0)

    def test_remote_state_changes(self):
        self.assertEqual(self.compare(self.state_a, self.state_b).returncode, 1)

    def test_remote_state_deleted(self):
        self.assertEqual(self.compare(self.state_a, self.deleted).returncode, 1)

    def test_first_baseline_commit_rebases_without_push(self):
        self.git('checkout', '-q', '-B', 'baseline', self.missing)
        self.git('update-ref', 'refs/remotes/origin/main', self.missing)
        state = self.repo / 'internship_alert_state.json'
        state.write_text('{"schema_version":2}\n')
        self.git('add', state.name)
        self.commit()
        self.assertEqual(self.compare('HEAD^', 'origin/main').returncode, 0)
        self.git('rebase', 'origin/main')
        self.assertEqual(state.read_text(), '{"schema_version":2}\n')


if __name__ == '__main__':
    unittest.main()
