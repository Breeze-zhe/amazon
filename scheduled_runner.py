"""Daily site isolation, durable authentication reminders and explicit same-day resume."""
from contextlib import ExitStack
from datetime import date, datetime
import json
import os
from pathlib import Path
from threading import Thread
import time

import requests

from .crawl_jobs import crawl_lease
from .crawl_policy import compensation_browser_options
from .site_sessions import SITE_SESSIONS, SITE_NAMES, debugger_address, page_status

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / '.crawl_jobs' / 'scheduled_state.json'
STOP = BASE / '.crawl_jobs' / 'scheduled_stop.json'
ORDER = ('M', 'C', 'G', 'E', 'I', 'F', 'S', 'J', 'A', 'U')
ACTIVE = {'queued', 'running', 'stopping'}


def read_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None


def public_state():
    state = read_state()
    if state and state.get('status') in {'running', 'preparing'}:
        try:
            with crawl_lease(BASE / '.crawl_jobs' / 'scheduled_runner.lock'):
                # A dead orchestrator is not an actively running batch. Keep
                # the durable job id and auth reminders for manual recovery.
                state = {**state, 'status': 'interrupted', 'complete': False,
                         'error': '调度进程已退出；请检查当前任务后手动恢复，系统不会自动重启'}
        except Exception:
            pass
    return state


def save_state(state):
    state['updated'] = datetime.now().astimezone().isoformat()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(STATE)


def stopped(state):
    try:
        return json.loads(STOP.read_text(encoding='utf-8')).get('run_id') == state['run_id']
    except FileNotFoundError:
        return False


def request_stop(state):
    STOP.parent.mkdir(parents=True, exist_ok=True)
    temporary = STOP.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'run_id': state['run_id']}), encoding='utf-8')
    temporary.replace(STOP)


def acquire_locks():
    stack = ExitStack()
    try:
        stack.enter_context(crawl_lease(BASE / '.crawl_jobs' / 'scheduled_runner.lock'))
        stack.enter_context(crawl_lease(BASE / '.crawl_jobs' / 'site_sequence.lock'))
        return stack
    except BaseException:
        stack.close()
        raise


def load_scopes():
    from web_app import connect, database_config, fetch_targets
    with connect(**database_config()) as connection:
        targets = fetch_targets(connection, exclude_activity=True, exclude_broken_links=True)
    unsupported = {t['country'] for t in targets} - set(SITE_SESSIONS)
    if unsupported:
        raise ValueError(f'Unsupported marketplaces: {sorted(unsupported)}')
    return {site: [int(t['id']) for t in targets if t['country'] == site] for site in ORDER}


def ensure_same_day(state):
    if state['count_day'] != date.today().isoformat():
        raise ValueError('日期已变更，请创建今天的新任务，不补写昨天的数据')


def validate_resume(state):
    if not state:
        raise ValueError('没有待认证的定时批次')
    ensure_same_day(state)
    if state['status'] != 'waiting_auth' or not state.get('waiting_auth'):
        raise ValueError('请等待当前批次结束；手动暂停的批次不会自动恢复')


def payload_for(site, ids, day, browser='chrome', *, resume=True,
                exclude_activity=True, skip_broken_links=True):
    options = dict(browser=browser, data_ids=ids, count_day=day,
                   compensation_browser=browser)
    if browser == 'chrome':
        options['debugger_address'] = debugger_address(site)
    payload = compensation_browser_options(options, browser=browser)
    payload.update(skip_deal=True, exclude_activity=exclude_activity, resume=resume,
                   auto_compensate=True, compensation_rounds=2,
                   pause_on_auth=True, skip_broken_links=skip_broken_links, **{'async': True})
    return payload


def get_job(session, base_url, job_id):
    response = session.get(base_url + '/jobs/' + job_id, timeout=20)
    response.raise_for_status()
    value = response.json()
    if not value.get('success') or not value.get('job'):
        raise ValueError('无法确认后台任务状态，不会重复提交')
    return value['job']


def wait_job(session, base_url, job_id, state, deadline):
    while True:
        job = get_job(session, base_url, job_id)
        if job['status'] not in ACTIVE:
            return job
        if stopped(state) or state['count_day'] != date.today().isoformat():
            response = session.post(base_url + '/jobs/' + job_id + '/stop', timeout=20)
            response.raise_for_status()
        if time.monotonic() >= deadline:
            raise TimeoutError('监控超时，原任务可能仍在运行；未提交其他站点')
        time.sleep(5)


