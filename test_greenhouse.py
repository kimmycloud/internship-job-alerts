import io
import json
import unittest
from unittest.mock import patch

from sources.greenhouse import fetch_greenhouse_jobs


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class GreenhouseTests(unittest.TestCase):
    def test_adapter_shape(self):
        payload = {'jobs': [{'id': 42, 'title': 'Example Intern',
                             'location': {'name': 'Toronto, ON'},
                             'absolute_url': 'https://example.invalid/42',
                             'content': '<p>Student role</p>',
                             'updated_at': '2026-09-30T00:00:00Z'}]}
        source = {'provider': 'greenhouse', 'employer': 'Example',
                  'endpoint': 'https://example.invalid/jobs'}
        with patch('sources.greenhouse.urllib.request.urlopen', return_value=FakeResponse(json.dumps(payload).encode())):
            jobs = fetch_greenhouse_jobs(source)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['job_id'], '42')
        self.assertEqual(jobs[0]['description_html'], '<p>Student role</p>')

    def test_valid_zero_and_invalid_payload(self):
        source = {'provider': 'greenhouse', 'employer': 'Example',
                  'endpoint': 'https://example.invalid/jobs'}
        with patch('sources.greenhouse.urllib.request.urlopen', return_value=FakeResponse(b'{"jobs": []}')):
            self.assertEqual(fetch_greenhouse_jobs(source), [])
        with patch('sources.greenhouse.urllib.request.urlopen', return_value=FakeResponse(b'{"unexpected": []}')):
            with self.assertRaises(ValueError):
                fetch_greenhouse_jobs(source)


if __name__ == '__main__':
    unittest.main()
