import contextlib
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internship_monitor import build_report, main, print_report, source_coverage, fetch_jobs, safe_source_error
from job_normalizer import deduplicate, freshness, normalize_job


PROFILES = {
    'sample_platform': {'id': 'sample_platform', 'education': {'year': 2, 'degree': 'Computing'},
                        'role_families': ['ml_infra', 'general_swe', 'systems'],
                        'preferred_locations': ['toronto_gta', 'remote_canada'], 'skills': ['Rust'],
                        'target_seasons': ['summer_2027']},
    'sample_circuit': {'id': 'sample_circuit', 'education': {'year': 4, 'degree': 'Engineering'},
                       'role_families': ['fpga', 'asic', 'design_verification', 'robotics_hardware'],
                       'skills': ['Verilog'], 'target_seasons': ['summer_2027']},
    'sample_network': {'id': 'sample_network', 'education': {'year': 2, 'degree': 'Computing'},
                       'role_families': ['backend', 'distributed_systems', 'networking', 'telecom', 'embedded', 'full_stack'],
                       'preferred_locations': ['ottawa', 'remote_canada'], 'javascript_preference': 'any'},
    'sample_ui': {'id': 'sample_ui', 'education': {'year': 3, 'degree': 'Computing'},
                  'role_families': ['frontend', 'full_stack', 'general_swe'], 'skills': ['Svelte']},
}


def raw(title, location='Toronto, ON', description='', job_id='1', **extra):
    return {'source': 'greenhouse', 'employer': 'Example', 'job_id': job_id,
            'title': title, 'location': location, 'description_html': description,
            'url': 'https://example.invalid/jobs/' + job_id, **extra}


