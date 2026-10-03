"""Public Workday CXS jobs adapter, using each configured tenant endpoint."""

import re
from urllib.parse import urlsplit

from .http import get_json, require_job


def fetch_workday_jobs(source):
    if source.get('provider') != 'workday' or not source.get('endpoint') or not source.get('careers_url'):
        raise ValueError('Invalid Workday source configuration')
    result, seen, offset, total = [], set(), 0, None
    while total is None or offset < total:
        data = get_json(source['endpoint'], {'appliedFacets': {}, 'limit': 20,
                                             'offset': offset, 'searchText': ''})
        if (not isinstance(data, dict) or not isinstance(data.get('jobPostings'), list)
                or not isinstance(data.get('total'), int) or data['total'] < 0):
            raise ValueError('Malformed Workday response')
        if total is None:
            total = data['total']
        elif data['total'] not in (0, total):
            raise ValueError('Workday total changed during pagination')
        page = data['jobPostings']
        if not page and offset < total:
            raise ValueError('Incomplete Workday page')
        for job in page:
            require_job(job, ('externalPath', 'title'))
            path = job['externalPath']
            if not path.startswith('/job/') or urlsplit(path).netloc:
                raise ValueError('Malformed Workday job path')
            if path in seen:
                raise ValueError('Duplicate Workday job ID')
            seen.add(path)
            base = source['careers_url'].rstrip('/')
            requisition = re.search(r'_(R[-_]?\d+|JR[-_]?\d+|REQ[-_]?\d+)$',
                                    path.rstrip('/'), re.I)
            stable_id = requisition.group(1).upper() if requisition else path
            result.append({'source': 'workday', 'employer': source['employer'],
                           'canonical_employer': source.get('canonical_employer', source['employer']),
                           'job_id': stable_id, 'title': job['title'],
                           'location': job.get('locationsText') or '',
                           'url': base + path, 'description': '',
                           'posted_date': None})
        offset += len(page)
        if len(result) > total:
            raise ValueError('Workday page exceeds total')
    if len(result) != total:
        raise ValueError('Incomplete Workday listing')
    return result
