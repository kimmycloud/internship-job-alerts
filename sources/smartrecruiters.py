"""Public SmartRecruiters company postings API adapter."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .http import get_json, require_job


def _page_url(endpoint, offset, limit):
    parts = urlsplit(endpoint)
    query = dict(parse_qsl(parts.query))
    query.update(offset=str(offset), limit=str(limit))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def fetch_smartrecruiters_jobs(source):
    if source.get('provider') != 'smartrecruiters' or not source.get('endpoint') or not source.get('board_id'):
        raise ValueError('Invalid SmartRecruiters source configuration')
    result, seen, offset, limit, total = [], set(), 0, 100, None
    while total is None or offset < total:
        data = get_json(_page_url(source['endpoint'], offset, limit))
        if (not isinstance(data, dict) or not isinstance(data.get('content'), list)
                or not isinstance(data.get('totalFound'), int)
                or not isinstance(data.get('offset'), int)
                or data['offset'] != offset or data['totalFound'] < 0):
            raise ValueError('Malformed SmartRecruiters response')
        if total is None:
            total = data['totalFound']
        elif total != data['totalFound']:
            raise ValueError('SmartRecruiters total changed during pagination')
        page = data['content']
        if not page and offset < total:
            raise ValueError('Incomplete SmartRecruiters page')
        for job in page:
            require_job(job, ('id', 'name'))
            if job['id'] in seen:
                raise ValueError('Duplicate SmartRecruiters job ID')
            seen.add(job['id'])
            location = job.get('location') or {}
            if not isinstance(location, dict):
                raise ValueError('Malformed SmartRecruiters location')
            location_text = location.get('fullLocation') or ', '.join(str(location[k]) for k in ('city', 'region', 'country') if location.get(k))
            result.append({'source': 'smartrecruiters', 'employer': source['employer'],
                           'canonical_employer': source.get('canonical_employer', source['employer']),
                           'job_id': str(job['id']), 'title': job['name'],
                           'location': location_text, 'url': f"https://jobs.smartrecruiters.com/{source['board_id']}/{job['id']}",
                           'description': '', 'posted_date': job.get('releasedDate'),
                           'employment_type': (job.get('typeOfEmployment') or {}).get('label', '') if isinstance(job.get('typeOfEmployment'), dict) else '',
                           'team': (job.get('department') or {}).get('label', '') if isinstance(job.get('department'), dict) else ''})
        offset += len(page)
        if len(result) > total:
            raise ValueError('SmartRecruiters page exceeds total')
    if len(result) != total:
        raise ValueError('Incomplete SmartRecruiters listing')
    return result