class MonitorTests(unittest.TestCase):
    def test_safe_provider_diagnostics(self):
        self.assertEqual(safe_source_error(ValueError('Malformed ATS job entry')), 'Malformed ATS job entry')
        self.assertEqual(safe_source_error(ValueError('private token=SECRET')), 'ValueError')
        def fail(_source):
            raise ValueError('Malformed ATS job entry')
        _, errors, _ = fetch_jobs([{'employer': 'P&G', 'provider': 'workday'}], fail)
        self.assertEqual(errors, [{'source': 'P&G', 'provider': 'workday',
                                   'error_type': 'ValueError', 'message': 'Malformed ATS job entry'}])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_report(build_report([], PROFILES, errors))
        self.assertIn('P&G [workday]: Malformed ATS job entry', output.getvalue())

    def test_role_cases(self):
        cases = [
            ('ML Infrastructure Intern Summer 2027', 'Toronto, ON', 'Rust model serving', 'sample_platform'),
            ('Software Engineering Intern', 'Toronto, ON', 'Platform work', 'sample_platform'),
            ('Frontend React Intern', 'Toronto, ON', 'Build interfaces', 'sample_ui'),
            ('Backend Distributed Systems Intern', 'Ottawa, ON', 'Services', 'sample_network'),
            ('FPGA Intern', 'Waterloo, ON', 'Logic', 'sample_circuit'),
            ('ASIC Design Verification Intern', 'Waterloo, ON', 'UVM', 'sample_circuit'),
            ('Robotics Hardware Intern', 'Waterloo, ON', 'Sensors', 'sample_circuit'),
            ('Telecom Networking Intern', 'Kanata, ON', 'Protocols', 'sample_network'),
            ('Embedded Software Intern', 'Ottawa, ON', 'Firmware', 'sample_network'),
            ('Full-stack Co-op', 'Ottawa, ON', 'Services', 'sample_network'),
        ]
        for title, location, description, expected in cases:
            with self.subTest(title=title):
                report = build_report([raw(title, location, description)], PROFILES)
                self.assertEqual(report['summary']['matched_by_profile'][expected], 1)

    def test_rejections_and_location(self):
        cases = [
            ('Senior Software Engineer', 'Toronto, ON', 'Mentor interns'),
            ('Software Engineering Intern', 'New York, USA', ''),
            ('Software Engineering Intern Fall 2026', 'Toronto, ON', ''),
            ('Software Engineering Intern 2026', 'Toronto, ON', ''),
            ('Software Engineering Intern', 'Toronto, ON', '5 years professional experience required'),
            ('Software Engineering Intern', 'Toronto, ON', 'Graduate degree required'),
        ]
        for title, location, description in cases:
            with self.subTest(title=title, location=location, description=description):
                report = build_report([raw(title, location, description)], PROFILES)
                self.assertEqual(report['summary']['matched_jobs'], 0)

    def test_remote_hybrid_and_js(self):
        cases = [('Remote - Canada', 'remote_canada'), ('Remote', 'unknown'),
                 ('Hybrid Toronto, ON', 'toronto_gta'), ('Hybrid Ottawa, ON', 'ottawa'),
                 ('United States (Remote)', 'non_canada'), ('Singapore', 'non_canada')]
        for location, expected in cases:
            with self.subTest(location=location):
                self.assertEqual(normalize_job(raw('Backend Intern', location))['location_normalized'], expected)
        self.assertEqual(normalize_job(raw('Frontend React Intern', description='React JavaScript TypeScript CSS'))['js_intensity'], 'HIGH')
        self.assertEqual(normalize_job(raw('Backend Intern', description='Python databases'))['js_intensity'], 'LOW')
        self.assertEqual(normalize_job(raw('Software Engineer Intern - Austin, TX', 'In-Office'))['location_normalized'], 'non_canada')

    def test_workday_path_location_when_display_is_generic(self):
        sample = raw('Software Engineering Intern', '5 Locations')
        sample['url'] = 'https://example.invalid/job/US-Oregon-Hillsboro_R123'
        normalized = normalize_job(sample)
        self.assertEqual(normalized['location_normalized'], 'non_canada')
        self.assertEqual(build_report([sample], PROFILES)['summary']['matched_jobs'], 0)
        sample['url'] = 'https://example.invalid/job/R123'
        self.assertEqual(normalize_job(sample)['location_normalized'], 'unknown')
        sample['location'] = 'Toronto, Canada; Hillsboro, Oregon'
        sample['url'] = 'https://example.invalid/job/US-Oregon-Hillsboro_R123'
        self.assertEqual(normalize_job(sample)['location_normalized'], 'toronto_gta')

    def test_incidental_hardware_description_does_not_match_software(self):
        job = raw('Digital Design Engineer Intern', description='ASIC and RTL design with a software team. ML infrastructure is a partner.')
        report = build_report([job], PROFILES)
        self.assertEqual(report['summary']['matched_by_profile']['sample_platform'], 0)
        self.assertEqual(report['summary']['matched_by_profile']['sample_network'], 0)
        self.assertEqual(report['summary']['matched_by_profile']['sample_circuit'], 1)
        web = build_report([raw('Software Developer Intern, Web', description='Work with embedded partner teams')], PROFILES)
        self.assertEqual(web['summary']['matched_by_profile']['sample_circuit'], 0)
        us_title = build_report([raw('Software Engineering Intern - Austin, TX', location='In-Office')], PROFILES)
        self.assertEqual(us_title['summary']['matched_jobs'], 0)

    def test_dates_and_requisitions(self):
        unknown = normalize_job(raw('Backend Intern'))
        self.assertEqual(freshness(unknown)['bucket'], 'UNKNOWN')
        self.assertIsNone(unknown['posted_date'])
        for posted, bucket in [('2026-09-30', 'NEW'), ('2026-09-25', 'RECENT'), ('2026-09-01', 'OLDER')]:
            self.assertEqual(freshness({'posted_date': posted}, date(2026, 9, 30))['bucket'], bucket)
        same = normalize_job(raw('Backend Intern', job_id='12'))
        distinct = normalize_job(raw('Backend Intern', job_id='13'))
        self.assertEqual(len(deduplicate([same, same.copy(), distinct])), 2)
        old_board = raw('Firmware Engineer Intern', job_id='old')
        old_board['employer'] = 'Example Early Career 2026'
        self.assertEqual(normalize_job(old_board)['summer_2027_relevance'], 'other')
        self.assertEqual(normalize_job(raw('Software Intern Summer 2027'))['summer_2027_relevance'], 'target')
        self.assertEqual(normalize_job(raw('Software Intern Fall 2027'))['summer_2027_relevance'], 'other')
        self.assertEqual(normalize_job(raw('Software Intern'))['summer_2027_relevance'], 'unknown')

    def test_private_data_not_in_report_or_output_and_no_discord(self):
        sentinel = 'PRIVATE_SKILL_SENTINEL'
        profiles = {'profile_01': {**PROFILES['sample_platform'], 'id': 'profile_01',
                                   'skills': [sentinel], 'display_name': 'PRIVATE_NAME_SENTINEL',
                                   'discord_user_id': '123456789012345678'}}
        with patch('discord_notify._send', side_effect=AssertionError('Discord called')):
            report = build_report([raw('Software Engineering Intern', description=sentinel)], profiles)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                print_report(report)
        serialized = json.dumps(report)
        for secret in (sentinel, 'PRIVATE_NAME_SENTINEL', '123456789012345678'):
            self.assertNotIn(secret, serialized + output.getvalue())
        self.assertIn('profile_01', serialized)

    def test_cli_dry_run_does_not_send(self):
        anonymous = {f'profile_{index:02d}': {**profile, 'id': f'profile_{index:02d}'}
                     for index, profile in enumerate(PROFILES.values(), 1)}
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / 'report.json'
            with patch('internship_monitor.load_profiles', return_value=anonymous), \
                 patch('internship_monitor.monitored_sources', return_value=[{'employer': 'Example'}]), \
                 patch('internship_monitor.fetch_jobs', return_value=([raw('Backend Intern', 'Ottawa, ON')], [], {'Example': 1})), \
                 patch('discord_notify._send', side_effect=AssertionError('Discord called')):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['--dry-run', '--report-file', str(report_path)]), 0)
            self.assertTrue(report_path.exists())
            self.assertIn('profile_03', report_path.read_text())

    def test_cli_preserves_failure_exit_and_safe_error(self):
        anonymous = {f'profile_{index:02d}': {**profile, 'id': f'profile_{index:02d}'}
                     for index, profile in enumerate(PROFILES.values(), 1)}
        error = {'source': 'P&G', 'provider': 'workday', 'error_type': 'ValueError',
                 'message': 'Malformed ATS job entry'}
        with tempfile.TemporaryDirectory() as directory, \
             patch('internship_monitor.load_profiles', return_value=anonymous), \
             patch('internship_monitor.monitored_sources', return_value=[]), \
             patch('internship_monitor.fetch_jobs', return_value=([], [error], {})):
            output = io.StringIO()
            report_path = Path(directory) / 'report.json'
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['--dry-run', '--top', '0', '--report-file', str(report_path)]), 1)
            self.assertIn('P&G [workday]: Malformed ATS job entry', output.getvalue())
            self.assertEqual(json.loads(report_path.read_text())['summary']['provider_errors'], [error])

    def test_coverage_and_valid_zero_versus_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / 'companies.json'
            registry.write_text(json.dumps({'companies': [
                {'employer': 'Empty', 'provider': 'greenhouse', 'monitoring_ready': True},
                {'employer': 'Failed', 'provider': 'greenhouse', 'monitoring_ready': True},
                {'employer': 'Partial', 'provider': 'unknown_ats', 'monitoring_ready': True},
                {'employer': 'No Route', 'provider': 'branded_or_unconfirmed', 'monitoring_ready': False, 'accessible': True},
            ]}))
            sources = [{'employer': 'Empty'}, {'employer': 'Failed'}]
            def fake_fetch(source):
                if source['employer'] == 'Failed':
                    raise ValueError('bad payload')
                return []
            jobs, errors, fetched = fetch_jobs(sources, fake_fetch)
            coverage = source_coverage(registry, errors, fetched)
        self.assertEqual(jobs, [])
        self.assertEqual(fetched, {'Empty': 0})
        self.assertEqual(coverage['counts'], {'MONITORING_READY': 1, 'PARTIAL': 1,
                                             'BROKEN': 1, 'NO_ROUTE': 1})
        outcomes = {r['employer']: r['routes'][0]['fetch_outcome'] for r in coverage['employers']}
        self.assertEqual(outcomes['Empty'], 'VALID_ZERO_JOBS')
        self.assertEqual(outcomes['Failed'], 'FETCH_FAILED')


if __name__ == '__main__':
    unittest.main()
