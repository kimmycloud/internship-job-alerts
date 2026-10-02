"""Public BambooHR careers listing adapter."""

from .http import get_json, require_job


def fetch_bamboohr_jobs(source):
    if source.get('provider') != 'bamboohr' or not source.get('endpoint') or not source.get('careers_url'):
        raise ValueError('Invalid BambooHR source configuration')
    data = get_json(source['endpoint'])
    if (not isinstance(data, dict) or not isinstance(data.get('result'), list)
            or not isinstance(data.get('meta'), dict)
            or not isinstance(data['meta'].get('totalCount'), int)
            or data['meta']['totalCount'] != len(data['result'])):
        raise ValueError('Malformed or incomplete BambooHR response')
    result, seen = [], set()
    for job in data['result']:
        require_job(job, ('id', 'jobOpeningName'))
        if job['id'] in seen:
            raise ValueError('Duplicate BambooHR job ID')
        seen.add(job['id'])
        location = job.get('location') or {}
        if not isinstance(location, dict):
            raise ValueError('Malformed BambooHR location')
        location_text = ', '.join(str(location[k]) for k in ('city', 'state') if location.get(k))
        result.append({'source': 'bamboohr', 'employer': source['employer'],
                       'canonical_employer': source.get('canonical_employer', source['employer']),
                       'job_id': str(job['id']), 'title': job['jobOpeningName'],
                       'location': location_text,
                       'url': source['careers_url'].rstrip('/') + '/' + str(job['id']),
                       'description': '', 'posted_date': None,
                       'employment_type': job.get('employmentStatusLabel') or '',
                       'team': job.get('departmentLabel') or ''})
    return result
