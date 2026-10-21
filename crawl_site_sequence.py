"""One-shot, ordered J -> A -> U crawl; never installs/enables a scheduler."""
import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import subprocess
import time

import requests
import websocket

from amazon_importer.crawl_jobs import crawl_lease
from amazon_importer.day_crawler import (
    fetch_targets, find_chrome_binary, get_site_domain, is_blocked_html,
)
from web_app import connect, database_config


BASE = Path(__file__).resolve().parent
ORDER = ('J', 'A', 'U')
PORTS = {'J': 9230, 'A': 9231, 'U': 9232}
ACTIVE = {'queued', 'running', 'stopping'}
STATE = BASE / '.remaining_sites_sequence.json'


def publish(state, **values):
    state.update(values, updated=datetime.now().astimezone().isoformat())
    temporary = STATE.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(STATE)
    print(json.dumps(state, ensure_ascii=True), flush=True)


def wait_job(session, base_url, job_id, crawl_day):
    while True:
        if date.today().isoformat() != crawl_day:
            raise RuntimeError('Date changed: do not submit another daily crawl')
        response = session.get(base_url + '/api/amazon-day-data/jobs/' + job_id, timeout=20)
        response.raise_for_status()
        job = response.json()['job']
        if job['status'] not in ACTIVE:
            return job
        time.sleep(5)


def page_requires_auth(session, site):
    port = PORTS[site]
    endpoint = f'http://127.0.0.1:{port}/json/list'
    try:
        response = session.get(endpoint, timeout=3)
        response.raise_for_status()
    except requests.ConnectionError:
        startup = subprocess.STARTUPINFO() if os.name == 'nt' else None
        if startup:
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
        binary = find_chrome_binary()
        if not binary:
            raise RuntimeError('Local Chrome not found')
        subprocess.Popen([
            binary, '--disable-features=NetworkServiceSandbox',
            '--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}',
            f'--user-data-dir={BASE / (".amazon_chrome_sequence_" + site)}',
            '--no-first-run', '--no-default-browser-check', '--new-window',
            f'https://{get_site_domain(site)}/',
        ], startupinfo=startup)
        for attempt in range(15):
            time.sleep(1)
            try:
                response = session.get(endpoint, timeout=3)
                response.raise_for_status()
                break
            except requests.ConnectionError:
                if attempt == 14:
                    raise
    pages = [tab for tab in response.json() if tab.get('type') == 'page']
    if not pages:
        raise RuntimeError(f'{site}: dedicated browser has no page')
    tab = pages[0]
    ws = websocket.create_connection(tab['webSocketDebuggerUrl'], timeout=15, suppress_origin=True)
    try:
        ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
            'expression': 'document.documentElement.outerHTML', 'returnByValue': True,
        }}))
        while True:
            result = json.loads(ws.recv())
            if result.get('id') == 1:
                return is_blocked_html(result['result']['result']['value'])
    finally:
        ws.close()


def execute_sequence(session, base_url, scopes, state):
    for site in ORDER:
        if date.today().isoformat() != state['count_day']:
            raise RuntimeError('Date changed before next site')
        publish(state, status='preparing', site=site, current_job=None)
        if not scopes[site]:
            state['results'][site] = {'status': 'empty', 'complete': True}
            continue
        if page_requires_auth(session, site):
            publish(state, status='authentication_required')
            return
        payload = {
            'async': True, 'data_ids': scopes[site], 'resume': False, 'workers': 1,
            'browser': 'chrome', 'debugger_address': f'127.0.0.1:{PORTS[site]}',
            'headless': False, 'wait_seconds': 5, 'page_timeout': 25,
            'exclude_activity': True, 'skip_deal': True,
            'auto_compensate': True, 'compensation_rounds': 2,
        }
        response = session.post(base_url + '/api/amazon-day-data/crawl', json=payload, timeout=20)
        response.raise_for_status()  # Do not retry POST and accidentally submit twice.
        job_id = response.json()['job']['id']
        publish(state, status='running', current_job=job_id)
        job = wait_job(session, base_url, job_id, state['count_day'])
        result = job.get('result') or {}
        state['results'][site] = {
            'job_id': job_id, 'status': job['status'],
            'complete': result.get('complete', False),
            'remaining': result.get('remaining_re_pull'), 'stats': result.get('stats'),
        }
        # Respect user pause and challenge pauses; never jump ahead of them.
        if job['status'] != 'finished':
            publish(state, status='paused', reason=job['status'])
            return
        publish(state, status='site_finished')
    publish(state, status='finished', site=None, current_job=None,
            complete=all(r['complete'] for r in state['results'].values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--after-job', required=True)
    args = parser.parse_args()
    session = requests.Session()
    session.trust_env = False
    base_url = 'http://127.0.0.1:6471'
    state = {'pid': os.getpid(), 'count_day': date.today().isoformat(),
             'order': list(ORDER), 'after_job': args.after_job, 'results': {}}
    with crawl_lease(BASE / '.crawl_jobs' / 'site_sequence.lock'):
        publish(state, status='waiting_for_previous')
        try:
            previous = wait_job(session, base_url, args.after_job, state['count_day'])
            if previous['status'] != 'finished':
                publish(state, status='paused', reason='previous_' + previous['status'])
                return 1
            with connect(**database_config()) as connection:
                targets = fetch_targets(connection, exclude_activity=True)
            scopes = {site: [int(t['id']) for t in targets if t['country'] == site] for site in ORDER}
            publish(state, scopes=scopes)
            execute_sequence(session, base_url, scopes, state)
            return 0 if state['status'] == 'finished' else 1
        except Exception as exc:
            publish(state, status='failed', error=str(exc))
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
