from contextlib import ExitStack
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

import requests
import scheduled_crawl
import web_app
from amazon_importer import day_crawler as crawler
from amazon_importer import scheduled_runner as r
from amazon_importer import site_sessions as browsers
from amazon_importer.crawl_jobs import CrawlBusy


def state():
    return dict(run_id='test', count_day=date.today().isoformat(), status='preparing',
                scopes={'J': [174], 'A': [158], 'U': [101]}, results={}, waiting_auth=[], complete=False)


def job(status='finished', complete=True, **values):
    return dict(id='job', status=status, result=dict(complete=complete, remaining_re_pull=0 if complete else 1), **values)


class ScheduledAuthTests(unittest.TestCase):
    def execute(self, auth, jobs, initial=None, stopped=False):
        batch = initial or state()
        session, log = Mock(), Mock()
        session.post.return_value.json.return_value = dict(success=True, job=dict(id='job'))
        with patch.object(r, 'save_state'), patch.object(r, 'stopped', return_value=stopped), \
             patch.object(r, 'page_status', side_effect=auth) as check, \
             patch.object(r, 'wait_job', side_effect=jobs) as wait:
            code = r.execute(session, 'http://local/api', batch, ['J', 'A', 'U'], log, time.monotonic()+100)
        return batch, session, code, log, check, wait

    def test_preflight_auth_switches_only_that_site_to_edge(self):
        batch, session, code, log, _, _ = self.execute(
            ['authentication_required', 'ready', 'ready'], [job(), job(), job()])
        payloads = [c.kwargs['json'] for c in session.post.call_args_list]
        self.assertEqual([p['data_ids'] for p in payloads], [[174], [158], [101]])
        self.assertEqual([p['browser'] for p in payloads], ['edge', 'chrome', 'chrome'])
        self.assertNotIn('debugger_address', payloads[0])
        self.assertEqual(payloads[1]['debugger_address'], '127.0.0.1:9231')
        self.assertEqual(batch['waiting_auth'], [])
        self.assertEqual(batch['status'], 'finished')
        self.assertTrue(batch['complete'])
        self.assertEqual(code, 0)
        self.assertTrue(any('AUTH_REQUIRED_IN_CHROME' in c.args[0] for c in log.call_args_list))

    def test_mid_crawl_auth_pauses_only_current_site(self):
        batch, session, code, _, _, _ = self.execute(['ready']*3, [
            job('paused', False, pause_reason='authentication_required'), job(), job(), job()])
        payloads = [c.kwargs['json'] for c in session.post.call_args_list]
        self.assertEqual([p['browser'] for p in payloads], ['chrome', 'edge', 'chrome', 'chrome'])
        self.assertEqual([p['data_ids'] for p in payloads[:2]], [[174], [174]])
        self.assertTrue(payloads[1]['resume'])
        self.assertNotIn('debugger_address', payloads[1])
        self.assertEqual(session.post.call_count, 4)
        self.assertEqual(batch['waiting_auth'], [])
        self.assertTrue(batch['results']['J']['complete'])
        self.assertTrue(batch['results']['U']['complete'])
        self.assertEqual(code, 0)

    def test_force_refresh_and_activity_scope_apply_to_chrome_and_edge(self):
        batch = state()
        batch.update(force_refresh=True, exclude_activity=False)
        session = Mock()
        session.post.return_value.json.return_value = dict(success=True, job=dict(id='job'))
        with patch.object(r, 'save_state'), patch.object(r, 'stopped', return_value=False), \
             patch.object(r, 'page_status', return_value='ready'), \
             patch.object(r, 'wait_job', side_effect=[
                 job('paused', False, pause_reason='authentication_required'), job(), job(), job()]):
            r.execute(session, 'http://local/api', batch, ['J', 'A', 'U'], Mock(), time.monotonic()+100)
        payloads = [c.kwargs['json'] for c in session.post.call_args_list]
        self.assertEqual([p['browser'] for p in payloads], ['chrome', 'edge', 'chrome', 'chrome'])
        for payload in payloads:
            self.assertFalse(payload['resume'])
            self.assertFalse(payload['exclude_activity'])
            self.assertTrue(payload['skip_deal'])

    def test_edge_auth_pauses_site_and_remembers_edge_for_manual_resume(self):
        batch, session, code, _, _, _ = self.execute(['ready']*3, [
            job('paused', False, pause_reason='authentication_required'),
            job('paused', False, pause_reason='authentication_required'), job(), job()])
        self.assertEqual([c.kwargs['json']['browser'] for c in session.post.call_args_list],
                         ['chrome', 'edge', 'chrome', 'chrome'])
        self.assertEqual(batch['waiting_auth'], ['J'])
        self.assertEqual(batch['waiting_auth_browser'], {'J': 'edge'})
        self.assertEqual(batch['status'], 'waiting_auth')
        self.assertTrue(batch['results']['A']['complete'])
        self.assertTrue(batch['results']['U']['complete'])
        self.assertEqual(code, 1)

    def test_manual_auth_resume_keeps_site_on_edge(self):
        batch = state()
        batch.update(status='waiting_auth', waiting_auth=['J'],
                     waiting_auth_browser={'J': 'edge'})
        session = Mock()
        session.post.return_value.json.return_value = dict(success=True, job=dict(id='edge-job'))
        with patch.object(r, 'save_state'), patch.object(r, 'stopped', return_value=False), \
             patch.object(r, 'page_status', side_effect=AssertionError('must not inspect Chrome')), \
             patch.object(r, 'wait_job', return_value=job()):
            code = r.execute(session, 'http://local/api', batch, ['J'], Mock(), time.monotonic()+100)
        self.assertEqual(code, 0)
        self.assertEqual(session.post.call_args.kwargs['json']['browser'], 'edge')
        self.assertEqual(batch['waiting_auth'], [])
        self.assertNotIn('J', batch['waiting_auth_browser'])

    def test_manual_stop_never_continues_other_sites(self):
        batch, session, _, _, _, _ = self.execute(['ready'], [job('paused', False, pause_reason='user_requested')])
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(batch['status'], 'paused')

    def test_batch_stop_before_site_never_opens_browser(self):
        batch, session, _, _, check, _ = self.execute([], [], stopped=True)
        session.post.assert_not_called()
        check.assert_not_called()
        self.assertEqual(batch['status'], 'paused')

    def test_unknown_or_failed_job_never_starts_next_site(self):
        for status in ('failed', 'interrupted'):
            with self.subTest(status=status):
                batch, session, _, _, _, _ = self.execute(['ready'], [job(status, False)])
                self.assertEqual(session.post.call_count, 1)
                self.assertEqual(batch['status'], 'paused')

    def test_post_timeout_is_never_retried(self):
        session = Mock()
        session.post.side_effect = requests.Timeout('ambiguous submission')
        with patch.object(r, 'save_state'), patch.object(r, 'stopped', return_value=False), \
             patch.object(r, 'page_status', return_value='ready'):
            with self.assertRaises(requests.Timeout):
                r.execute(session, 'http://local/api', state(), ['J','A'], Mock(), time.monotonic()+100)
        self.assertEqual(session.post.call_count, 1)

    def test_missing_data_does_not_report_complete(self):
        batch, _, code, log, _, _ = self.execute(['ready']*3, [job(complete=False), job(), job()])
        self.assertEqual(batch['status'], 'finished')
        self.assertFalse(batch['complete'])
        self.assertEqual(code, 1)
        self.assertIn('complete=False', log.call_args.args[0])
        self.assertIn('remaining_re_pull=1', log.call_args.args[0])

    def test_chrome_preflight_error_falls_back_to_edge_and_other_sites_run(self):
        batch, session, code, log, _, _ = self.execute(
            [RuntimeError('wrong marketplace tab'), 'ready', 'ready'], [job(), job(), job()])
        self.assertEqual([c.kwargs['json']['browser'] for c in session.post.call_args_list],
                         ['edge', 'chrome', 'chrome'])
        self.assertEqual(batch['results']['J']['browser'], 'edge')
        self.assertTrue(batch['results']['J']['complete'])
        self.assertEqual(session.post.call_count, 3)
        self.assertEqual(code, 0)
        self.assertTrue(any('CHROME_PREFLIGHT_ERROR' in c.args[0] for c in log.call_args_list))

    def test_payload_retains_quality_and_only_resumes_incomplete(self):
        p = r.payload_for('J', [174], date.today().isoformat())
        for key in ('resume', 'pause_on_auth', 'exclude_activity', 'skip_deal', 'auto_compensate', 'selenium_only'):
            self.assertTrue(p[key], key)
        self.assertEqual((p['browser'], p['workers'], p['compensation_rounds']), ('chrome', 1, 2))
        self.assertEqual(p['debugger_address'], '127.0.0.1:9230')

    def test_payload_can_force_refresh_without_activity_filter_but_still_skip_deal(self):
        p = r.payload_for('J', [174], date.today().isoformat(), resume=False,
                          exclude_activity=False, skip_broken_links=True)
        self.assertFalse(p['resume'])
        self.assertFalse(p['exclude_activity'])
        self.assertTrue(p['skip_deal'])
        self.assertTrue(p['skip_broken_links'])

    def test_old_day_and_manual_pause_cannot_resume_auth(self):
        for status, day in [('paused', date.today()), ('waiting_auth', date.today()-timedelta(days=1))]:
            batch = state()
            batch.update(status=status, count_day=day.isoformat(), waiting_auth=['J'])
            with self.assertRaises(ValueError):
                r.validate_resume(batch)

    def test_resume_runs_only_waiting_sites_preserving_completed_results(self):
        batch = state()
        batch.update(status='waiting_auth', waiting_auth=['J'], results={'A': {'complete': True}})
        session = Mock()
        session.get.return_value.json.return_value = {'job': None}
        with patch.object(r, 'read_state', return_value=batch), patch.object(r.requests, 'Session', return_value=session), \
             patch.object(r, 'execute', return_value=1) as execute:
            self.assertEqual(r.run(Mock(), 'http://local/api/crawl', 50, resume_auth=True), 1)
        self.assertEqual(execute.call_args.args[3], ['J'])
        self.assertTrue(batch['results']['A']['complete'])

    def test_duplicate_daily_start_does_not_restart_paused_batch(self):
        batch = state()
        batch.update(status='paused')
        with patch.object(r, 'read_state', return_value=batch), patch.object(r, 'load_scopes') as load, \
             patch.object(r, 'execute') as execute:
            self.assertEqual(r.run(Mock(), 'http://local/api/crawl', 10), 1)
        execute.assert_not_called()
        load.assert_not_called()

    def test_state_and_stop_signal_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(r, 'STATE', Path(directory)/'state.json'), patch.object(r, 'STOP', Path(directory)/'stop.json'):
            batch = state()
            r.save_state(batch)
            self.assertEqual(r.read_state()['count_day'], batch['count_day'])
            self.assertFalse(r.stopped(batch))
            r.request_stop(batch)
            self.assertTrue(r.stopped(r.read_state()))
            self.assertFalse(r.stopped(dict(run_id='new')))

    def test_exclusive_lock_prevents_duplicate_orchestrator(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(r, 'BASE', Path(directory)):
            with r.acquire_locks():
                with self.assertRaises(CrawlBusy):
                    r.acquire_locks()

    def test_duplicate_resume_click_rejected_before_thread_launch(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(r, 'BASE', Path(directory)), \
             patch.object(r, 'Thread') as thread:
            with r.acquire_locks():
                with self.assertRaises(CrawlBusy):
                    r.launch_resume(Mock(), 'http://local/api/crawl', 10)
        thread.assert_not_called()

    def test_date_change_during_browser_check_does_not_submit(self):
        session = Mock()
        with patch.object(r, 'save_state'), patch.object(r, 'stopped', return_value=False), \
             patch.object(r, 'page_status', return_value='ready'), \
             patch.object(r, 'ensure_same_day', side_effect=[None, ValueError('date changed')]):
            with self.assertRaises(ValueError):
                r.execute(session, 'http://local/api', state(), ['J'], Mock(), time.monotonic()+100)
        session.post.assert_not_called()

    def test_preflight_auth_fallback_does_not_hide_incomplete_edge_result(self):
        batch, _, code, log, _, _ = self.execute(
            ['authentication_required', 'ready', 'ready'],
            [job(complete=False), job(), job()])
        self.assertFalse(batch['complete'])
        self.assertEqual(code, 1)
        self.assertIn('remaining_re_pull=1', log.call_args.args[0])

    def test_schedule_main_preserves_unsuccessful_result(self):
        with patch.object(r, 'acquire_locks', return_value=ExitStack()), patch.object(r, 'run', return_value=1):
            self.assertEqual(scheduled_crawl.main(), 1)


class ApiAuthTests(unittest.TestCase):
    def test_runtime_challenge_sets_reason_and_stops_before_compensation(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        event, progress = Event(), Mock()
        record = dict(data_id=1, country_code='J', attempts=[dict(success=False, message='blocked by Amazon captcha')])
        def crawl(*args, **kwargs):
            kwargs['crawl_kwargs']['progress_callback'](record)
            self.assertTrue(event.is_set())
            return dict(stats={}, items=[], remaining_re_pull=1)
        with patch.object(web_app, 'database_config', return_value={}), \
             patch.object(web_app, 'connect', return_value=connection), \
             patch.object(web_app, 'ensure_day_data_columns'), \
             patch.object(web_app, 'fetch_targets', return_value=[dict(id=1, country='J')]), \
             patch.object(web_app, 'fetch_repull_ids', return_value=[1]), \
             patch.object(web_app, 'crawl_with_compensation', side_effect=crawl) as run:
            result = web_app.run_optimized_crawl(dict(pause_on_auth=True), 'crawl', progress, event)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(result['complete'])
        self.assertTrue(any(c.kwargs.get('pause_reason') == 'authentication_required' for c in progress.call_args_list))

    def test_postal_setup_detects_captcha_before_changing_location(self):
        driver = Mock(page_source='<input id="captchacharacters">')
        with patch.object(crawler.time, 'sleep'), patch.object(crawler, '_dismiss_cookie_consent') as dismiss:
            with self.assertRaisesRegex(RuntimeError, 'captcha'):
                crawler.set_delivery_location_selenium(driver, 'J', 'ja-JP')
        dismiss.assert_not_called()

    def test_resume_rejects_active_crawl(self):
        with patch.object(web_app.crawl_jobs, 'get', return_value=dict(status='running')), \
             patch.object(r, 'launch_resume') as resume:
            response = web_app.app.test_client().post('/api/amazon-day-data/scheduled/resume-auth')
        self.assertEqual(response.status_code, 409)
        resume.assert_not_called()

    def test_old_day_submission_rejected_before_job_creation(self):
        with patch.object(web_app.crawl_jobs, 'submit') as submit:
            response = web_app.app.test_client().post('/api/amazon-day-data/crawl', json={
                'async': True, 'count_day': (date.today()-timedelta(days=1)).isoformat()})
        self.assertEqual(response.status_code, 400)
        submit.assert_not_called()

    def test_reminder_is_available_even_when_latest_job_is_another_site(self):
        batch = state()
        batch.update(waiting_auth=['J'])
        with patch.object(r, 'read_state', return_value=batch):
            response = web_app.app.test_client().get('/api/amazon-day-data/scheduled')
        self.assertEqual(response.json['batch']['waiting_auth'], ['J'])
        self.assertEqual(response.json['site_names']['J'], '日本')

    def test_resume_button_launches_auth_only_runner(self):
        with patch.object(web_app.crawl_jobs, 'get', return_value=dict(status='finished')), \
             patch.object(r, 'launch_resume') as resume:
            response = web_app.app.test_client().post('/api/amazon-day-data/scheduled/resume-auth')
        self.assertEqual(response.status_code, 202)
        resume.assert_called_once()


class BrowserTests(unittest.TestCase):
    def test_image_only_missing_page_is_ready_for_next_product(self):
        for site in browsers.SITE_SESSIONS:
            with self.subTest(site=site):
                session, ws = Mock(), Mock()
                url = 'https://' + browsers.get_site_domain(site) + '/dp/B0EXAMPLE01'
                session.get.return_value.json.return_value = [dict(
                    type='page', url=url, webSocketDebuggerUrl='ws://localhost/tab')]
                ws.recv.return_value = json.dumps(dict(id=1, result=dict(result=dict(value=dict(
                    html='<title>Page Not Found</title><body><img src="error.png"></body>',
                    url=url, ready='complete', text='')))))
                with patch.object(browsers.websocket, 'create_connection', return_value=ws):
                    self.assertEqual(browsers.page_status(session, site), 'ready')
                ws.close.assert_called_once()

    def test_modern_human_confirmation_not_product_copy(self):
        self.assertTrue(crawler.is_blocked_html('<h1>Click the button below to continue shopping</h1><button>Continue shopping</button>'))
        self.assertFalse(crawler.is_blocked_html('<div id="productTitle">Click the button below to continue shopping</div>'))

    def test_wrong_marketplace_tab_rejected_before_cdp(self):
        session = Mock()
        session.get.return_value.json.return_value = [dict(type='page', url='https://www.amazon.com/')]
        with patch.object(browsers.websocket, 'create_connection') as connect:
            with self.assertRaisesRegex(RuntimeError, 'matching marketplace'):
                browsers.page_status(session, 'J')
        connect.assert_not_called()

    def test_challenge_is_read_only_no_navigation_or_solving(self):
        session, ws = Mock(), Mock()
        session.get.return_value.json.return_value = [dict(type='page', url='https://www.amazon.co.jp/', webSocketDebuggerUrl='ws://localhost/tab')]
        ws.recv.return_value = json.dumps(dict(id=1, result=dict(result=dict(value=dict(
            html='<input id="captchacharacters">', url='https://www.amazon.co.jp/', ready='complete', text='verify')))))
        with patch.object(browsers.websocket, 'create_connection', return_value=ws):
            self.assertEqual(browsers.page_status(session, 'J'), 'authentication_required')
        self.assertEqual(json.loads(ws.send.call_args.args[0])['method'], 'Runtime.evaluate')
        self.assertNotIn('click', json.loads(ws.send.call_args.args[0])['params']['expression'])
        ws.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
