import json
import os
from pathlib import Path
import tempfile
from threading import Barrier, Event, Lock, Thread
import unittest
from unittest.mock import MagicMock, Mock, patch

import web_app
from amazon_importer import day_crawler as crawler
from amazon_importer.crawl_jobs import CrawlBusy, CrawlJobs, crawl_lease
from amazon_importer.crawl_policy import compensation_browser_options


def target(data_id, country="G"):
    return dict(id=data_id, country=country, asin=f"B{data_id:09d}", asin_country=f"{data_id}-{country}", name="Test")


class LocalChromeDriverTests(unittest.TestCase):
    def test_site_debuggers_must_not_share_a_browser(self):
        with patch.dict(os.environ, {"AMAZON_SELENIUM_SITE_DEBUGGERS": json.dumps({"C": "127.0.0.1:9223", "M": "127.0.0.1:9223"})}):
            with self.assertRaisesRegex(ValueError, "distinct"):
                crawler.site_debugger_addresses()

    def test_site_debuggers_reject_nonlocal_hosts(self):
        with patch.dict(os.environ, {"AMAZON_SELENIUM_SITE_DEBUGGERS": json.dumps({"C": "example.com:9223"})}):
            with self.assertRaisesRegex(ValueError, "loopback"):
                crawler.site_debugger_addresses()

    def test_site_browser_is_selected_by_country(self):
        for country, port in (("C", 9223), ("M", 9224)):
            with (
                self.subTest(country=country),
                patch.dict(os.environ, {"AMAZON_SELENIUM_BROWSER": "chrome", "AMAZON_SELENIUM_DEBUGGER_ADDRESS": "", "AMAZON_SELENIUM_SITE_DEBUGGERS": json.dumps({"C": "127.0.0.1:9223", "M": "127.0.0.1:9224"})}),
                patch.object(crawler, "find_chrome_binary", return_value="chrome.exe"),
                patch.object(crawler, "find_chromedriver_binary", return_value="driver.exe"),
                patch.object(crawler, "executable_version", return_value="155.0.8059.84"),
                patch.object(crawler, "ChromeService"),
                patch.object(crawler.webdriver, "Chrome") as browser,
            ):
                crawler.create_selenium_driver("en", profile_key=country)
                self.assertEqual(browser.call_args.kwargs["options"].experimental_options["debuggerAddress"], f"127.0.0.1:{port}")

    def test_explicit_driver_must_exist_and_cannot_silently_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.exe")
            with patch.dict(os.environ, {"CHROMEDRIVER_PATH": missing}):
                with self.assertRaisesRegex(RuntimeError, "CHROMEDRIVER_PATH does not exist"):
                    crawler.find_chromedriver_binary()

    def test_explicit_driver_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chromedriver.exe"
            executable.touch()
            with patch.dict(os.environ, {"CHROMEDRIVER_PATH": str(executable)}):
                self.assertEqual(crawler.find_chromedriver_binary(), str(executable.resolve()))

    def test_project_driver_is_used_without_path_search(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chromedriver.exe"
            executable.touch()
            with (patch.dict(os.environ, {"CHROMEDRIVER_PATH": ""}),
                  patch.object(crawler, "LOCAL_CHROMEDRIVER_PATH", executable),
                  patch.object(crawler.shutil, "which") as search):
                self.assertEqual(crawler.find_chromedriver_binary(), str(executable))
                search.assert_not_called()

    def test_missing_driver_fails_fast_instead_of_network_download(self):
        with tempfile.TemporaryDirectory() as directory:
            with (patch.dict(os.environ, {"CHROMEDRIVER_PATH": ""}),
                  patch.object(crawler, "LOCAL_CHROMEDRIVER_PATH", Path(directory) / "missing.exe"),
                  patch.object(crawler.shutil, "which", return_value=None)):
                with self.assertRaisesRegex(RuntimeError, "Automatic driver downloads are disabled"):
                    crawler.find_chromedriver_binary()

    def test_matching_chrome_build_accepts_different_patch(self):
        crawler.validate_chrome_driver_versions("155.0.8059.85", "155.0.8059.84")

    def test_incompatible_build_is_rejected_before_browser_start(self):
        with self.assertRaisesRegex(RuntimeError, "incompatible"):
            crawler.validate_chrome_driver_versions("155.0.8059.85", "155.0.8000.1")

    def test_invalid_version_output_is_rejected(self):
        with patch.object(crawler.subprocess, "run", return_value=Mock(stdout="Unknown version")):
            with self.assertRaisesRegex(RuntimeError, "Unable to read executable version"):
                crawler.executable_version("driver.exe")

    def test_chrome_receives_explicit_service_and_keeps_attached_browser(self):
        with (
            patch.dict(os.environ, {"AMAZON_SELENIUM_BROWSER": "chrome", "AMAZON_SELENIUM_DEBUGGER_ADDRESS": "127.0.0.1:9223"}),
            patch.object(crawler, "find_chrome_binary", return_value="chrome.exe"),
            patch.object(crawler, "find_chromedriver_binary", return_value="local-driver.exe"),
            patch.object(crawler, "executable_version", side_effect=["155.0.8059.84", "155.0.8059.85"]),
            patch.object(crawler, "ChromeService") as service,
            patch.object(crawler.webdriver, "Chrome") as browser,
        ):
            driver, context = crawler.create_selenium_driver("en-CA")
            self.assertIsNone(context)
            self.assertTrue(driver._codex_attached)
            service.assert_called_once_with(executable_path="local-driver.exe")
            self.assertIs(browser.call_args.kwargs["service"], service.return_value)
            options = browser.call_args.kwargs["options"]
            self.assertEqual(options.experimental_options["debuggerAddress"], "127.0.0.1:9223")
            self.assertFalse(any("user-agent" in arg for arg in options.arguments))
            crawler.close_selenium_driver(driver, context)
            driver.quit.assert_not_called()


class OptimizedItemTests(unittest.TestCase):
    def test_mexico_canada_use_requested_default_postcodes(self):
        with patch.dict(os.environ, {"AMAZON_POSTAL_M": "", "AMAZON_POSTAL_C": ""}):
            self.assertEqual(crawler.get_postal_code("M"), "11000")
            self.assertEqual(crawler.get_postal_code("C"), "T4B 2T3")

    def test_postcode_environment_override_is_preserved(self):
        with patch.dict(os.environ, {"AMAZON_POSTAL_C": "  H2X 1Y4  "}):
            self.assertEqual(crawler.get_postal_code("C"), "H2X 1Y4")

    def test_delivery_header_rejects_previous_mexico_canada_postcodes(self):
        with patch.dict(os.environ, {"AMAZON_POSTAL_M": "", "AMAZON_POSTAL_C": ""}):
            for country, domain, old_postal, new_header in (
                ("M", "amazon.com.mx", "06600", "Enviar a 11000"),
                ("C", "amazon.ca", "M5V 2T6", "Deliver to T4B2T3\u200c"),
            ):
                with self.subTest(country=country):
                    driver = Mock(current_url=f"https://www.{domain}/")
                    driver.execute_script.return_value = old_postal
                    self.assertFalse(crawler._delivery_location_matches(driver, country))
                    driver.execute_script.return_value = new_header
                    self.assertTrue(crawler._delivery_location_matches(driver, country))

    def crawl(self, env, side_effect):
        state = {"country": "G", "postal_code": crawler.get_postal_code("G"), "currency_country": "G"}
        with (
            patch.dict(os.environ, {"AMAZON_ENABLE_SELENIUM": "1", "AMAZON_PRIMARY_ONLY": "1",
                                    "AMAZON_STOP_ON_BLOCK": "1", "AMAZON_SELENIUM_ONLY": "0", **env}),
            patch.object(crawler, "_delivery_location_matches", return_value=True),
            patch.object(crawler, "fetch_selenium_html", side_effect=side_effect) as browser,
            patch.object(crawler, "fetch_requests_html") as requests,
            patch.object(crawler, "fetch_curl_html") as curl,
            patch.object(crawler, "set_currency_preference_selenium") as currency,
            patch.object(crawler, "set_delivery_location_requests") as postal,
        ):
            metrics, attempts = crawler.crawl_item_metrics("G", "B000000001", selenium_driver=object(), selenium_location_state=state)
        return metrics, attempts, browser, requests, curl, currency, postal

    def test_primary_incomplete_makes_only_one_browser_visit(self):
        result, attempts, browser, requests, curl, _, postal = self.crawl({}, RuntimeError("Amazon response has no product details"))
        self.assertFalse(result.is_complete())
        self.assertEqual(len(attempts), 1)
        browser.assert_called_once()
        requests.assert_not_called()
        curl.assert_not_called()
        postal.assert_not_called()

    def test_captcha_stops_compensation_without_other_clients(self):
        _, attempts, browser, requests, curl, _, _ = self.crawl({"AMAZON_PRIMARY_ONLY": "0"}, RuntimeError("blocked by Amazon captcha or robot check"))
        self.assertEqual(len(attempts), 1)
        browser.assert_called_once()
        requests.assert_not_called()
        curl.assert_not_called()

    def test_wrong_postal_does_not_proceed_to_other_urls(self):
        _, attempts, browser, requests, curl, _, _ = self.crawl({"AMAZON_PRIMARY_ONLY": "0"}, RuntimeError("delivery postal verification failed"))
        self.assertEqual(len(attempts), 1)
        browser.assert_called_once()
        requests.assert_not_called()
        curl.assert_not_called()

    def test_verified_currency_state_is_reused(self):
        *_, currency, _ = self.crawl({}, RuntimeError("no product details"))
        currency.assert_not_called()

    def test_compensation_budget_caps_total_network_paths_at_three(self):
        state = {"country": "G", "postal_code": crawler.get_postal_code("G"), "currency_country": "G"}
        with (
            patch.dict(os.environ, {"AMAZON_ENABLE_SELENIUM": "1", "AMAZON_PRIMARY_ONLY": "0",
                                    "AMAZON_STOP_ON_BLOCK": "1", "AMAZON_SELENIUM_ONLY": "0", "AMAZON_SELENIUM_FAST": "0"}),
            patch.object(crawler, "_delivery_location_matches", return_value=True),
            patch.object(crawler, "fetch_selenium_html", side_effect=RuntimeError("no product details")),
            patch.object(crawler, "fetch_requests_html", side_effect=RuntimeError("network timeout")),
            patch.object(crawler, "fetch_curl_html") as curl,
        ):
            metrics, attempts = crawler.crawl_item_metrics("G", "B000000001", selenium_driver=object(), selenium_location_state=state)
        self.assertEqual(len(attempts), 3)
        self.assertFalse(metrics.is_complete())
        curl.assert_not_called()

    def test_asin_redirect_is_rejected(self):
        driver = Mock(current_url="https://www.amazon.de/dp/B000000002")
        with patch.object(crawler, "_wait_for_complete_product_html", return_value="<div id='productTitle'>X</div>"):
            with self.assertRaisesRegex(RuntimeError, "ASIN mismatch"):
                crawler.fetch_selenium_html("https://www.amazon.de/dp/B000000001", "de", driver=driver, country_code="G")

    def test_empty_selection_cannot_expand_to_all_targets(self):
        connection = Mock()
        self.assertEqual(crawler.fetch_targets(connection, data_ids=[]), [])
        connection.cursor.assert_not_called()

    def test_confirmed_absence_is_distinct_from_unknown_offer(self):
        self.assertTrue(crawler.CrawlMetric(buy_box=0, star="4", reviews="10", bsr="无").is_complete())
        self.assertFalse(crawler.CrawlMetric(star="4", reviews="10", bsr="无").is_complete())
        self.assertTrue(crawler.CrawlMetric(buy_box=0, star="4", reviews="0", bsr="10").is_complete())


class CrawlRunnerTests(unittest.TestCase):
    def test_dedicated_site_browsers_allow_parallel_workers(self):
        with patch.dict(os.environ, {"AMAZON_SELENIUM_SITE_DEBUGGERS": json.dumps({"G": "127.0.0.1:9223", "F": "127.0.0.1:9224"})}):
            self.test_country_workers_use_independent_connections_and_overlap()

    def test_resume_only_skips_valid_completed_ids(self):
        with (
            patch.dict(os.environ, {"AMAZON_ENABLE_SELENIUM": "0"}),
            patch.object(crawler, "ensure_re_pull_column"),
            patch.object(crawler, "fetch_targets", return_value=[target(1), target(2)]),
            patch.object(crawler, "fetch_completed_day_ids", return_value={1}),
            patch.object(crawler, "crawl_item_metrics", return_value=(crawler.CrawlMetric(), [])) as fetch,
            patch.object(crawler, "upsert_day_record", return_value="updated"),
            patch.object(crawler, "write_crawl_diagnostic"),
        ):
            result = crawler.crawl_amazon_day_data(Mock(), resume=True)
        self.assertEqual(result["stats"]["resumed"], 1)
        self.assertEqual([item["data_id"] for item in result["items"]], [2])
        self.assertEqual(fetch.call_count, 1)

    def test_stopped_job_never_starts_browser_or_writes(self):
        stopped = Event()
        stopped.set()
        with (
            patch.dict(os.environ, {"AMAZON_ENABLE_SELENIUM": "1"}),
            patch.object(crawler, "ensure_re_pull_column"),
            patch.object(crawler, "fetch_targets", return_value=[target(1)]),
            patch.object(crawler, "create_selenium_driver") as browser,
            patch.object(crawler, "upsert_day_record") as write,
        ):
            result = crawler.crawl_amazon_day_data(Mock(), stop_event=stopped)
        browser.assert_not_called()
        write.assert_not_called()
        self.assertEqual(result["items"], [])

    def test_country_workers_use_independent_connections_and_overlap(self):
        targets = [target(1), target(2, "F")]
        connections = []
        barrier = Barrier(2)
        def factory():
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connections.append(connection)
            return connection
        def fetch(connection, **kwargs):
            return [row for row in targets if kwargs.get("data_ids") is None or row["id"] in kwargs["data_ids"]]
        def crawl(*args, **kwargs):
            barrier.wait(timeout=5)
            return crawler.CrawlMetric(buy_box=0, star="4", reviews="10", bsr="10"), []
        with (
            patch.dict(os.environ, {"AMAZON_ENABLE_SELENIUM": "0", "AMAZON_SELENIUM_DEBUGGER_ADDRESS": ""}),
            patch.object(crawler, "ensure_re_pull_column"),
            patch.object(crawler, "fetch_targets", side_effect=fetch),
            patch.object(crawler, "crawl_item_metrics", side_effect=crawl),
            patch.object(crawler, "upsert_day_record", return_value="updated") as write,
            patch.object(crawler, "write_crawl_diagnostic"),
        ):
            result = crawler.crawl_amazon_day_data(Mock(), workers=2, connection_factory=factory)
        self.assertEqual(result["stats"]["updated"], 2)
        self.assertEqual(len(connections), 2)
        self.assertIsNot(connections[0], connections[1])
        self.assertEqual({id(call.args[0]) for call in write.call_args_list}, {id(c) for c in connections})

    def test_missing_rows_and_same_asin_across_countries_remain_pending_by_id(self):
        with patch.object(web_app, "fetch_completed_day_ids", return_value={2}):
            self.assertEqual(web_app.fetch_repull_ids(Mock(), "2026-10-20", [1, 2, 3]), [1, 3])


class JobTests(unittest.TestCase):
    def test_cross_instance_lease_prevents_duplicate_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            with crawl_lease(Path(directory) / "crawl.lock"):
                with self.assertRaises(CrawlBusy):
                    with crawl_lease(Path(directory) / "crawl.lock"):
                        self.fail("second lease acquired")

    def test_progress_is_atomic_and_stop_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = CrawlJobs(directory)
            started, finished = Event(), Event()
            def execute(payload, kind, progress, stop):
                progress(phase="primary", total=40, scope_ids=[1])
                def send():
                    for i in range(20):
                        progress({"data_id": i})
                threads = [Thread(target=send), Thread(target=send)]
                for thread in threads: thread.start()
                for thread in threads: thread.join()
                started.set()
                stop.wait(5)
                finished.set()
                return {"complete": False}
            job = jobs.submit({}, "crawl", execute)
            self.assertTrue(started.wait(5))
            self.assertEqual(jobs.get(job["id"])["processed"], 40)
            with self.assertRaises(CrawlBusy):
                CrawlJobs(directory).submit({}, "crawl", execute)
            jobs.stop(job["id"])
            self.assertTrue(finished.wait(5))
            for _ in range(100):
                state = jobs.get(job["id"])
                if state["status"] == "paused": break
                Event().wait(.01)
            self.assertEqual(state["status"], "paused")
            # Lease release happens just after the paused state is saved.
            for _ in range(100):
                try:
                    with crawl_lease(jobs.lease_path): break
                except CrawlBusy: Event().wait(.01)
            self.assertEqual(CrawlJobs(directory).get(job["id"])["status"], "paused")

    def test_orphaned_job_is_reported_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = CrawlJobs(directory)
            with jobs.db() as db:
                db.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?)",
                           ("dead", "running", "{}", json.dumps({"id": "dead", "status": "running", "processed": 1}), "now"))
            self.assertEqual(CrawlJobs(directory).get("dead")["status"], "interrupted")


