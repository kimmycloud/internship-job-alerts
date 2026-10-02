"""Small shared client for public ATS JSON endpoints."""

import json
import ssl
import urllib.request

import certifi

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def get_json(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={
        'User-Agent': 'internship-job-alerts/1.0',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
        if response.status != 200:
            raise RuntimeError(f'ATS returned HTTP {response.status}')
        return json.load(response)


def require_job(job, fields):
    if not isinstance(job, dict) or any(not isinstance(job.get(k), (str, int)) or not job.get(k) for k in fields):
        raise ValueError('Malformed ATS job entry')
