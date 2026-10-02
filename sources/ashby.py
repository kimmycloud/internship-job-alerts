"""Public Ashby posting API adapter."""

from .http import get_json, require_job


def fetch_ashby_jobs(source):
    if source.get('provider') != 'ashby' or not source.get('endpoint'):
        raise ValueError('Invalid Ashby source configuration')
    data = get_json(source['endpoint'])
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
        raise ValueError('Malformed Ashby response')
    result = []
    seen = set()
    for job in data['jobs']:
        require_job(job, ('id', 'title', 'jobUrl'))
        if job['id'] in seen:
            raise ValueError('Duplicate Ashby job ID')
        seen.add(job['id'])
        result.append({'source': 'ashby', 'employer': source['employer'],
                       'canonical_employer': source.get('canonical_employer', source['employer']),
                       'job_id': str(job['id']), 'title': job['title'],
                       'location': job.get('location') or '', 'url': job['jobUrl'],
                       'description': job.get('descriptionPlain') or job.get('descriptionHtml') or '',
                       'posted_date': job.get('publishedAt'),
                       'employment_type': job.get('employmentType') or '',
                       'team': job.get('team') or job.get('department') or ''})
    return result
