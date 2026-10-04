"""Persistent, conservative new-discovery alerts for supported ATS sources."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile

from discord_notify import send_new_job_alert
from internship_monitor import (ROOT, build_report, fetch_jobs, monitored_sources,
                                source_coverage)
from job_normalizer import deduplicate, normalize_job, stable_identity
from profiles import PUBLIC_PROFILE_IDS, ProfileError, load_profiles

DEFAULT_STATE = ROOT / 'internship_alert_state.json'
STATE_VERSION = 2


def source_key(provider, source_name):
    return f'{provider}:{source_name}'


def alertable(job):
    """Require a strong student and family match; permit an unknown term."""
    if not job['student_role'] or not job['role_families']:
        return False
    if job['summer_2027_relevance'] == 'other' or job['location_normalized'] in ('unknown', 'non_canada'):
        return False
    return any(decision['matched'] and decision['role_match']
               and decision['student_eligibility_match'] is True
               and not decision['exclusions_triggered']
               for decision in job['matches'].values())


def read_state(path):
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding='utf-8'))
    if (state.get('schema_version') != STATE_VERSION or
            not isinstance(state.get('jobs'), dict) or
            not isinstance(state.get('seen_job_ids'), list) or
            not isinstance(state.get('initialized_sources'), list)):
        raise ValueError('Invalid internship alert state')
    return state


def write_state(path, state):
    """Atomically save after each successful delivery or retryable failure."""
    serialized = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + '\n'
    if path.exists() and path.read_text(encoding='utf-8') == serialized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def state_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a+', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def process_jobs(raw_jobs, profiles, successful_sources, state_path, mode,
                 sender=send_new_job_alert, now=None):
    """Caller holds the local lock. Persist each transition before continuing."""
    now = now or datetime.now(timezone.utc).isoformat()
    state = read_state(state_path)
    if mode == 'baseline':
        if state is not None:
            raise ValueError('Baseline already exists; refusing to replace alert history')
        state = {'schema_version': STATE_VERSION, 'initialized_sources': [],
                 'seen_job_ids': [], 'jobs': {}}
    elif mode == 'production' and state is None:
        raise ValueError('Missing baseline; run --baseline first')
    elif mode == 'dry-run':
        state = state or {'schema_version': STATE_VERSION, 'initialized_sources': [],
                          'seen_job_ids': [], 'jobs': {}}
    else:
        if mode not in ('baseline', 'production'):
            raise ValueError('Invalid alert mode')

    report = build_report(raw_jobs, profiles)
    matched = {stable_identity(job): job for job in report['matched_jobs']}
    normalized = deduplicate([normalize_job(raw) for raw in raw_jobs])
    initialized = set(state['initialized_sources'])
    successful = {source_key(source['provider'], source['employer']) for source in successful_sources}
    previously_seen = set(state['seen_job_ids'])
    seen = set(previously_seen)
    counts = {'baselined': 0, 'sent': 0, 'failed': 0, 'pending': 0, 'seen': 0}
    for job in normalized:
        key = stable_identity(job)
        source = source_key(job['provider'], job['source_name'])
        seen.add(key)
        public_match = matched.get(key)
        profile_ids = sorted(pid for pid, decision in (public_match or {}).get('matches', {}).items()
                             if decision['matched'])
        if any(pid not in PUBLIC_PROFILE_IDS for pid in profile_ids):
            raise ValueError('Unexpected profile ID in alert match')
        record = state['jobs'].get(key)
        if mode == 'baseline' or source not in initialized:
            counts['baselined'] += 1
        elif record is not None and record['status'] == 'alerted':
            counts['seen'] += 1
        elif (key not in previously_seen or
              record is not None and record['status'] == 'pending') and public_match and alertable(public_match):
            if mode == 'production':
                # The pre-send save makes a newly discovered job retryable after a crash.
                if record is None:
                    record = {'company': job['company'], 'title': job['title'],
                              'url': job['url'], 'first_seen': now,
                              'matched_profile_ids': profile_ids, 'status': 'pending'}
                    state['jobs'][key] = record
                else:
                    record.update(title=job['title'], url=job['url'],
                                  matched_profile_ids=profile_ids)
                state['seen_job_ids'] = sorted(seen)
                write_state(state_path, state)
                try:
                    sender(public_match, profile_ids, profiles)
                except Exception:
                    counts['failed'] += 1
                    write_state(state_path, state)
                    continue
                record['status'] = 'alerted'
                write_state(state_path, state)
                counts['sent'] += 1
            else:
                counts['pending'] += 1
        else:
            counts['seen'] += 1
    state['seen_job_ids'] = sorted(seen)
    state['initialized_sources'] = sorted(initialized | successful)
    if mode != 'dry-run':
        write_state(state_path, state)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description='Baseline or send new internship alerts')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--baseline', action='store_true')
    mode.add_argument('--production', action='store_true')
    parser.add_argument('--state-file', type=Path, default=DEFAULT_STATE)
    args = parser.parse_args(argv)
    selected = 'baseline' if args.baseline else 'production' if args.production else 'dry-run'
    if selected == 'production' and not os.environ.get('INTERNSHIP_DISCORD_WEBHOOK_URL'):
        parser.exit(2, 'Production requires INTERNSHIP_DISCORD_WEBHOOK_URL\n')
    try:
        profiles = load_profiles()
        if set(profiles) != set(PUBLIC_PROFILE_IDS):
            raise ProfileError('Expected four anonymous profile IDs')
    except ProfileError as exc:
        parser.exit(2, f'Profile loading failed: {exc}\n')
    sources = monitored_sources()
    raw_jobs, errors, fetched = fetch_jobs(sources)
    successful = [source for source in sources if source['employer'] in fetched]
    with state_lock(args.state_file):
        try:
            counts = process_jobs(raw_jobs, profiles, successful, args.state_file, selected)
        except ValueError as exc:
            parser.exit(2, f'Alert state error: {exc}\n')
    coverage = source_coverage(errors=errors, fetched=fetched)
    print(f'Mode: {selected}; fetched: {len(raw_jobs)}; sources OK: {len(fetched)}; '
          f'baselined: {counts["baselined"]}; sent: {counts["sent"]}; '
          f'failed deliveries: {counts["failed"]}; pending: {counts["pending"]}')
    print(f'Source coverage: {coverage["counts"]}; provider failures: {len(errors)}')
    for error in errors:
        print(f'Provider failure: {error["source"]} [{error["provider"]}]: {error["message"]}')
    return 1 if errors or counts['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
