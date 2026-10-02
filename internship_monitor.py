"""Read-only internship matching report. This module has no Discord sender."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from urllib.error import HTTPError

from job_matcher import match_profiles
from job_normalizer import deduplicate, freshness, normalize_job
from profiles import PUBLIC_PROFILE_IDS, ProfileError, load_profiles
from sources.greenhouse import fetch_greenhouse_jobs
from sources.ashby import fetch_ashby_jobs
from sources.lever import fetch_lever_jobs
from sources.smartrecruiters import fetch_smartrecruiters_jobs
from sources.workday import fetch_workday_jobs
from sources.bamboohr import fetch_bamboohr_jobs

ROOT = Path(__file__).resolve().parent
DEFAULT_REPORT = ROOT / 'dry_run_matches.json'
BUCKET_ORDER = {'NEW': 0, 'RECENT': 1, 'UNKNOWN': 2, 'OLDER': 3}
ADAPTERS = {'greenhouse': fetch_greenhouse_jobs, 'ashby': fetch_ashby_jobs,
            'lever': fetch_lever_jobs, 'smartrecruiters': fetch_smartrecruiters_jobs,
            'workday': fetch_workday_jobs, 'bamboohr': fetch_bamboohr_jobs}
SAFE_ADAPTER_ERRORS = {
    'Invalid BambooHR source configuration', 'Malformed or incomplete BambooHR response',
    'Duplicate BambooHR job ID', 'Malformed BambooHR location',
    'Invalid Ashby source configuration', 'Malformed Ashby response', 'Duplicate Ashby job ID',
    'Invalid Lever source configuration', 'Malformed Lever response',
    'Duplicate Lever job ID', 'Malformed Lever categories',
    'Invalid Greenhouse jobs response', 'Invalid Greenhouse job entry', 'Invalid Greenhouse location',
    'Invalid SmartRecruiters source configuration', 'Malformed SmartRecruiters response',
    'SmartRecruiters total changed during pagination', 'Incomplete SmartRecruiters page',
    'Duplicate SmartRecruiters job ID', 'Malformed SmartRecruiters location',
    'SmartRecruiters page exceeds total', 'Incomplete SmartRecruiters listing',
    'Invalid Workday source configuration', 'Malformed Workday response',
    'Workday total changed during pagination', 'Incomplete Workday page',
    'Malformed Workday job path', 'Duplicate Workday job ID',
    'Workday page exceeds total', 'Incomplete Workday listing', 'Malformed ATS job entry',
}


def safe_source_error(exc):
    """Expose adapter diagnostics, never arbitrary upstream text or request URLs."""
    if isinstance(exc, ValueError) and str(exc) in SAFE_ADAPTER_ERRORS:
        return str(exc)
    if isinstance(exc, HTTPError):
        return f'HTTP {exc.code}'
    return type(exc).__name__


def monitored_sources(registry_path=ROOT / 'companies.json'):
    registry = json.loads(Path(registry_path).read_text(encoding='utf-8'))
    return [source for source in registry['companies']
            if source.get('provider') in ADAPTERS and source.get('monitoring_ready') is True]


def source_coverage(registry_path=ROOT / 'companies.json', errors=None, fetched=None):
    """Account for each canonical employer, including routes without an adapter."""
    registry = json.loads(Path(registry_path).read_text(encoding='utf-8'))
    failures = {item['source'] for item in (errors or [])}
    fetched = fetched or {}
    employers = {}
    for source in registry['companies']:
        name = source.get('canonical_employer') or source['employer']
        if source['employer'] in failures:
            status, outcome = 'BROKEN', 'FETCH_FAILED'
        elif source.get('provider') in ADAPTERS and source.get('monitoring_ready'):
            status = 'MONITORING_READY'
            outcome = ('VALID_ZERO_JOBS' if fetched.get(source['employer']) == 0 else
                       'FETCH_OK' if source['employer'] in fetched else 'NOT_FETCHED')
        elif source.get('monitoring_ready'):
            status, outcome = 'PARTIAL', 'ADAPTER_MISSING'
        elif source.get('accessible') is False:
            status, outcome = 'BROKEN', 'SOURCE_UNAVAILABLE'
        else:
            status, outcome = 'NO_ROUTE', 'NO_PARSED_ROUTE'
        employers.setdefault(name, []).append({'source': source['employer'],
                                                 'provider': source.get('provider', 'unknown'),
                                                 'status': status, 'fetch_outcome': outcome,
                                                 'jobs_fetched': fetched.get(source['employer'])})
    def employer_status(routes):
        states = {route['status'] for route in routes}
        if 'MONITORING_READY' in states:
            return 'MONITORING_READY' if states == {'MONITORING_READY'} else 'PARTIAL'
        if 'PARTIAL' in states:
            return 'PARTIAL'
        if 'BROKEN' in states:
            return 'BROKEN'
        return 'NO_ROUTE'

    rows = [{'employer': name, 'status': employer_status(routes), 'routes': routes}
            for name, routes in sorted(employers.items())]
    counts = Counter(row['status'] for row in rows)
    return {'target_employers': len(rows),
            'counts': {key: counts[key] for key in ('MONITORING_READY', 'PARTIAL', 'BROKEN', 'NO_ROUTE')},
            'employers': rows}


def fetch_source(source):
    return ADAPTERS[source['provider']](source)


def fetch_jobs(sources, fetcher=None):
    fetcher = fetcher or fetch_source
    jobs, errors, fetched = [], [], {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetcher, source): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                source_jobs = future.result()
                fetched[source['employer']] = len(source_jobs)
                jobs.extend(source_jobs)
            except Exception as exc:
                errors.append({'source': source['employer'], 'provider': source.get('provider', 'unknown'),
                               'error_type': type(exc).__name__, 'message': safe_source_error(exc)})
    return jobs, sorted(errors, key=lambda item: item['source']), fetched


def public_decision(decision):
    """Publish match evidence without copying private skills or profile fields."""
    components = decision['score_components']
    reasons = [reason for reason in decision['reasons'] if not reason.startswith('Skill overlap:')]
    if components['skill_overlap']:
        reasons.append(f"{len(components['skill_overlap'])} matching skill(s)")
    return {
        'profile_id': decision['profile_id'], 'matched': decision['matched'],
        'role_match': components['role_families'],
        'location_match': components['location_fit'],
        'student_eligibility_match': components['internship_eligibility'],
        'skill_overlap_count': len(components['skill_overlap']),
        'preference_alignment': components['season_fit'],
        'exclusions_triggered': components['excluded_families'],
        'javascript_intensity': components['javascript_intensity'],
        'reasons': reasons, 'warnings': decision['warnings'],
    }


def rejection_reason(job, decisions):
    if not job['student_role']:
        return 'not student role'
    if job['summer_2027_relevance'] == 'other':
        return 'eligibility mismatch'
    if job['location_normalized'] == 'non_canada':
        return 'location mismatch'
    if not job['role_families']:
        return 'insufficient information'
    if all(not item['role_match'] for item in decisions.values()):
        return 'role-family mismatch'
    if all(item['location_match'] == 'outside_preference' for item in decisions.values()):
        return 'location mismatch'
    if any(item['exclusions_triggered'] for item in decisions.values()):
        return 'role-family mismatch'
    return 'eligibility mismatch'


def build_report(raw_jobs, profiles, provider_errors=None, coverage=None, successful_sources=0):
    normalized = deduplicate([normalize_job(raw) for raw in raw_jobs])
    matches, rejections = [], Counter()
    counts = Counter({profile_id: 0 for profile_id in profiles})
    student_count = 0
    unmatched_students = 0
    for job in normalized:
        if job['student_role']:
            student_count += 1
        decisions = {key: public_decision(value)
                     for key, value in match_profiles(job, profiles).items()}
        if job['summer_2027_relevance'] == 'other':
            for decision in decisions.values():
                decision['matched'] = False
                decision['warnings'].append('Outside Summer 2027 target term')
        matched = [key for key, value in decisions.items() if value['matched']]
        if not matched:
            rejections[rejection_reason(job, decisions)] += 1
            if job['student_role']:
                unmatched_students += 1
            continue
        for profile_id in matched:
            counts[profile_id] += 1
        public_job = {key: value for key, value in job.items() if key != 'description'}
        public_job.update(freshness(job))
        public_job['matches'] = decisions
        matches.append(public_job)
    matches.sort(key=lambda job: (BUCKET_ORDER[job['bucket']],
                                  0 if job['summer_2027_relevance'] == 'target' else 1,
                                  job['age_days'] or 0,
                                  job['company'], job['title']))
    return {
        'coverage': coverage or source_coverage(),
        'summary': {'total_jobs_fetched': len(raw_jobs), 'successful_sources': successful_sources,
                    'unique_jobs': len(normalized),
                    'student_roles': student_count, 'matched_jobs': len(matches),
                    'matched_by_profile': dict(sorted(counts.items())),
                    'unmatched_student_roles': unmatched_students,
                    'rejection_reasons': dict(sorted(rejections.items())),
                    'provider_errors': provider_errors or []},
        'matched_jobs': matches,
    }


def print_report(report, review=False, top=10):
    summary = report['summary']
    coverage = report['coverage']
    print('=== SOURCE COVERAGE ===')
    print(f"Target employers: {coverage['target_employers']}")
    for status, label in (('MONITORING_READY', 'Monitoring-ready'), ('PARTIAL', 'Partial'),
                          ('BROKEN', 'Broken'), ('NO_ROUTE', 'No route')):
        print(f"{label}: {coverage['counts'][status]}")
    print('\n=== FETCH ===')
    print(f"Jobs fetched: {summary['total_jobs_fetched']}")
    print(f"Successful sources: {summary['successful_sources']}")
    print(f"Errors: {len(summary['provider_errors'])}")
    for error in summary['provider_errors']:
        print(f"  {error['source']} [{error.get('provider', 'unknown')}]: {error.get('message', error['error_type'])}")
    print('\n=== STUDENT ROLES ===')
    print(f"Candidate student roles: {summary['student_roles']}")
    print(f"Unmatched student roles: {summary['unmatched_student_roles']}")
    print('\n=== MATCHES ===')
    print(f"Matched jobs: {summary['matched_jobs']}")
    for profile_id in PUBLIC_PROFILE_IDS:
        print(f"{profile_id}: {summary['matched_by_profile'].get(profile_id, 0)}")
    print('Common rejection reasons:')
    for reason, count in summary['rejection_reasons'].items():
        print(f'  {count}: {reason}')
    print('\n=== MATCHED JOBS ===')
    if review:
        selections = [('all profiles', report['matched_jobs'])]
    else:
        selections = [(profile_id, [job for job in report['matched_jobs']
                                    if job['matches'][profile_id]['matched']][:top])
                      for profile_id in summary['matched_by_profile']]
    for label, jobs in selections:
        print(f'\n{label} ({len(jobs)} shown)')
        for job in jobs:
            age = 'unknown' if job['age_days'] is None else f"{job['age_days']} day(s) ago"
            print(f"- [{job['bucket']}] {job['company']} — {job['title']} | {job['location']} ({job['location_normalized']})")
            print(f"  {job['provider']} | Posted: {age} | {job['url']}")
            print(f"  Families: {', '.join(job['role_families']) or 'unknown'} | JS: {job['js_intensity']}")
            for profile_id, decision in job['matches'].items():
                if decision['matched']:
                    print(f"  {profile_id}: {'; '.join(decision['reasons'])}")
                    if decision['warnings']:
                        print(f"    Uncertainty: {'; '.join(decision['warnings'])}")


def main(argv=None):
    parser = argparse.ArgumentParser(description='Fetch and review internship matches without sending alerts')
    parser.add_argument('--dry-run', action='store_true', help='Explicit dry-run marker (also the only mode)')
    parser.add_argument('--review', action='store_true', help='Show every matched job')
    parser.add_argument('--top', type=int, default=10, help='Matches shown per profile in standard output')
    parser.add_argument('--report-file', type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    try:
        profiles = load_profiles()
        if set(profiles) != set(PUBLIC_PROFILE_IDS):
            raise ProfileError('Expected four anonymous profile IDs')
    except ProfileError as exc:
        parser.exit(2, f'Profile loading failed: {exc}\n')
    raw_jobs, errors, fetched = fetch_jobs(monitored_sources())
    coverage = source_coverage(errors=errors, fetched=fetched)
    report = build_report(raw_jobs, profiles, errors, coverage, len(fetched))
    args.report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print_report(report, review=args.review, top=max(args.top, 0))
    print(f'\nJSON report: {args.report_file}')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
