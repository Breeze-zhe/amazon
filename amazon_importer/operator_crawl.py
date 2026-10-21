"""Run a durable, operation-scoped full refresh through the shared site runner."""
import argparse
from datetime import date, datetime
import hashlib
import os
import sys
import time

import requests

from . import scheduled_runner as runner


def log(message):
    print(f'[{datetime.now().astimezone().isoformat(timespec="seconds")}] {message}', flush=True)


def configure_state_paths(operation, day):
    key = hashlib.sha256(f'{operation}:{day}'.encode('utf-8')).hexdigest()[:16]
    directory = runner.BASE / '.crawl_jobs'
    runner.STATE = directory / f'operator-{key}-state.json'
    runner.STOP = directory / f'operator-{key}-stop.json'


def load_scopes(operation):
    from web_app import DEFAULT_TABLE, connect, database_config, quote_identifier

    table = quote_identifier(DEFAULT_TABLE)
    with connect(**database_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id, country FROM {table} "
                "WHERE status = 1 AND TRIM(COALESCE(operation, '')) = %s "
                "AND COALESCE(label, '') <> %s ORDER BY id ASC",
                (operation, '链接挂了'),
            )
            targets = list(cursor.fetchall())

    unsupported = {str(target.get('country') or 'U') for target in targets} - set(runner.ORDER)
    if unsupported:
        raise ValueError(f'Unsupported marketplaces: {sorted(unsupported)}')
    scopes = {
        site: [int(target['id']) for target in targets
               if str(target.get('country') or 'U') == site]
        for site in runner.ORDER
    }
    return {site: ids for site, ids in scopes.items() if ids}


def latest_job_is_active(session, base_url):
    response = session.get(base_url + '/jobs/latest', timeout=20)
    response.raise_for_status()
    job = response.json().get('job') or {}
    if job.get('status') in runner.ACTIVE:
        raise RuntimeError(f"crawl already active: {job.get('id')} ({job.get('status')})")


def run(operation, api_url, timeout_seconds, resume_auth=False):
    today = date.today().isoformat()
    configure_state_paths(operation, today)
    session = requests.Session()
    session.trust_env = False
    state = None
    owns_state = False
    try:
        with runner.acquire_locks():
            base_url = api_url.rsplit('/', 1)[0]
            latest_job_is_active(session, base_url)
            state = runner.read_state()
            if resume_auth:
                runner.validate_resume(state)
                if state.get('operation') != operation:
                    raise ValueError('saved operation does not match the requested operation')
                sites = list(state['waiting_auth'])
                owns_state = True
            else:
                if (state and state.get('count_day') == today
                        and state.get('status') in {'preparing', 'running'}):
                    if state.get('current_job'):
                        raise ValueError('an operation-scoped batch has a job in progress')
                    state.update(status='interrupted', complete=False,
                                 error='runner stopped before submitting the current site')
                    runner.save_state(state)
                if (state and state.get('count_day') == today
                        and state.get('status') == 'waiting_auth'):
                    raise ValueError('this batch is waiting for authentication; use --resume-auth')
                scopes = load_scopes(operation)
                if not scopes:
                    raise ValueError(f'no active products found for operation {operation!r}')
                state = dict(
                    run_id=datetime.now().astimezone().isoformat(), pid=os.getpid(),
                    operation=operation, count_day=today, status='preparing',
                    order=list(scopes), scopes=scopes, results={}, waiting_auth=[],
                    waiting_auth_browser={}, complete=False, force_refresh=True,
                    exclude_activity=False, skip_broken_links=True,
                    total_targets=sum(map(len, scopes.values())),
                )
                owns_state = True
                sites = list(scopes)
                log(f"start operation={operation} total={state['total_targets']} sites="
                    + ','.join(f'{site}:{len(scopes[site])}' for site in sites)
                    + ' exclude_activity=False skip_deal=True')

            state['run_id'] = datetime.now().astimezone().isoformat()
            state['pid'] = os.getpid()
            state.pop('error', None)
            state['complete'] = False
            runner.save_state(state)
            return runner.execute(
                session, base_url, state, sites, log,
                time.monotonic() + max(1, int(timeout_seconds)),
            )
    except Exception as exc:
        if state and owns_state:
            state.update(status='failed', complete=False, error=str(exc))
            runner.save_state(state)
        log(f'failed error={exc}')
        return 1
    finally:
        session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', required=True, help='Exact operation/owner name')
    parser.add_argument('--api-url', default=os.getenv(
        'AMAZON_DAY_DATA_CRAWL_URL',
        'http://127.0.0.1:6471/api/amazon-day-data/crawl',
    ))
    parser.add_argument('--timeout-seconds', type=int, default=10 * 60 * 60)
    parser.add_argument('--resume-auth', action='store_true',
                        help='resume only sites waiting for manual browser authentication')
    args = parser.parse_args(argv)
    return run(args.operation, args.api_url, args.timeout_seconds, args.resume_auth)


if __name__ == '__main__':
    sys.exit(main())
