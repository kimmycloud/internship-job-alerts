"""Normalize provider jobs for matching and reporting."""

from datetime import datetime, timezone
import re
from urllib.parse import unquote, urlsplit

from job_matcher import (classify_role_families, internship_eligibility,
                         javascript_intensity, normalize_location)


def _date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).date().isoformat()
    except ValueError:
        return None


def normalize_job(raw):
    """Greenhouse updated_at is not a posting date, so leave posted_date unknown."""
    title = raw.get('title') or ''
    description = raw.get('description') or raw.get('description_html') or ''
    location = raw.get('location') or ''
    job = {
        'provider': raw.get('source') or raw.get('provider') or 'unknown',
        'source_job_id': str(raw.get('job_id') or raw.get('source_job_id') or ''),
        'company': raw.get('canonical_employer') or raw.get('employer') or raw.get('company') or '',
        'source_name': raw.get('employer') or raw.get('company') or '',
        'title': title, 'location': location, 'raw_location': location,
        'location_normalized': normalize_location(location),
        'normalized_location': normalize_location(location),
        'description': description, 'url': raw.get('url') or '',
        'posted_date': _date(raw.get('posted_date')),
        'employment_type': raw.get('employment_type') or '',
        'team': raw.get('team') or raw.get('category') or '',
    }
    job['student_role'] = internship_eligibility(job) is True
    if job['location_normalized'] == 'unknown':
        title_location = normalize_location(title)
        if title_location in {'non_canada', 'toronto_gta', 'ottawa', 'other_canadian_city'}:
            job['location_normalized'] = title_location
            job['normalized_location'] = title_location
    if job['location_normalized'] == 'unknown':
        # Workday requisition paths sometimes encode the selected office while
        # the displayed location is only a count (for example, "5 Locations").
        path = unquote(urlsplit(job['url']).path)
        if re.search(r'(?<![A-Za-z])(?:Canada[-_](?:Ontario[-_])?(?:Toronto|Ottawa)|CA[-_]Ontario[-_](?:Toronto|Ottawa))(?![A-Za-z])', path, re.I):
            job['location_normalized'] = normalize_location(path)
            job['normalized_location'] = job['location_normalized']
        elif re.search(r'(?<![A-Za-z])US[-_](?:Oregon|Texas|Washington|California|New[-_]York)[-_][A-Za-z]+(?![A-Za-z])', path, re.I):
            job['location_normalized'] = 'non_canada'
            job['normalized_location'] = 'non_canada'
    text = title + ' ' + re.sub(r'<[^>]+>', ' ', description)
    terms = {(m.group(1).lower().replace('autumn', 'fall'), m.group(2))
             for m in re.finditer(r'\b(summer|fall|autumn|winter|spring)\s+(20\d{2})\b', text, re.I)}
    job['summer_2027_relevance'] = ('target' if ('summer', '2027') in terms else
                                    'other' if terms or re.search(r'\b2026\b', title + ' ' + job['source_name']) else 'unknown')
    job['role_families'] = sorted(classify_role_families(job))
    job['js_intensity'] = javascript_intensity(job, set(job['role_families']))
    job['javascript_intensity'] = job['js_intensity']
    return job


def identity(job):
    company = re.sub(r'\W+', '', job['company'].casefold())
    if job['source_job_id']:
        return ('id', company, job['provider'], job['source_job_id'])
    url = urlsplit(job['url'])
    path = url.path.rstrip('/')
    return ('url', company, url.netloc.lower(), path) if path else ('unique', id(job))


def deduplicate(jobs):
    seen = set()
    unique = []
    for job in jobs:
        key = identity(job)
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def freshness(job, today=None):
    today = today or datetime.now(timezone.utc).date()
    if not job.get('posted_date'):
        return {'bucket': 'UNKNOWN', 'age_days': None}
    age = (today - datetime.fromisoformat(job['posted_date']).date()).days
    return {'bucket': 'NEW' if age <= 1 else 'RECENT' if age <= 7 else 'OLDER',
            'age_days': max(age, 0)}
