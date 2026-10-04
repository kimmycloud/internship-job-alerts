import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from internship_alerts import alertable, main, process_jobs, read_state, state_lock
from internship_monitor import build_report
from job_normalizer import stable_identity


def profile(profile_id, **extra):
    return {'id': profile_id, 'education': {'year': 2, 'degree': 'Computing', 'level': 'undergraduate'},
            'role_families': ['backend', 'general_swe'], 'preferred_locations': ['toronto_gta'],
            'target_seasons': ['summer_2027'], **extra}


PROFILES = {f'profile_0{i}': profile(f'profile_0{i}') for i in range(1, 5)}
SOURCE = {'employer': 'Example', 'provider': 'greenhouse'}


def job(job_id='1', title='Backend Software Engineering Intern', location='Toronto, ON',
        description='', url=None):
    return {'source': 'greenhouse', 'employer': 'Example', 'job_id': job_id, 'title': title,
            'location': location, 'description': description,
            'url': url or f'https://example.invalid/jobs/{job_id}'}


class AlertStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.sender = Mock()

    def run_jobs(self, jobs, mode='production', profiles=None, sources=None,
                 now='2026-10-03T00:00:00+00:00'):
        return process_jobs(jobs, profiles or PROFILES, [SOURCE] if sources is None else sources,
                            self.path, mode, sender=self.sender, now=now)

    def test_initial_baseline_and_unchanged_run(self):
        self.assertEqual(self.run_jobs([job()], 'baseline')['sent'], 0)
        before = self.path.read_bytes()
        before_mtime = self.path.stat().st_mtime_ns
        self.assertEqual(self.run_jobs([job()], now='2026-10-04T00:00:00+00:00')['sent'], 0)
        self.sender.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, before_mtime)
        self.assertEqual(read_state(self.path)['jobs'], {})
        self.assertEqual(len(read_state(self.path)['seen_job_ids']), 1)

    def test_new_job_one_message_three_matches_and_reload(self):
        self.run_jobs([job()], 'baseline')
        three = dict(list(PROFILES.items())[:3])
        result = self.run_jobs([job(), job('2')], profiles=three)
        self.assertEqual(result['sent'], 1)
        self.assertEqual(self.sender.call_count, 1)
        self.assertEqual(self.sender.call_args.args[1], ['profile_01', 'profile_02', 'profile_03'])
        record = [r for r in read_state(self.path)['jobs'].values() if r['title'] == job('2')['title'] and r['status'] == 'alerted'][0]
        self.assertEqual(record['matched_profile_ids'], ['profile_01', 'profile_02', 'profile_03'])
        self.assertEqual(self.run_jobs([job(), job('2')], profiles=three)['sent'], 0)

    def test_disappear_return_tracking_description_and_match_change(self):
        self.run_jobs([], 'baseline')
        self.run_jobs([job(url='https://example.invalid/jobs/1?utm_source=a')])
        self.run_jobs([])
        changed = job(description='Completely rewritten role', url='https://example.invalid/jobs/1?utm_source=b')
        self.run_jobs([changed], profiles={'profile_01': PROFILES['profile_01']})
        self.assertEqual(self.sender.call_count, 1)
        self.assertEqual(next(iter(read_state(self.path)['jobs'].values()))['status'], 'alerted')

    def test_distinct_ids_same_title(self):
        self.run_jobs([], 'baseline')
        self.assertEqual(self.run_jobs([job('1'), job('2')])['sent'], 2)
        self.assertEqual(len(read_state(self.path)['jobs']), 2)

    def test_failed_send_stays_retryable_and_success_marks_alerted(self):
        self.run_jobs([], 'baseline')
        self.sender.side_effect = [RuntimeError('webhook secret'), None]
        self.assertEqual(self.run_jobs([job()])['failed'], 1)
        record = next(iter(read_state(self.path)['jobs'].values()))
        self.assertEqual(record['status'], 'pending')
        self.assertEqual(record['matched_profile_ids'], ['profile_01', 'profile_02', 'profile_03', 'profile_04'])
        self.assertEqual(self.run_jobs([job()])['sent'], 1)
        self.assertEqual(next(iter(read_state(self.path)['jobs'].values()))['status'], 'alerted')
        self.assertEqual(self.sender.call_count, 2)

    def test_failed_send_retries_after_disappearance(self):
        self.run_jobs([], 'baseline')
        self.sender.side_effect = [RuntimeError('temporary failure'), None]
        self.assertEqual(self.run_jobs([job()])['failed'], 1)
        self.run_jobs([])
        self.assertEqual(self.run_jobs([job()])['sent'], 1)
        self.assertEqual(self.sender.call_count, 2)

    def test_nonmatching_foreign_graduate_and_wrong_term(self):
        self.run_jobs([], 'baseline')
        jobs = [job('1', title='Finance Intern'),
                job('2', location='San Jose, CA'),
                job('3', description='Must be enrolled in a graduate degree program.'),
                job('4', title='Backend Software Engineering Intern Fall 2027')]
        self.assertEqual(self.run_jobs(jobs)['sent'], 0)
        self.sender.assert_not_called()
        self.assertEqual(self.run_jobs(jobs)['sent'], 0)

    def test_unknown_term_strong_match(self):
        public = build_report([job()], PROFILES)['matched_jobs'][0]
        self.assertEqual(public['summer_2027_relevance'], 'unknown')
        self.assertTrue(alertable(public))
        self.run_jobs([], 'baseline')
        self.assertEqual(self.run_jobs([job()])['sent'], 1)

    def test_failed_source_preserves_state_and_recovered_source_baselines(self):
        self.run_jobs([job()], 'baseline')
        before = read_state(self.path)
        self.run_jobs([], sources=[])
        self.assertEqual(read_state(self.path)['jobs'], before['jobs'])
        self.assertEqual(self.run_jobs([job('2')])['sent'], 1)

    def test_dry_run_never_sends_or_writes(self):
        self.run_jobs([job()], 'dry-run')
        self.sender.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_production_requires_baseline_and_rebaseline_refused(self):
        with self.assertRaisesRegex(ValueError, 'Missing baseline'):
            self.run_jobs([job()])
        self.run_jobs([], 'baseline')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_jobs([], 'baseline')

    def test_state_privacy(self):
        private = profile('profile_01', display_name='PRIVATE_PERSON_SENTINEL',
                          discord_user_id='123456789012345678', skills=['SECRET_SKILL_SENTINEL'])
        self.run_jobs([], 'baseline')
        self.run_jobs([job()], profiles={'profile_01': private})
        serialized = self.path.read_text()
        for value in ('PRIVATE_PERSON_SENTINEL', '123456789012345678', 'SECRET_SKILL_SENTINEL'):
            self.assertNotIn(value, serialized)
        self.assertIn('profile_01', serialized)

    def test_old_nonmatching_job_becomes_matching_without_new_alert(self):
        self.run_jobs([job(title='Accountant')], 'baseline')
        self.assertEqual(self.run_jobs([job()])['sent'], 0)
        self.sender.assert_not_called()

    def test_baselined_job_disappears_and_returns_without_alert(self):
        self.run_jobs([job()], 'baseline')
        self.run_jobs([])
        self.assertEqual(self.run_jobs([job()])['sent'], 0)
        self.sender.assert_not_called()

    def test_state_omits_description_payload_and_match_details(self):
        self.run_jobs([], 'baseline')
        self.run_jobs([job(description='SECRET_DESCRIPTION_SENTINEL',
                           url='https://example.invalid/jobs/1')])
        serialized = self.path.read_text()
        self.assertNotIn('SECRET_DESCRIPTION_SENTINEL', serialized)
        self.assertNotIn('description', serialized)
        self.assertNotIn('reasons', serialized)
        self.assertNotIn('last_seen', serialized)
        self.assertNotIn('raw', serialized)
        self.assertEqual(len(read_state(self.path)['seen_job_ids']), 1)

    def test_identity_ignores_url_tracking_and_requires_id(self):
        public = build_report([job()], PROFILES)['matched_jobs'][0]
        self.assertEqual(stable_identity(public), 'example:greenhouse:1')
        public['source_job_id'] = ''
        with self.assertRaises(ValueError):
            stable_identity(public)

    def test_new_source_is_baselined_after_prior_state_exists(self):
        self.run_jobs([], 'baseline', sources=[])
        self.assertEqual(self.run_jobs([job()])['sent'], 0)
        self.sender.assert_not_called()

    def test_local_lock_serializes_invocations(self):
        with state_lock(self.path):
            self.run_jobs([], 'baseline')
            self.run_jobs([job()])
        with state_lock(self.path):
            self.run_jobs([job()])
        self.assertEqual(self.sender.call_count, 1)

    def test_cli_production_requires_webhook(self):
        with patch.dict(os.environ, {'INTERNSHIP_DISCORD_WEBHOOK_URL': ''}):
            with self.assertRaises(SystemExit) as raised:
                main(['--production', '--state-file', str(self.path)])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_dry_run_never_uses_discord(self):
        with patch('internship_alerts.load_profiles', return_value=PROFILES), \
             patch('internship_alerts.monitored_sources', return_value=[SOURCE]), \
             patch('internship_alerts.fetch_jobs', return_value=([job()], [], {'Example': 1})), \
             patch('internship_alerts.send_new_job_alert', side_effect=AssertionError('Discord called')):
            self.assertEqual(main(['--dry-run', '--state-file', str(self.path)]), 0)
        self.assertFalse(self.path.exists())

    def test_provider_failure_is_visible_after_state_save(self):
        self.run_jobs([], 'baseline')
        error = {'source': 'Example', 'provider': 'greenhouse',
                 'error_type': 'RuntimeError', 'message': 'RuntimeError'}
        with patch.dict(os.environ, {'INTERNSHIP_DISCORD_WEBHOOK_URL': 'mock-webhook'}), \
             patch('internship_alerts.load_profiles', return_value=PROFILES), \
             patch('internship_alerts.monitored_sources', return_value=[SOURCE]), \
             patch('internship_alerts.fetch_jobs', return_value=([], [error], {})):
            self.assertEqual(main(['--production', '--state-file', str(self.path)]), 1)

    def test_discord_format_uses_anonymous_ids_and_unknown_date(self):
        from discord_notify import send_new_job_alert
        public = build_report([job()], PROFILES)['matched_jobs'][0]
        private = profile('profile_01', display_name='PRIVATE_NAME',
                          discord_user_id='123456789012345678')
        with patch.dict(os.environ, {'INTERNSHIP_DISCORD_WEBHOOK_URL': 'https://example.invalid/webhook'}), \
             patch('discord_notify._send') as send:
            send_new_job_alert(public, ['profile_01'], {'profile_01': private})
        payload = send.call_args.args[1]
        self.assertIn('NEW INTERNSHIP', payload['embeds'][0]['description'])
        self.assertNotIn('POSTED TODAY', payload['embeds'][0]['description'])
        self.assertIn('profile_01', json.dumps(payload))
        self.assertNotIn('PRIVATE_NAME', json.dumps(payload))
        self.assertEqual(payload['embeds'][0]['fields'][4]['value'], 'Unknown')


if __name__ == '__main__':
    unittest.main()
