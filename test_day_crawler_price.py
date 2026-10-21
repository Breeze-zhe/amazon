import json
import os
import unittest
from datetime import date
from unittest.mock import Mock, patch

from amazon_importer import day_crawler


def product_html(*, cart=True, price_html="", extra_html=""):
    cart_html = '<input id="add-to-cart-button" value="Add to Cart">' if cart else ""
    return f"""
        <html><body>
            <div id="productTitle">Test product</div>
            <div id="buybox">{cart_html}</div>
            {price_html}
            {extra_html}
        </body></html>
    """


class BuyBoxPriceTests(unittest.TestCase):
    def test_delivery_location_uses_submit_input_and_de_uk_confirmation(self):
        class Node:
            def __init__(self, click=None):
                self.value = ""
                self.click_callback = click
                self.clicks = 0

            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            def click(self):
                self.clicks += 1
                if self.click_callback:
                    self.click_callback()

            def clear(self):
                self.value = ""

            def send_keys(self, value):
                self.value += value

        class Driver:
            def __init__(self):
                self.dialog_open = False
                self.submitted = False
                self.confirmed = False
                self.ingress = Node(lambda: setattr(self, "dialog_open", True))
                self.postal = Node()
                self.submit = Node(lambda: setattr(self, "submitted", True))
                self.confirm = Node(lambda: setattr(self, "confirmed", True))

            def get(self, _url):
                return None

            def set_script_timeout(self, _timeout):
                return None

            def execute_async_script(self, *_args):
                return '{"ok": false}'

            def find_elements(self, _by, selector):
                if "ZipUpdateInput" in selector:
                    return [self.postal] if self.dialog_open else []
                if selector == "#nav-global-location-popover-link":
                    return [self.ingress]
                if selector == "#GLUXZipUpdate input[type='submit'], #GLUXZipUpdate button":
                    return [self.submit] if self.dialog_open else []
                if selector == "#GLUXConfirmClose input, #GLUXConfirmClose button":
                    return [self.confirm] if self.submitted else []
                return []

        driver = Driver()
        with (
            patch.object(day_crawler, "_dismiss_cookie_consent"),
            patch.object(day_crawler.time, "sleep"),
            patch.object(
                day_crawler,
                "_delivery_location_matches",
                side_effect=lambda current, _country: current.confirmed,
            ),
        ):
            ready = day_crawler.set_delivery_location_selenium(driver, "E", "en-GB")

        self.assertTrue(ready)
        self.assertEqual(driver.postal.value, "SW1A 1AA")
        self.assertEqual(driver.ingress.clicks, 1)
        self.assertEqual(driver.submit.clicks, 1)
        self.assertEqual(driver.confirm.clicks, 1)

    def test_delivery_location_match_ignores_postcode_spacing_and_hidden_marks(self):
        driver = Mock()
        driver.current_url = "https://www.amazon.co.uk/"
        driver.execute_script.return_value = "Deliver to London SW1A1AA\u200c"
        self.assertTrue(day_crawler._delivery_location_matches(driver, "E"))

    def test_existing_delivery_location_skips_dialog(self):
        driver = Mock()
        with (
            patch.object(day_crawler, "_dismiss_cookie_consent"),
            patch.object(day_crawler.time, "sleep"),
            patch.object(day_crawler, "_delivery_location_matches", return_value=True),
        ):
            ready = day_crawler.set_delivery_location_selenium(driver, "G", "de-DE")

        self.assertTrue(ready)
        driver.execute_async_script.assert_not_called()
        driver.find_elements.assert_not_called()

    def test_confirmed_cart_uses_primary_site_price(self):
        html = product_html(
            price_html=(
                '<div id="corePrice_desktop"><span class="a-price">'
                '<span class="a-offscreen">$19.99</span></span></div>'
            )
        )
        metrics = day_crawler.extract_metrics(html, "U", offer_allowed=True)
        self.assertEqual(metrics.buy_box, 1)
        self.assertEqual(metrics.price, "$19.99")

    def test_confirmed_german_cart_uses_euro_price(self):
        html = product_html(
            price_html=(
                '<div id="corePrice_desktop"><span class="a-price">'
                '<span class="a-offscreen">24,55€</span></span></div>'
            )
        )
        metrics = day_crawler.extract_metrics(html, "G", offer_allowed=True)
        self.assertEqual(metrics.buy_box, 1)
        self.assertEqual(metrics.price, "24,55€")

    def test_verified_buy_box_extracts_discount_coupon_and_promotion(self):
        html = product_html(
            price_html=(
                '<div id="corePrice_desktop">'
                '<span class="a-price"><span class="a-offscreen">£27.99</span></span>'
                '<span class="savingsPercentage">-7%</span>'
                '<span class="dealBadge">Limited Time Deal</span>'
                '<div id="couponFeature"><span>Voucher: Apply 5% voucher</span></div>'
                '</div>'
            )
        )
        metrics = day_crawler.extract_metrics(html, "E", offer_allowed=True)
        self.assertEqual(metrics.deal, [
            {"type": "price_discount", "label": "-7%"},
            {"type": "coupon", "label": "Voucher: Apply 5% voucher"},
            {"type": "promotion", "label": "Limited Time Deal"},
        ])

    def test_deal_parser_requires_verified_buy_box_and_ignores_other_seller(self):
        html = product_html(
            price_html='<div id="corePrice_desktop">-7% Limited Time Deal Voucher</div>'
        )
        self.assertIsNone(day_crawler.extract_metrics(html, "E", offer_allowed=False).deal)
        html_without_cart = product_html(
            cart=False,
            price_html='<div id="corePrice_desktop">-7% Limited Time Deal Voucher</div>',
        )
        self.assertIsNone(day_crawler.extract_metrics(html_without_cart, "E", offer_allowed=True).deal)

        other_seller = product_html(
            price_html='<div id="corePrice_desktop">£29.99</div>',
            extra_html='<div id="aod-offer">-70% Voucher Limited Time Deal</div>',
        )
        self.assertEqual(day_crawler.extract_metrics(other_seller, "E", offer_allowed=True).deal, [])

    def test_verified_offer_without_discount_has_empty_deal_list(self):
        html = product_html(price_html='<div id="corePrice_desktop">£29.99</div>')
        metrics = day_crawler.extract_metrics(html, "E", offer_allowed=True)
        self.assertEqual(metrics.deal, [])

    def test_spanish_promocion_is_captured_as_promotion(self):
        html = product_html(price_html='<div id="corePrice_desktop">Promoción -7% $1,020.60</div>')
        metrics = day_crawler.extract_metrics(html, "M", offer_allowed=True)
        self.assertEqual(metrics.deal, [
            {"type": "price_discount", "label": "-7%"},
            {"type": "promotion", "label": "Promoción"},
        ])

    def test_discount_text_and_strike_price_are_recognized(self):
        for price_markup, expected in (
            ('<span>10% off</span>', "10% off"),
            ('<span class="a-text-price"><span class="a-offscreen">£39.99</span></span>', "Was £39.99"),
        ):
            with self.subTest(price_markup=price_markup):
                metrics = day_crawler.extract_metrics(
                    product_html(price_html=f'<div id="corePrice_desktop">{price_markup}</div>'),
                    "E",
                    offer_allowed=True,
                )
                self.assertEqual(metrics.deal[0], {"type": "price_discount", "label": expected})

    def test_deal_terms_follow_each_supported_market_language(self):
        examples = (
            ("U", "Save 10% Coupon", {"price_discount", "coupon"}),
            ("M", "Ahorra 10% Cupón Promoción", {"price_discount", "coupon", "promotion"}),
            ("G", "10% Rabatt Gutschein Blitzangebot", {"price_discount", "coupon", "promotion"}),
            ("E", "10% off Voucher Limited Time Deal", {"price_discount", "coupon", "promotion"}),
            ("C", "10% off Coupon", {"price_discount", "coupon"}),
            ("F", "Économisez 10% Bon de réduction Offre du jour", {"price_discount", "coupon", "promotion"}),
            ("I", "Risparmia 10% Buono sconto Offerta lampo", {"price_discount", "coupon", "promotion"}),
            ("S", "10% de descuento Cupón Promoción", {"price_discount", "coupon", "promotion"}),
            ("A", "Save 10% Coupon Deal of the day", {"price_discount", "coupon", "promotion"}),
        )
        for country, content, expected_types in examples:
            with self.subTest(country=country):
                html = product_html(price_html=f'<div id="corePrice_desktop">{content}</div>')
                metrics = day_crawler.extract_metrics(html, country, offer_allowed=True)
                self.assertEqual({item["type"] for item in metrics.deal}, expected_types)

    def test_no_cart_rejects_visible_and_structured_prices(self):
        html = product_html(
            cart=False,
            price_html='<span class="a-price"><span class="a-offscreen">$17.50</span></span>',
            extra_html=(
                '<script type="application/ld+json">'
                '{"offers":{"price":"16.00","priceCurrency":"USD"}}'
                '</script>'
            ),
        )
        metrics = day_crawler.extract_metrics(html, "U", offer_allowed=True)
        self.assertEqual(metrics.buy_box, 0)
        self.assertIsNone(metrics.price)
        self.assertTrue(metrics.is_complete() is False)

    def test_other_seller_price_is_not_used(self):
        html = product_html(
            extra_html=(
                '<div id="aod-offer"><span class="a-price">'
                '<span class="a-offscreen">$14.00</span></span></div>'
            )
        )
        metrics = day_crawler.extract_metrics(html, "U", offer_allowed=True)
        self.assertEqual(metrics.buy_box, 1)
        self.assertIsNone(metrics.price)

    def test_wrong_site_currency_is_rejected(self):
        html = product_html(
            price_html=(
                '<div id="corePrice_desktop"><span class="a-price">'
                '<span class="a-offscreen">CN¥139.00</span></span></div>'
            )
        )
        metrics = day_crawler.extract_metrics(html, "U", offer_allowed=True)
        self.assertEqual(metrics.buy_box, 1)
        self.assertIsNone(metrics.price)
        self.assertFalse(day_crawler.price_matches_site("CNY 139.00", "U"))
        self.assertFalse(day_crawler.price_matches_site("￥139.00", "U"))

    def test_wrong_split_price_symbol_is_rejected(self):
        html = product_html(
            price_html=(
                '<div id="corePrice_desktop"><span class="a-price">'
                '<span class="a-price-symbol">¥</span>'
                '<span class="a-price-whole">139</span>'
                '<span class="a-price-fraction">00</span></span></div>'
            )
        )
        metrics = day_crawler.extract_metrics(html, "U", offer_allowed=True)
        self.assertEqual(metrics.buy_box, 1)
        self.assertIsNone(metrics.price)

    def test_unverified_client_cannot_supply_cart_or_price(self):
        html = product_html(
            price_html=(
                '<div id="corePrice_desktop"><span class="a-price">'
                '<span class="a-offscreen">$19.99</span></span></div>'
            )
        )
        metrics = day_crawler.extract_metrics(html, "U", offer_allowed=False)
        self.assertIsNone(metrics.buy_box)
        self.assertIsNone(metrics.price)

    def test_cart_page_overrides_earlier_no_cart_page_atomically(self):
        combined = day_crawler.CrawlMetric(buy_box=0, offer_verified=True)
        combined.merge(day_crawler.CrawlMetric(
            buy_box=1,
            price="$21.00",
            offer_verified=True,
        ))
        self.assertEqual(combined.buy_box, 1)
        self.assertEqual(combined.price, "$21.00")

    def test_country_detection_requires_exact_amazon_domain(self):
        self.assertEqual(day_crawler.country_from_url("https://www.amazon.com.mx/dp/X"), "M")
        self.assertEqual(day_crawler.country_from_url("https://www.amazon.com/dp/X"), "U")
        self.assertFalse(day_crawler.url_matches_site("https://www.amazon.cn/dp/X", "U"))

    def test_failed_postal_verification_skips_browser_offer(self):
        driver = object()
        fetch = Mock()
        env = {
            "AMAZON_ENABLE_SELENIUM": "1",
            "AMAZON_SELENIUM_ONLY": "1",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(day_crawler, "set_delivery_location_requests"),
            patch.object(day_crawler, "set_delivery_location_curl"),
            patch.object(day_crawler, "set_delivery_location_selenium", return_value=False),
            patch.object(day_crawler, "fetch_selenium_html", fetch),
        ):
            metrics, attempts = day_crawler.crawl_item_metrics(
                "U", "B000TEST", selenium_driver=driver, selenium_location_state={}
            )

        fetch.assert_not_called()
        self.assertIsNone(metrics.buy_box)
        self.assertIsNone(metrics.price)
        self.assertFalse(metrics.offer_verified)
        self.assertTrue(any(attempt.method == "selenium:delivery-location" for attempt in attempts))

    def test_no_cart_is_complete_without_a_price(self):
        metrics = day_crawler.CrawlMetric(
            buy_box=0,
            price=None,
            star="4.5",
            reviews="10",
            bsr="100",
            offer_verified=True,
        )
        self.assertTrue(metrics.is_complete())

    def test_unverified_offer_does_not_overwrite_stored_offer(self):
        class Cursor:
            def __init__(self, connection):
                self.connection = connection

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params):
                self.connection.statements.append((sql, params))

        class Connection:
            def __init__(self):
                self.statements = []

            def cursor(self):
                return Cursor(self)

            def commit(self):
                return None

        connection = Connection()
        metrics = day_crawler.CrawlMetric(
            star="4.5",
            reviews="10",
            bsr="100",
            offer_verified=False,
        )
        with patch.object(day_crawler, "fetch_existing_day_id", return_value=7):
            day_crawler.upsert_day_record(
                connection,
                98,
                "B0TEST德国",
                "Test",
                metrics,
                date(2026, 9, 28),
            )

        update_sql = connection.statements[-1][0]
        self.assertNotIn("`buy_box`", update_sql)
        self.assertNotIn("`price`", update_sql)

    def test_verified_no_cart_clears_stored_price(self):
        class Cursor:
            def __init__(self, connection):
                self.connection = connection

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params):
                self.connection.statements.append((sql, params))

        class Connection:
            def __init__(self):
                self.statements = []

            def cursor(self):
                return Cursor(self)

            def commit(self):
                return None

        connection = Connection()
        metrics = day_crawler.CrawlMetric(
            buy_box=0,
            star="4.5",
            reviews="10",
            bsr="100",
            offer_verified=True,
        )
        with patch.object(day_crawler, "fetch_existing_day_id", return_value=7):
            day_crawler.upsert_day_record(
                connection,
                99,
                "B0TEST美国",
                "Test",
                metrics,
                date(2026, 9, 28),
            )

        update_sql, update_params = connection.statements[-1]
        self.assertIn("`buy_box`", update_sql)
        self.assertIn("`price`", update_sql)
        self.assertIn(None, update_params)

    def test_upsert_persists_structured_deal_json(self):
        class Cursor:
            def __init__(self, connection):
                self.connection = connection

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params):
                self.connection.statement = (sql, params)

        class Connection:
            def __init__(self):
                self.statement = None

            def cursor(self):
                return Cursor(self)

            def commit(self):
                return None

        deal = [{"type": "coupon", "label": "Apply 5% voucher"}]
        connection = Connection()
        with patch.object(day_crawler, "fetch_existing_day_id", return_value=None):
            day_crawler.upsert_day_record(
                connection,
                101,
                "B0TESTUK",
                "Test",
                day_crawler.CrawlMetric(
                    buy_box=1,
                    price="£27.99",
                    star="4.5",
                    reviews="10",
                    bsr="100",
                    deal=deal,
                    offer_verified=True,
                ),
                date(2026, 10, 19),
            )

        sql, params = connection.statement
        self.assertIn("`deal`", sql)
        self.assertIn(json.dumps(deal, ensure_ascii=False), params)


if __name__ == "__main__":
    unittest.main()
