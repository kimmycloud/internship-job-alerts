import unittest
from unittest.mock import patch

from sources.ashby import fetch_ashby_jobs
from sources.lever import fetch_lever_jobs
from sources.smartrecruiters import fetch_smartrecruiters_jobs
from sources.workday import fetch_workday_jobs
from sources.bamboohr import fetch_bamboohr_jobs


def source(provider):
    return {'provider': provider, 'employer': 'Example', 'canonical_employer': 'Example',
            'endpoint': 'https://example.invalid/jobs', 'board_id': 'example',
            'careers_url': 'https://example.invalid/en-US/example'}


class AdapterTests(unittest.TestCase):
    def test_bamboohr(self):
        job = {'id': '1', 'jobOpeningName': 'Intern'}
        with patch('sources.bamboohr.get_json', return_value={'meta': {'totalCount': 1}, 'result': [job]}):
            jobs = fetch_bamboohr_jobs(source('bamboohr'))
        self.assertEqual(jobs[0]['job_id'], '1')
        self.assertEqual(jobs[0]['location'], '')
        self.assertIsNone(jobs[0]['posted_date'])
        with patch('sources.bamboohr.get_json', return_value={'meta': {'totalCount': 0}, 'result': []}):
            self.assertEqual(fetch_bamboohr_jobs(source('bamboohr')), [])
        for payload in ({}, {'meta': {'totalCount': 2}, 'result': [job]},
                        {'meta': {'totalCount': 2}, 'result': [job, job]},
                        {'meta': {'totalCount': 1}, 'result': [{'id': '1'}]}):
            with patch('sources.bamboohr.get_json', return_value=payload), self.assertRaises(ValueError):
                fetch_bamboohr_jobs(source('bamboohr'))

    def test_ashby(self):
        job = {'id': 'a', 'title': 'Intern', 'jobUrl': 'https://example.invalid/a'}
        with patch('sources.ashby.get_json', return_value={'jobs': [job]}):
            result = fetch_ashby_jobs(source('ashby'))
        self.assertEqual((result[0]['job_id'], result[0]['location'], result[0]['posted_date']), ('a', '', None))
        with patch('sources.ashby.get_json', return_value={'jobs': []}):
            self.assertEqual(fetch_ashby_jobs(source('ashby')), [])
        for payload in ({}, {'jobs': [job, job]}, {'jobs': [{'id': 'a'}]}):
            with patch('sources.ashby.get_json', return_value=payload), self.assertRaises(ValueError):
                fetch_ashby_jobs(source('ashby'))

    def test_lever(self):
        job = {'id': 'a', 'text': 'Intern', 'hostedUrl': 'https://example.invalid/a',
               'createdAt': 1780000000000}
        with patch('sources.lever.get_json', return_value=[job]):
            result = fetch_lever_jobs(source('lever'))
        self.assertEqual(result[0]['job_id'], 'a')
        self.assertIsNone(result[0]['posted_date'])
        self.assertEqual(result[0]['location'], '')
        with patch('sources.lever.get_json', return_value=[]):
            self.assertEqual(fetch_lever_jobs(source('lever')), [])
        for payload in ({}, [job, job], [{'id': 'a'}]):
            with patch('sources.lever.get_json', return_value=payload), self.assertRaises(ValueError):
                fetch_lever_jobs(source('lever'))

    def test_smartrecruiters_pages_and_errors(self):
        def page(offset, content, total):
            return {'offset': offset, 'content': content, 'totalFound': total}
        first = {'id': 'a', 'name': 'Intern', 'location': {'city': 'Toronto'}, 'releasedDate': '2026-09-01'}
        second = {'id': 'b', 'name': 'Engineer'}
        with patch('sources.smartrecruiters.get_json', side_effect=[page(0, [first], 2), page(1, [second], 2)]) as call:
            jobs = fetch_smartrecruiters_jobs(source('smartrecruiters'))
        self.assertEqual(len(jobs), 2)
        self.assertIn('offset=1', call.call_args_list[1].args[0])
        self.assertEqual(jobs[1]['location'], '')
        self.assertIsNone(jobs[1]['posted_date'])
        with patch('sources.smartrecruiters.get_json', return_value=page(0, [], 0)):
            self.assertEqual(fetch_smartrecruiters_jobs(source('smartrecruiters')), [])
        for payloads in ([{}], [page(0, [first], 2), page(1, [], 2)],
                         [page(0, [first], 2), page(1, [first], 2)]):
            with patch('sources.smartrecruiters.get_json', side_effect=payloads), self.assertRaises(ValueError):
                fetch_smartrecruiters_jobs(source('smartrecruiters'))

    def test_workday_pages_and_errors(self):
        first = {'externalPath': '/job/a', 'title': 'Intern', 'locationsText': 'Toronto'}
        second = {'externalPath': '/job/b', 'title': 'Engineer'}
        with patch('sources.workday.get_json', side_effect=[{'total': 2, 'jobPostings': [first]},
                                                            {'total': 0, 'jobPostings': [second]}]) as call:
            jobs = fetch_workday_jobs(source('workday'))
        self.assertEqual([j['job_id'] for j in jobs], ['/job/a', '/job/b'])
        self.assertEqual(call.call_args_list[1].args[1]['offset'], 1)
        self.assertIsNone(jobs[0]['posted_date'])
        self.assertEqual(jobs[1]['location'], '')
        with patch('sources.workday.get_json', return_value={'total': 0, 'jobPostings': []}):
            self.assertEqual(fetch_workday_jobs(source('workday')), [])
        for payloads in ([{}], [{'total': 2, 'jobPostings': [first]}, {'total': 2, 'jobPostings': []}],
                         [{'total': 2, 'jobPostings': [first]}, {'total': 2, 'jobPostings': [first]}],
                         [{'total': 1, 'jobPostings': [{'externalPath': 'bad', 'title': 'Intern'}]}]):
            with patch('sources.workday.get_json', side_effect=payloads), self.assertRaises(ValueError):
                fetch_workday_jobs(source('workday'))

    def test_workday_zero_subsequent_total_and_malformed_pg_entry(self):
        valid = {'externalPath': '/job/a', 'title': 'Intern'}
        with patch('sources.workday.get_json', side_effect=[
            {'total': 2, 'jobPostings': [valid]},
            {'total': 0, 'jobPostings': [{'externalPath': '/job/b', 'title': 'Engineer'}]},
        ]):
            self.assertEqual(len(fetch_workday_jobs(source('workday'))), 2)
        with patch('sources.workday.get_json', side_effect=[
            {'total': 2, 'jobPostings': [valid]},
            {'total': 0, 'jobPostings': [{'bulletFields': ['public malformed listing']}]},
        ]), self.assertRaisesRegex(ValueError, 'Malformed ATS job entry'):
            fetch_workday_jobs(source('workday'))


if __name__ == '__main__':
    unittest.main()