def execute(session, base_url, state, sites, log, deadline):
    state.setdefault('waiting_auth_browser', {})
    for site in sites:
        ensure_same_day(state)
        if stopped(state):
            state.update(status='paused', current_site=None, current_job=None)
            save_state(state)
            return 1
        if time.monotonic() >= deadline:
            raise TimeoutError('批次时间预算已用完，未启动下一站')
        ids = state['scopes'][site]
        payload_options = dict(
            resume=not bool(state.get('force_refresh', False)),
            exclude_activity=bool(state.get('exclude_activity', True)),
            skip_broken_links=bool(state.get('skip_broken_links', True)),
        )
        state.update(status='running', current_site=site, current_job=None)
        save_state(state)
        if not ids:
            state['results'][site] = dict(status='empty', complete=True)
            continue
        browser = state['waiting_auth_browser'].get(site, 'chrome')
        try:
            auth = browser == 'chrome' and page_status(session, site) == 'authentication_required'
        except Exception as exc:
            auth = False
            if browser == 'chrome':
                browser = 'edge'
                log(f'CHROME_PREFLIGHT_ERROR site={site} name={SITE_NAMES[site]} error={exc}; 本站改用 Edge')
            else:
                state['results'][site] = dict(status='browser_error', complete=False, error=str(exc))
                save_state(state)
                log(f'site={site} browser_error={exc}')
                continue
        if stopped(state):
            state.update(status='paused')
            save_state(state)
            return 1
        if auth:
            browser = 'edge'
            log(f'AUTH_REQUIRED_IN_CHROME site={site} name={SITE_NAMES[site]}，本次站点改用 Edge')
        if stopped(state):
            state.update(status='paused')
            save_state(state)
            return 1
        ensure_same_day(state)
        # Never retry a POST: a network timeout may have accepted a real job.
        response = session.post(base_url + '/crawl',
                                json=payload_for(site, ids, state['count_day'], browser,
                                                 **payload_options), timeout=20)
        response.raise_for_status()
        body = response.json()
        if not body.get('success') or not body.get('job', {}).get('id'):
            raise ValueError('提交结果不明确，停止批次以避免重复抓取')
        job_id = body['job']['id']
        state['current_job'] = job_id
        save_state(state)
        job = wait_job(session, base_url, job_id, state, deadline)
        result = job.get('result') or {}
        if (browser == 'chrome' and job['status'] == 'paused'
                and job.get('pause_reason') == 'authentication_required'):
            if stopped(state):
                state.update(status='paused', current_site=site, current_job=None)
                save_state(state)
                return 1
            log(f'AUTH_REQUIRED_IN_CHROME site={site} name={SITE_NAMES[site]}，未完成商品改用 Edge 补拉')
            edge_response = session.post(
                base_url + '/crawl',
                json=payload_for(site, ids, state['count_day'], browser='edge',
                                 **payload_options), timeout=20)
            edge_response.raise_for_status()
            edge_body = edge_response.json()
            if not edge_body.get('success') or not edge_body.get('job', {}).get('id'):
                raise ValueError('Edge 补拉任务提交结果不明确，停止批次避免重复提交')
            job_id = edge_body['job']['id']
            state['current_job'] = job_id
            save_state(state)
            job = wait_job(session, base_url, job_id, state, deadline)
            result = job.get('result') or {}
            browser = 'edge'
        if stopped(state) or (job['status'] != 'finished' and not (
                job['status'] == 'paused' and job.get('pause_reason') == 'authentication_required')):
            state.update(status='paused')
            save_state(state)
            log(f'paused site={site} job={job_id} status={job["status"]}')
            return 1
        auth = job['status'] == 'paused'
        state['results'][site] = dict(job_id=job_id, status='authentication_required' if auth else job['status'],
                                      complete=not auth and bool(result.get('complete')),
                                      remaining=result.get('remaining_re_pull'), stats=result.get('stats'))
        if auth:
            if site not in state['waiting_auth']:
                state['waiting_auth'].append(site)
            state['waiting_auth_browser'][site] = browser
            log(f'AUTH_REQUIRED site={site} name={SITE_NAMES[site]} browser={browser} job={job_id} 请人工认证后补拉')
        elif site in state['waiting_auth']:
            state['waiting_auth'].remove(site)
            state['waiting_auth_browser'].pop(site, None)
        state['results'][site]['browser'] = browser
        save_state(state)
    if stopped(state):
        state.update(status='paused')
        save_state(state)
        return 1
    state.update(status='waiting_auth' if state['waiting_auth'] else 'finished',
                 current_site=None, current_job=None,
                 complete=all(r.get('complete', False) for r in state['results'].values()))
    save_state(state)
    unknown = any(not r.get('complete') and r.get('remaining') is None for r in state['results'].values())
    remaining = 'unknown' if unknown else sum(r.get('remaining') or 0 for r in state['results'].values())
    log(f'finish complete={state["complete"]} waiting_auth={state["waiting_auth"]} remaining_re_pull={remaining}')
    return 0 if state['complete'] else 1


def run(log, api_url, timeout_seconds, resume_auth=False):
    session = requests.Session()
    session.trust_env = False
    state = None
    owns_state = False
    try:
        state = read_state()
        if resume_auth:
            validate_resume(state)
            sites = list(state['waiting_auth'])
        else:
            if state and state['count_day'] == date.today().isoformat():
                log('same_day_batch_exists: 不自动重启已暂停或已完成的批次')
                return 0 if state.get('complete') else 1
            scopes = load_scopes()
            state = dict(run_id=datetime.now().astimezone().isoformat(), pid=os.getpid(),
                         count_day=date.today().isoformat(), status='preparing', order=list(ORDER),
                         scopes=scopes, results={}, waiting_auth=[], complete=False)
            sites = list(ORDER)
        latest = session.get(api_url.rsplit('/', 1)[0] + '/jobs/latest', timeout=20)
        latest.raise_for_status()
        if (latest.json().get('job') or {}).get('status') in ACTIVE:
            log('已有抓取正在运行，本批次未启动')
            return 1
        state['run_id'] = datetime.now().astimezone().isoformat()
        state['pid'] = os.getpid()
        state.pop('error', None)
        state['complete'] = False
        owns_state = True
        return execute(session, api_url.rsplit('/', 1)[0], state, sites, log, time.monotonic() + timeout_seconds)
    except Exception as exc:
        if state and owns_state:
            state.update(status='failed', complete=False, error=str(exc))
            save_state(state)
        log(f'failed error={exc}')
        return 1
    finally:
        session.close()


def launch_resume(log, api_url, timeout_seconds):
    locks = acquire_locks()
    try:
        validate_resume(read_state())
        def worker():
            try:
                run(log, api_url, timeout_seconds, resume_auth=True)
            finally:
                locks.close()
        Thread(target=worker, name='scheduled-auth-resume', daemon=True).start()
    except BaseException:
        locks.close()
        raise
