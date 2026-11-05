from contextlib import nullcontext
from datetime import date
import unittest
from unittest.mock import Mock, patch

from amazon_importer import day_crawler as c
from amazon_importer import importer
from amazon_importer.crawl_jobs import CrawlJobs
import web_app


class BrokenPageDetectionTests(unittest.TestCase):
    def test_amazon_us_and_uk_missing_link_screenshots_are_terminal_not_captcha(self):
        pages = (
            '''<title>Page Not Found</title><h1>SORRY</h1><h2>we couldn't find that page</h2>
                <a>Amazon's home page</a><img alt="Bailey Meet the dogs of Amazon">''',
            '''<title>Page Not Found</title><b>Looking for something?</b><br>
                We're sorry. The Web address you entered is not a functioning page on our site.
                <a>Click here to go back to the Amazon home page</a>''',
        )
        for country, html in zip(('U', 'E'), pages):
            with self.subTest(country=country):
                self.assertFalse(c.is_blocked_html(html))
                self.assertTrue(c.is_product_link_unavailable_html(html))

    def test_all_marketplaces_use_the_same_terminal_page_rule(self):
        html = '''<html><head><title>Page Not Found</title><script>validateCaptcha();</script></head>
                  <body><h1>SORRY</h1><p>we couldn't find that page</p>
                  <a href="/">Amazon's home page</a></body></html>'''
        for country in ('U', 'M', 'C', 'G', 'E', 'I', 'F', 'S', 'J', 'A'):
            with self.subTest(country=country):
                self.assertTrue(c.is_product_link_unavailable_html(html))
                self.assertFalse(c.is_blocked_html(html))

    def test_real_captcha_is_never_classified_as_broken_link(self):
        html = '<title>Robot Check</title><form action="/errors/validateCaptcha"><input id="captchacharacters"></form>'
        self.assertTrue(c.is_blocked_html(html))
        self.assertFalse(c.is_product_link_unavailable_html(html))

    def test_product_copy_does_not_trigger_missing_link_classification(self):
        html = '<div id="productTitle">Page Not Found sticker</div><div id="buybox">test</div>'
        self.assertFalse(c.is_product_link_unavailable_html(html))

    def test_missing_link_returns_before_any_secondary_request(self):
        with patch.dict('os.environ', {'AMAZON_ENABLE_SELENIUM': '1',
                                      'AMAZON_SELENIUM_ONLY': '1', 'AMAZON_PRIMARY_ONLY': '1',
                                      'AMAZON_STOP_ON_BLOCK': '1'}), \
             patch.object(c, 'get_postal_code', return_value='10001'), \
             patch.object(c, '_delivery_location_matches', return_value=True), \
             patch.object(c, 'fetch_selenium_html', side_effect=c.ProductLinkUnavailable(c.LINK_UNAVAILABLE_MESSAGE)) as browser, \
             patch.object(c, 'fetch_requests_html') as requests, patch.object(c, 'fetch_curl_html') as curl:
            _, attempts = c.crawl_item_metrics('U', 'B0G7CP744Q', selenium_driver=Mock(),
                                               selenium_location_state={'country':'U','postal_code':'10001','currency_country':'U'})
        self.assertEqual(len(attempts), 1)
        self.assertFalse(attempts[0].success)
        self.assertIn('page not found', attempts[0].message)
        browser.assert_called_once()
        requests.assert_not_called()
        curl.assert_not_called()

    def test_target_filter_uses_only_existing_activity_fields(self):
        connection = Mock()
        connection.cursor.return_value.__enter__ = Mock()
        connection.cursor.return_value.__exit__ = Mock(return_value=False)
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        c.fetch_targets(connection, data_ids=[1], exclude_activity=True)
        query, params = cursor.execute.call_args.args
        self.assertNotIn("label", query)
        self.assertNotIn("ad_activity", query)
        self.assertIn("TRIM(COALESCE(deal, ''))", query)
        self.assertEqual(params, [1])

    def test_unavailable_item_is_recorded_for_this_run_and_next_target_continues(self):
        first = {'id': 1008, 'asin':'B0G7CP744Q', 'asin_country':'B0G7CP744Q美国','country':'U','name':'标签打印机'}
        second = {'id': 1009, 'asin':'B0EXAMPLE01', 'asin_country':'B0EXAMPLE01美国','country':'U','name':'下一个商品'}
        db = Mock()
        db.__enter__ = Mock(return_value=db)
        db.__exit__ = Mock(return_value=False)
        db.cursor.return_value.__enter__ = Mock(return_value=db.cursor.return_value)
        db.cursor.return_value.__exit__ = Mock(return_value=False)
        metric = c.CrawlMetric(buy_box=1, price='$12.00', star='4', reviews='1', bsr='1', offer_verified=True)
        with patch.dict('os.environ', {'AMAZON_ENABLE_SELENIUM':'0'}), \
             patch.object(c, 'ensure_re_pull_column'), patch.object(c, 'fetch_targets', return_value=[first, second]), \
             patch.object(c, 'fetch_completed_day_ids', return_value=set()), \
             patch.object(c, 'crawl_item_metrics', side_effect=[
                 (c.CrawlMetric(), [c.CrawlAttempt('selenium:product-dp','https://www.amazon.com/dp/B0G7CP744Q',False,c.LINK_UNAVAILABLE_MESSAGE)]),
                 (metric, [])]) as crawl, \
             patch.object(c, 'upsert_day_record', return_value='updated') as upsert, \
             patch.object(c, 'write_crawl_diagnostic') as diagnostic:
            result = c.crawl_amazon_day_data(db, data_ids=[1008,1009], count_day=date.today())
        self.assertEqual(crawl.call_count, 2)
        self.assertEqual(upsert.call_count, 2)
        self.assertEqual(result['items'][0]['db_action'], 'link_unavailable')
        self.assertEqual(result['items'][0]['error'], c.LINK_UNAVAILABLE_MESSAGE)
        self.assertEqual(result['stats']['link_unavailable'], 1)
        self.assertEqual(result['items'][1]['asin'], 'B0EXAMPLE01')
        self.assertFalse(any(
            "UPDATE `amazon_data`" in str(call.args[0])
            for call in db.cursor.return_value.execute.call_args_list
        ))
        self.assertEqual(diagnostic.call_count, 2)

    def test_unavailable_items_are_not_persisted_as_cross_run_skip_state(self):
        connection = Mock()
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchall.return_value = []
        connection.cursor.return_value = cursor
        self.assertEqual(c.fetch_completed_day_ids(connection, date.today(), [1008,1009]), set())
        self.assertFalse(any("amazon_data" in str(call.args[0]) and "label" in str(call.args[0])
                             for call in cursor.execute.call_args_list))

    def test_removed_label_field_is_not_imported_or_editable(self):
        mapping, _ = importer.build_column_mapping(
            ["ASIN", "简称", "label", "标签", "广告活动"], ["asin", "country"])
        self.assertNotIn("label", mapping)
        self.assertNotIn("ad_activity", mapping)
        with self.assertRaises(ValueError):
            web_app.normalize_edit_payload({"label": "链接挂了"})


if __name__ == '__main__':
    unittest.main()