class ApiTests(unittest.TestCase):
    def test_compensation_browser_policy_is_shared_and_canonical(self):
        options = compensation_browser_options({
            "browser": "edge", "headless": True, "workers": 2,
            "debugger_address": "127.0.0.1:9223",
            "profile_dir": "edge-profile",
        })
        self.assertEqual(
            {key: options[key] for key in (
                "use_selenium", "browser", "headless", "wait_seconds",
                "page_timeout", "selenium_only", "workers",
            )},
            {"use_selenium": True, "browser": "chrome", "headless": False,
             "wait_seconds": 5, "page_timeout": 25, "selenium_only": True,
             "workers": 1},
        )
        self.assertNotIn("debugger_address", options)
        self.assertNotIn("profile_dir", options)
        chrome = compensation_browser_options({
            "browser": "chrome", "debugger_address": "127.0.0.1:9232",
        })
        self.assertEqual(chrome["debugger_address"], "127.0.0.1:9232")
        edge = compensation_browser_options(
            {"browser": "chrome", "debugger_address": "127.0.0.1:9232"}, browser="edge")
        self.assertEqual(edge["browser"], "edge")
        self.assertNotIn("debugger_address", edge)

    def test_site_debugger_request_and_restore(self):
        with patch.dict(os.environ, {"AMAZON_SELENIUM_SITE_DEBUGGERS": ""}):
            original = web_app.apply_crawl_env({"browser": "chrome", "debugger_addresses": {"C": "127.0.0.1:9223", "M": "127.0.0.1:9224"}})
            try:
                self.assertEqual(crawler.site_debugger_addresses(), {"C": "127.0.0.1:9223", "M": "127.0.0.1:9224"})
                self.assertEqual(os.environ["AMAZON_SELENIUM_DEBUGGER_ADDRESS"], "")
            finally:
                web_app.restore_env(original)
            self.assertEqual(os.environ["AMAZON_SELENIUM_SITE_DEBUGGERS"], "")

    def test_failed_forced_refresh_is_not_hidden_by_old_valid_daily_rows(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with (
            patch.object(web_app, "database_config", return_value={}),
            patch.object(web_app, "connect", return_value=connection),
            patch.object(web_app, "ensure_day_data_columns"),
            patch.object(web_app, "fetch_targets", return_value=[target(1)]),
            patch.object(web_app, "fetch_repull_ids", return_value=[]),
            patch.object(web_app, "crawl_with_compensation", return_value={
                "stats": {}, "items": [{"data_id": 1, "db_action": "failed"}], "remaining_re_pull": 1,
            }),
        ):
            result = web_app.run_optimized_crawl({"resume": False}, "crawl")
        self.assertFalse(result["complete"])
        self.assertEqual(result["stats"]["complete_targets"], 0)
        self.assertEqual(result["stats"]["failed_targets"], 1)

    def test_background_first_pass_is_published_before_bounded_compensation(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        event = Mock()
        event.is_set.return_value = False
        event.wait.return_value = False
        progress = Mock()
        modes = []
        def run(*args, **kwargs):
            modes.append((os.environ["AMAZON_PRIMARY_ONLY"], os.environ["AMAZON_SKIP_DEAL"],
                          os.environ.get("AMAZON_SELENIUM_BROWSER"), kwargs))
            return {"stats": {"updated": 1}, "items": [], "remaining_re_pull": 1, "complete": False}
        with (
            patch.object(web_app, "database_config", return_value={}),
            patch.object(web_app, "connect", return_value=connection),
            patch.object(web_app, "ensure_day_data_columns"),
            patch.object(web_app, "fetch_targets", return_value=[target(1), target(2)]),
            patch.object(web_app, "fetch_repull_ids", side_effect=[[1, 2], [2], [2], [2], [2], []]),
            patch.object(web_app, "crawl_with_compensation", side_effect=run),
        ):
            result = web_app.run_optimized_crawl(
                {"skip_deal": False, "workers": 2}, "crawl", progress, event)
        self.assertEqual([mode[:2] for mode in modes],
                         [("1", "1"), ("0", "1"), ("0", "1"), ("0", "1")])
        self.assertEqual([mode[2] for mode in modes], ["chrome", "chrome", "chrome", "edge"])
        self.assertTrue(all(mode[3]["max_rounds"] == 0 for mode in modes))
        self.assertTrue(any("first_result" in call.kwargs for call in progress.call_args_list))
        self.assertEqual(modes[1][3]["crawl_kwargs"]["data_ids"], [2])
        self.assertEqual(modes[1][3]["crawl_kwargs"]["workers"], 1)
        self.assertEqual(modes[3][3]["crawl_kwargs"]["data_ids"], [2])
        self.assertEqual(modes[3][3]["crawl_kwargs"]["workers"], 1)
        self.assertEqual(result["stats"]["unique_targets"], 2)
        self.assertEqual(result["stats"]["complete_targets"], 2)
        self.assertTrue(result["complete"])
        connection.autocommit.assert_called_once_with(True)

    def test_explicit_compensate_job_forces_shared_chrome_policy(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        seen = []
        def run(*args, **kwargs):
            seen.append((os.environ.get("AMAZON_SELENIUM_BROWSER"),
                         os.environ.get("AMAZON_SELENIUM_ONLY"),
                         kwargs["crawl_kwargs"]["workers"],
                         kwargs["crawl_kwargs"]["fill_missing_only"]))
            return {"stats": {}, "items": [], "remaining_re_pull": 0, "complete": True}
        with (
            patch.object(web_app, "database_config", return_value={}),
            patch.object(web_app, "connect", return_value=connection),
            patch.object(web_app, "ensure_day_data_columns"),
            patch.object(web_app, "fetch_targets", return_value=[target(1)]),
            patch.object(web_app, "fetch_repull_ids", side_effect=[[1], [1], [], [1], []]),
            patch.object(web_app, "crawl_with_compensation", side_effect=run),
        ):
            result = web_app.run_optimized_crawl(
                {"browser": "edge", "workers": 2}, "compensate")
        self.assertTrue(result["complete"])
        self.assertEqual(seen, [("chrome", "1", 1, True), ("edge", "1", 1, True)])

    def test_sync_request_finishes_after_the_first_pass(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with (
            patch.object(web_app, "database_config", return_value={}),
            patch.object(web_app, "connect", return_value=connection),
            patch.object(web_app, "ensure_day_data_columns"),
            patch.object(web_app, "fetch_targets", return_value=[target(1)]),
            patch.object(web_app, "fetch_repull_ids", return_value=[1]),
            patch.object(web_app, "crawl_with_compensation", return_value={"stats": {}, "items": [], "remaining_re_pull": 1}) as run,
        ):
            result = web_app.run_optimized_crawl({}, "crawl")
        self.assertEqual(run.call_count, 1)
        self.assertFalse(result["complete"])

    def test_async_api_submits_without_holding_request_for_crawl(self):
        with patch.object(web_app.crawl_jobs, "submit", return_value={"id": "example", "status": "queued"}) as submit:
            response = web_app.app.test_client().post("/api/amazon-day-data/crawl", json={"async": True, "data_ids": [3, 3]})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(submit.call_args.args[0]["data_ids"], [3])

    def test_duplicate_submission_returns_conflict(self):
        with patch.object(web_app.crawl_jobs, "submit", side_effect=CrawlBusy("busy")):
            response = web_app.app.test_client().post("/api/amazon-day-data/crawl", json={"async": True})
        self.assertEqual(response.status_code, 409)

    def test_skipping_deal_applies_with_browser_disabled(self):
        original = web_app.apply_crawl_env({"use_selenium": False})
        try:
            self.assertEqual(os.environ["AMAZON_SKIP_DEAL"], "1")
            self.assertEqual(os.environ["AMAZON_ENABLE_SELENIUM"], "0")
        finally:
            web_app.restore_env(original)

    def test_default_daily_browser_is_chrome(self):
        original = web_app.apply_crawl_env({})
        try:
            self.assertEqual(os.environ["AMAZON_SELENIUM_BROWSER"], "chrome")
        finally:
            web_app.restore_env(original)


if __name__ == "__main__":
    unittest.main()
