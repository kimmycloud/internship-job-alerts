"""Public Lever postings API adapter."""

from .http import get_json, require_job


def fetch_lever_jobs(source):
    if source.get('provider') != 'lever' or not source.get('endpoint'):
        raise ValueError('Invalid Lever source configuration')
    data = get_json(source['endpoint'])
    if not isinstance(data, list):
        raise ValueError('Malformed Lever response')
    result = []
    seen = set()
    for job in data:
        require_job(job, ('id', 'text', 'hostedUrl'))
        if job['id'] in seen:
            raise ValueError('Duplicate Lever job ID')
        seen.add(job['id'])
        categories = job.get('categories') or {}
        if not isinstance(categories, dict):
            raise ValueError('Malformed Lever categories')
        result.append({'source': 'lever', 'employer': source['employer'],
                       'canonical_employer': source.get('canonical_employer', source['employer']),
                       'job_id': str(job['id']), 'title': job['text'],
                       'location': categories.get('location') or '', 'url': job['hostedUrl'],
                       'description': job.get('descriptionPlain') or job.get('description') or '',
                       'posted_date': None, 'employment_type': categories.get('commitment') or '',
                       'team': categories.get('team') or categories.get('department') or ''})
    return result
