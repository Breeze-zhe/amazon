import json
import os
import unittest
from unittest.mock import Mock, patch

import web_app
from amazon_importer import day_crawler as c
from amazon_importer.amazon_sites import get_site_domain, build_product_link


def product(extra):
    return '<div id="productTitle">信号発生器</div><div id="buybox"><input id="add-to-cart-button"></div>' + extra


class JapanTests(unittest.TestCase):
    def test_real_japanese_site_with_chinese_language_cookie(self):
        html = product('<table id="productDetails"><tr><th>推荐度：</th>'
                       '<td>4.2 4.2 颗星，最多 5 颗星 (27)</td></tr>'
                       '<tr><th>亚马逊热销商品排行榜</th><td>'
                       '工业与科研用品商品里排第4,356名 '
                       '(查看工业与科研用品商品销售排行榜) 频率传感器商品里排第1名'
                       '</td></tr></table><div id="availability">现在有货。关于库存情况</div>'
                       '<span id="priceblock_ourprice">¥3,939</span>')
        m = c.extract_metrics(html, 'J', offer_allowed=True)
        self.assertEqual((m.price, m.star, m.reviews, m.bsr, m.inventory),
                         ('¥3,939', '4.2', '27', '1', '1'))
        self.assertTrue(m.is_complete())

    def test_attached_cleanup_preserves_browser_but_stops_service(self):
        driver, profile = Mock(), Mock()
        driver._codex_attached = True
        c.close_selenium_driver(driver, profile)
        driver.quit.assert_not_called()
        driver.service.stop.assert_called_once()
        profile.cleanup.assert_called_once()

    def test_site_and_currency_routing(self):
        self.assertEqual(get_site_domain('J'), 'www.amazon.co.jp')
        self.assertIn('amazon.co.jp/dp/EXAMPLE', build_product_link('J', 'EXAMPLE'))
        self.assertEqual(c.country_from_url('https://www.amazon.co.jp/dp/EXAMPLE'), 'J')
        self.assertTrue(c.get_locale('J').startswith('ja-JP'))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(c.get_postal_code('J'), '100-0001')
        self.assertTrue(all('amazon.co.jp/' in url and '?currency=JPY' in url
                            for _, url in c.build_candidate_urls('J', 'EXAMPLE')))
        self.assertTrue(all('language=ja_JP' in url for _, url in c.build_candidate_urls('J', 'EXAMPLE')))

    def test_yen_does_not_allow_yuan_or_other_currencies(self):
        for value in ('¥1,980', '￥1980', 'JPY1980', '1,980円'):
            self.assertTrue(c.price_matches_site(value, 'J'), value)
            self.assertFalse(c.price_matches_site(value, 'U'), value)
        for value in ('CN¥1980', 'CNY1980', 'RMB1980', '1980元', 'USD12', '$12', '€12', '£12', '₩1980'):
            self.assertFalse(c.price_matches_site(value, 'J'), value)

    def test_yen_whole_price_without_decimal_fraction(self):
        html = product('<div id="corePrice_desktop"><span class="a-price-symbol">￥</span>'
                       '<span class="a-price-whole">1,980</span></div>')
        self.assertEqual(c.extract_metrics(html, 'J', offer_allowed=True).price, '¥1980')
        self.assertIsNone(c.extract_metrics(html.replace('￥', 'CN¥'), 'J', offer_allowed=True).price)

    def test_japanese_detail_metrics_and_small_category_rank(self):
        html = product('<table id="productDetails"><tr><th>おすすめ度</th>'
                       '<td>5つ星のうち4.3 1,234個の評価</td></tr><tr><th>Amazon 売れ筋ランキング</th>'
                       '<td>- 24,531位 家電＆カメラ - 58位 信号発生器</td></tr></table>'
                       '<div id="availability">残り3点 ご注文はお早めに</div>'
                       '<span id="priceblock_ourprice">￥1,980</span>')
        m = c.extract_metrics(html, 'J', offer_allowed=True)
        self.assertEqual((m.star, m.reviews, m.bsr, m.inventory), ('4.3', '1234', '58', '3'))
        self.assertTrue(m.is_complete())

    def test_japanese_stars_do_not_capture_the_scale_as_rating(self):
        for text in ('5つ星のうち4.3', '5 つ星のうち 4.3'):
            m = c.extract_metrics(product('<span class="a-icon-alt">' + text + '</span>'), 'J')
            self.assertEqual(m.star, '4.3')

    def test_japanese_heading_preserves_detail_values(self):
        html = product('<section><h2>登録情報</h2><table><tr><th>カスタマーレビュー</th>'
                       '<td>5つ星のうち4.1 (12)</td></tr></table></section>')
        m = c.extract_metrics(html, 'J')
        self.assertEqual((m.detail_status, m.star, m.reviews), (c.DETAIL_FOUND, '4.1', '12'))

    def test_postal_header_requires_japanese_domain(self):
        driver = Mock(current_url='https://www.amazon.co.jp/')
        driver.execute_script.return_value = 'お届け先 東京都 1000001'
        self.assertTrue(c._delivery_location_matches(driver, 'J'))
        driver.current_url = 'https://www.amazon.com/'
        self.assertFalse(c._delivery_location_matches(driver, 'J'))

    def test_api_accepts_japanese_dedicated_browser(self):
        previous = web_app.apply_crawl_env({'browser': 'chrome', 'debugger_addresses': {'J': '127.0.0.1:9230'}})
        try:
            self.assertEqual(c.site_debugger_addresses(), {'J': '127.0.0.1:9230'})
        finally:
            web_app.restore_env(previous)

    def test_japanese_two_part_postal_input(self):
        first, second, ingress, submit = Mock(), Mock(), Mock(), Mock()
        def visible(_by, selector):
            if selector == '#GLUXZipUpdateInput_0': return [first]
            if selector == '#GLUXZipUpdateInput_1': return [second]
            if 'ZipUpdateInput' in selector: return [first]
            if selector == "#GLUXZipUpdate input[type='submit'], #GLUXZipUpdate button": return [submit]
            return []
        driver = Mock()
        driver.find_elements.side_effect = visible
        driver.execute_async_script.return_value = '{"ok":false}'
        with patch.object(c, '_dismiss_cookie_consent'), patch.object(c.time, 'sleep'), \
             patch.object(c, '_delivery_location_matches', side_effect=[False, True]):
            self.assertTrue(c.set_delivery_location_selenium(driver, 'J', c.get_locale('J')))
        first.send_keys.assert_called_once_with('100')
        second.send_keys.assert_called_once_with('0001')
        submit.click.assert_called_once()


if __name__ == '__main__':
    unittest.main()
