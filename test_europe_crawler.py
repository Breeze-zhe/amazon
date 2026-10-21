import json
import os
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import scheduled_crawl
import web_app
from amazon_importer import day_crawler as crawler


def product_html(extra=""):
    return '<div id="productTitle">Product</div>' + extra


class EuropeParserTests(unittest.TestCase):
    def test_candidate_urls_pin_site_currency(self):
        for country, currency in (("G", "EUR"), ("F", "EUR"), ("I", "EUR"), ("S", "EUR"), ("E", "GBP")):
            urls = crawler.build_candidate_urls(country, "EXAMPLE")
            self.assertTrue(all(f"?currency={currency}" in url for _, url in urls))

    def test_localized_details_keep_values_and_use_small_category_rank(self):
        locales = [
            ("G", "Kundenrezensionen", "4,4 von 5 Sternen (1.234)",
             "Amazon Bestseller-Rang", "Nr. 300 in Auto Nr. 12 in Zubeh\u00f6r"),
            ("F", "Commentaires client", "4,4 sur 5 \u00e9toiles (1 234)",
             "Classement des meilleures ventes", "300 en Auto 12 en Accessoires"),
            ("I", "Recensioni dei clienti", "4,4 su 5 stelle (1.234)",
             "Posizione nella classifica Bestseller", "n. 300 in Auto n. 12 in Accessori"),
        ]
        for country, review_label, reviews, rank_label, rank in locales:
            with self.subTest(country=country):
                html = product_html(
                    '<table id="productDetails"><tr><th>' + review_label
                    + '</th><td>' + reviews + '</td></tr><tr><th>' + rank_label
                    + '</th><td>' + rank + '</td></tr></table>'
                )
                metric = crawler.extract_metrics(html, country)
                self.assertEqual((metric.star, metric.reviews, metric.bsr), ("4.4", "1234", "12"))

    def test_values_outside_detail_table_are_not_erased(self):
        html = product_html(
            '<table id="productDetails"><tr><th>Brand</th><td>Example</td></tr></table>'
            '<span class="a-icon-alt">4,5 sur 5 \u00e9toiles</span>'
            '<span id="acrCustomerReviewText">1.234 \u00e9valuations</span>'
            '<div>Classement des meilleures ventes #12 en Auto</div>'
        )
        metric = crawler.extract_metrics(html, "F")
        self.assertEqual((metric.star, metric.reviews, metric.bsr), ("4.5", "1234", "12"))

    def test_localized_accordion_without_legacy_ids(self):
        for heading in ("Informations sur le produit", "Informazioni sul prodotto"):
            with self.subTest(heading=heading):
                html = product_html('<section><h2>' + heading + '</h2><table>'
                                    '<tr><th>Commentaires client</th><td>4,5 sur 5 \u00e9toiles (123)</td>'
                                    '</tr></table></section>')
                metric = crawler.extract_metrics(html, "F")
                self.assertEqual(metric.detail_status, crawler.DETAIL_FOUND)
                self.assertEqual((metric.star, metric.reviews), ("4.5", "123"))

    def test_currency_codes_next_to_digits_and_mixed_codes(self):
        for price in ("37,96USD", "USD37.96", "37,96EUR USD", "$37.96"):
            with self.subTest(price=price):
                self.assertFalse(crawler.price_matches_site(price, "G"))
        for price in ("37,96EUR", "EUR37,96", "37,96\u20ac"):
            with self.subTest(price=price):
                self.assertTrue(crawler.price_matches_site(price, "G"))

    def test_wrong_currency_is_not_returned_by_price_parser(self):
        for node in (
            '<span class="a-offscreen">37,96USD</span>',
            '<span class="a-price-symbol">USD</span><span class="a-price-whole">37</span>'
            '<span class="a-price-fraction">96</span>',
        ):
            html = product_html('<div id="buybox"><input id="add-to-cart-button"></div>'
                                '<div id="corePrice_desktop"><span class="a-price">' + node + '</span></div>')
            metric = crawler.extract_metrics(html, "G", offer_allowed=True)
            self.assertEqual(metric.buy_box, 1)
            self.assertIsNone(metric.price)

    def test_unknown_offer_stays_retryable_even_when_details_are_absent(self):
        for status in (crawler.DETAIL_FOUND, crawler.DETAIL_EMPTY):
            metric = crawler.CrawlMetric(star="\u65e0", reviews="\u65e0", bsr="\u65e0", detail_status=status)
            self.assertTrue(crawler.metric_needs_repull(metric))
        metric.buy_box = 0
        self.assertFalse(crawler.metric_needs_repull(metric))

    def test_merge_can_replace_absent_marker_with_actual_data(self):
        metric = crawler.CrawlMetric(star="\u65e0", reviews="\u65e0", bsr="\u65e0")
        metric.merge(crawler.CrawlMetric(star="4.5", reviews="123", bsr="12"))
        self.assertEqual((metric.star, metric.reviews, metric.bsr), ("4.5", "123", "12"))


class BrowserRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.driver = Mock()
        self.driver.current_url = "https://www.amazon.de/dp/EXAMPLE"
        self.driver.page_source = product_html('<div id="buybox"><input id="add-to-cart-button"></div>')
        self.env_patch = patch.dict(os.environ, {"AMAZON_SELENIUM_WAIT_SECONDS": "0"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def fetch(self):
        return crawler.fetch_selenium_html(self.driver.current_url, "de-DE", driver=self.driver, country_code="G")

    def test_postal_header_from_another_domain_is_not_trusted(self):
        self.driver.current_url = "https://www.amazon.com/"
        self.driver.execute_script.return_value = "Deliver to Berlin 10115"
        self.assertFalse(crawler._delivery_location_matches(self.driver, "G"))

    def test_resource_timeout_accepts_only_a_verified_product_page(self):
        self.driver.get.side_effect = crawler.TimeoutException("resource timeout")
        with patch.object(crawler, "_delivery_location_matches", return_value=True):
            self.assertEqual(self.fetch(), self.driver.page_source)
        self.driver.execute_script.assert_called_once_with("window.stop()")
        self.driver.page_source = "<html><body>Service unavailable</body></html>"
        with patch.object(crawler, "_delivery_location_matches", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "no product details"):
                self.fetch()

    def test_product_wait_ends_early_when_core_metrics_are_complete(self):
        complete = crawler.CrawlMetric(
            buy_box=1, price="EUR12", star="4.5", reviews="10", bsr="12", offer_verified=True
        )
        incomplete = crawler.CrawlMetric(star="4.5")
        with (
            patch.object(crawler, "extract_metrics", side_effect=[incomplete, complete]),
            patch.object(crawler.time, "sleep") as sleep,
        ):
            html = crawler._wait_for_complete_product_html(self.driver, "G", 2)
        self.assertEqual(html, self.driver.page_source)
        sleep.assert_called_once_with(0.25)

    def test_postal_change_on_product_page_is_recovered_and_rechecked(self):
        with (
            patch.object(crawler, "_delivery_location_matches", side_effect=[False, True]) as verify,
            patch.object(crawler, "set_delivery_location_selenium", return_value=True) as setup,
        ):
            self.assertEqual(self.fetch(), self.driver.page_source)
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(self.driver.get.call_count, 2)
        setup.assert_called_once()

    def test_persistent_wrong_postal_is_rejected_after_recovery(self):
        with (
            patch.object(crawler, "_delivery_location_matches", return_value=False),
            patch.object(crawler, "set_delivery_location_selenium", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "postal verification failed"):
                self.fetch()
        self.assertEqual(self.driver.get.call_count, 2)

    def test_location_setup_failure_retries_before_giving_up(self):
        metric = crawler.CrawlMetric(buy_box=1, price="EUR12", star="4.5", reviews="10", bsr="12", offer_verified=True)
        with (
            patch.dict(os.environ, {"AMAZON_ENABLE_SELENIUM": "1", "AMAZON_SELENIUM_ONLY": "1"}),
            patch.object(crawler, "set_delivery_location_requests"),
            patch.object(crawler, "set_delivery_location_curl"),
            patch.object(crawler, "set_delivery_location_selenium", side_effect=[False, True]) as setup,
            patch.object(crawler, "fetch_selenium_html", return_value=product_html()),
            patch.object(crawler, "extract_metrics", return_value=metric),
        ):
            result, attempts = crawler.crawl_item_metrics("G", "EXAMPLE", selenium_driver=object(), selenium_location_state={})
        self.assertEqual(setup.call_count, 2)
        self.assertTrue(result.is_complete())
        self.assertFalse(attempts[0].success)

    def test_currency_preference_is_pinned_to_site_cookie(self):
        self.driver.current_url = "https://www.amazon.de/"
        self.assertTrue(crawler.set_currency_preference_selenium(self.driver, "G"))
        self.driver.add_cookie.assert_called_once_with({
            "name": "i18n-prefs", "value": "EUR", "domain": ".amazon.de", "path": "/"
        })

    def test_dead_browser_session_is_recreated_for_current_item(self):
        first_driver = object()
        second_driver = object()
        complete = crawler.CrawlMetric(
            buy_box=1,
            price="EUR12",
            star="4.5",
            reviews="10",
            bsr="12",
            offer_verified=True,
            detail_status=crawler.DETAIL_FOUND,
        )
        lost = crawler.SeleniumSessionError(
            "invalid session id",
            crawler.CrawlMetric(),
            [crawler.CrawlAttempt("selenium:product-dp", "https://www.amazon.de/dp/EXAMPLE", False, "invalid session id")],
        )
        connection = Mock()
        with (
            patch.dict(os.environ, {"AMAZON_ENABLE_SELENIUM": "1"}),
            patch.object(crawler, "ensure_re_pull_column"),
            patch.object(crawler, "fetch_targets", return_value=[{
                "id": 1, "asin": "EXAMPLE", "asin_country": "EXAMPLE-G",
                "country": "G", "name": "Example",
            }]),
            patch.object(crawler, "create_selenium_driver", side_effect=[(first_driver, None), (second_driver, None)]) as create,
            patch.object(crawler, "close_selenium_driver") as close,
            patch.object(crawler, "crawl_item_metrics", side_effect=[lost, (complete, [])]) as crawl,
            patch.object(crawler, "upsert_day_record", return_value="updated"),
            patch.object(crawler, "write_crawl_diagnostic"),
        ):
            result = crawler.crawl_amazon_day_data(connection)

        self.assertEqual(create.call_count, 2)
        close.assert_any_call(first_driver, None)
        close.assert_any_call(second_driver, None)
        self.assertEqual(crawl.call_count, 2)
        self.assertIs(crawl.call_args_list[0].kwargs["selenium_driver"], first_driver)
        self.assertIs(crawl.call_args_list[1].kwargs["selenium_driver"], second_driver)
        self.assertEqual(result["stats"]["updated"], 1)
        self.assertEqual(result["items"][0]["attempts"][1]["method"], "selenium:session-recovery")

    def test_daily_crawl_groups_targets_by_market_without_dropping_items(self):
        targets = [
            {"id": 3, "asin": "C", "asin_country": "C-C", "country": "C", "name": "C"},
            {"id": 2, "asin": "G2", "asin_country": "G2-G", "country": "G", "name": "G2"},
            {"id": 1, "asin": "G1", "asin_country": "G1-G", "country": "G", "name": "G1"},
        ]
        order = []
        metric = crawler.CrawlMetric(buy_box=0, star="4.5", reviews="10", bsr="12")

        def crawl(country, asin, **kwargs):
            order.append((country, asin))
            return metric, []

        with (
            patch.dict(os.environ, {"AMAZON_ENABLE_SELENIUM": "0"}),
            patch.object(crawler, "ensure_re_pull_column"),
            patch.object(crawler, "fetch_targets", return_value=targets),
            patch.object(crawler, "crawl_item_metrics", side_effect=crawl),
            patch.object(crawler, "upsert_day_record", return_value="inserted"),
            patch.object(crawler, "write_crawl_diagnostic"),
        ):
            result = crawler.crawl_amazon_day_data(Mock())

        self.assertEqual(order, [("C", "C"), ("G", "G1"), ("G", "G2")])
        self.assertEqual(result["stats"]["total_targets"], len(targets))
        self.assertEqual({item["data_id"] for item in result["items"]}, {1, 2, 3})


class CompensationTests(unittest.TestCase):
    def update(self, stored, incoming, **kwargs):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = stored
        with patch.object(crawler, "fetch_existing_day_id", return_value=7):
            crawler.upsert_day_record(connection, 1, "EXAMPLE", "Product", incoming, date(2026, 10, 16), **kwargs)
        sql, params = cursor.execute.call_args.args
        columns = re.findall(r"`([^`]+)` = %s", sql)
        return dict(zip(columns, params))

    def test_compensation_uses_merged_stored_metrics_for_retry_flag(self):
        stored = dict(buy_box=1, price="EUR12", star="4.5", reviews=None, bsr="12")
        incoming = crawler.CrawlMetric(reviews="123")
        updates = self.update(stored, incoming, fill_missing_only=True)
        self.assertEqual(updates["reviews"], "123")
        self.assertEqual(updates["re_pull"], 0)
        self.assertNotIn("price", updates)

    def test_compensation_repairs_old_absent_markers(self):
        stored = dict(buy_box=1, price="EUR12", star="\u65e0", reviews="123", bsr="\u65e0")
        updates = self.update(stored, crawler.CrawlMetric(star="4.5", bsr="12"), fill_missing_only=True)
        self.assertEqual((updates["star"], updates["bsr"], updates["re_pull"]), ("4.5", "12", 0))

    def test_compensation_replaces_wrong_currency_in_existing_row(self):
        stored = dict(buy_box=1, price="37,96USD", star="4.5", reviews="123", bsr="12")
        updates = self.update(stored, crawler.CrawlMetric(buy_box=1, price="EUR35", offer_verified=True),
                              fill_missing_only=True, country_code="G")
        self.assertEqual((updates["price"], updates["re_pull"]), ("EUR35", 0))

    def test_wrong_stored_currency_is_cleared_and_kept_pending_without_replacement(self):
        stored = dict(buy_box=1, price="37,96USD", star="4.5", reviews="123", bsr="12")
        updates = self.update(stored, crawler.CrawlMetric(), fill_missing_only=True, country_code="G")
        self.assertIsNone(updates["price"])
        self.assertEqual(updates["re_pull"], 1)

    def test_last_round_cannot_force_incomplete_record_closed(self):
        updates = self.update({}, crawler.CrawlMetric(star="4.5"), finalize_repull=True)
        self.assertEqual(updates["re_pull"], 1)

    def test_five_round_limit_preserves_pending_records_and_reports_incomplete(self):
        connection = Mock()
        result = {"stats": {"total_targets": 1, "updated": 1}, "items": [{"data_id": 1, "db_action": "updated"}]}
        with (
            patch.object(web_app, "fetch_targets", return_value=[{"id": 1}]),
            patch.object(web_app, "fetch_repull_ids", return_value=[1]),
            patch.object(web_app, "crawl_amazon_day_data", return_value=result) as crawl,
        ):
            combined = web_app.crawl_with_compensation(connection, crawl_kwargs={}, count_day="2026-10-16")
        self.assertEqual(crawl.call_count, 6)
        self.assertFalse(combined["complete"])
        self.assertEqual(combined["remaining_re_pull"], 1)
        self.assertEqual(combined["stats"]["unique_targets"], 1)
        self.assertEqual(combined["stats"]["complete_targets"], 0)
        connection.commit.assert_not_called()
        connection.cursor.assert_not_called()
        self.assertTrue(all(not call.kwargs.get("finalize_repull") for call in crawl.call_args_list))

    def test_recovered_error_does_not_count_as_final_failure(self):
        failed = {"stats": {"failed": 1}, "items": [{"data_id": 1, "db_action": "failed"}]}
        recovered = {"stats": {"updated": 1}, "items": [{"data_id": 1, "db_action": "updated"}]}
        with (
            patch.object(web_app, "fetch_targets", return_value=[{"id": 1}]),
            patch.object(web_app, "fetch_repull_ids", side_effect=[[1], [], []]),
            patch.object(web_app, "crawl_amazon_day_data", side_effect=[failed, recovered]),
        ):
            combined = web_app.crawl_with_compensation(Mock(), crawl_kwargs={}, count_day="2026-10-16")
        self.assertTrue(combined["complete"])
        self.assertEqual(combined["stats"]["complete_targets"], 1)
        self.assertEqual(combined["stats"]["failed_targets"], 0)

    def test_empty_scope_cannot_pick_up_other_products(self):
        connection = Mock()
        self.assertEqual(web_app.fetch_repull_ids(connection, "2026-10-16", []), [])
        connection.cursor.assert_not_called()


class DiagnosticTests(unittest.TestCase):
    def test_record_log_keeps_failure_reason_and_retry_state(self):
        record = crawler.CrawlRecord(1, "EXAMPLE", "EXAMPLE-G", "G", date(2026, 10, 16))
        record.attempts = [crawler.CrawlAttempt("selenium:delivery-location", "https://www.amazon.de/", False, "postal verification failed")]
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(crawler, "Path") as path:
                path.return_value.resolve.return_value.parent.parent = Path(temp_dir)
                crawler.write_crawl_diagnostic(record)
            entry = json.loads((Path(temp_dir) / "crawl_attempts.jsonl").read_text(encoding="utf-8"))
        self.assertTrue(entry["needs_repull"])
        self.assertEqual(entry["attempts"][0]["message"], "postal verification failed")

    def test_scheduler_treats_incomplete_response_as_unsuccessful_run(self):
        with (
            patch.object(scheduled_crawl.scheduled_runner, "acquire_locks", return_value=MagicMock()),
            patch.object(scheduled_crawl.scheduled_runner, "run", return_value=1) as run,
        ):
            self.assertEqual(scheduled_crawl.main(), 1)
        self.assertFalse(run.call_args.kwargs['resume_auth'])


if __name__ == "__main__":
    unittest.main()
