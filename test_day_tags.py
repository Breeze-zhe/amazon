import unittest
import json
from unittest.mock import patch

import web_app


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.connection.queries.append((sql, params))

    def fetchall(self):
        if "SELECT data_id, count_day, price" in self.sql:
            return self.connection.previous
        return self.connection.records


class FakeConnection:
    def __init__(self, records, previous):
        self.records = records
        self.previous = previous
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return FakeCursor(self)


class DayTagTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()
        self.records = [
            dict(id=10, data_id=1, name="Alpha", asin="A1", country_name="德国",
                 price="12", deal=json.dumps([
                     {"type": "price_discount", "label": "-7%"},
                     {"type": "coupon", "label": "Apply 5% voucher"},
                     {"type": "promotion", "label": "Limited Time Deal"},
                 ]), bsr="200", star="4.1", reviews="8", buy_box=1),
            dict(id=11, data_id=2, name="Beta", asin="B2", country_name="英国",
                 price=None, bsr=None, star="4.5", reviews="20", buy_box=0),
            dict(id=12, data_id=3, name="Gamma", asin="C3", country_name="加拿大",
                 price="9", bsr="90", star="4.8", reviews="30", buy_box=1),
        ]
        self.previous = [
            dict(data_id=1, price="10", bsr="100", star="4.8", reviews="10", buy_box=1),
            dict(data_id=2, price="8", bsr="100", star="4.5", reviews="20", buy_box=1),
        ]
        self.connection = FakeConnection(self.records, self.previous)
        self.patches = (
            patch.object(web_app, "connect", return_value=self.connection),
            patch.object(web_app, "database_config", return_value={}),
            patch.object(web_app, "ensure_day_data_columns"),
        )
        for active_patch in self.patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

    def get_data(self, **params):
        result = self.client.get("/api/day-data", query_string={"date": "2026-09-24", **params})
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()

    def test_multiple_tags_and_unique_group_counts(self):
        result = self.get_data()
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["summary"]["risk_count"], 2)
        self.assertEqual(result["summary"]["attention_count"], 0)
        self.assertEqual(result["summary"]["price_changed"], 1)
        self.assertEqual(result["summary"]["buy_box_missing"], 1)
        self.assertEqual(result["summary"]["missing"], 1)
        first = result["rows"][0]
        self.assertEqual(first["buy_box"], "1")
        self.assertEqual(first["tag_keys"], [
            "rank_up", "star_down", "reviews_down", "price_changed"
        ])
        self.assertEqual([tag["color"] for tag in first["tags"]], [
            "blue", "yellow", "green", "red"
        ])
        self.assertEqual(first["deal"], [
            {"type": "price_discount", "label": "-7%"},
            {"type": "coupon", "label": "Apply 5% voucher"},
            {"type": "promotion", "label": "Limited Time Deal"},
        ])
        self.assertIn("d.deal", self.connection.queries[0][0])
        self.assertEqual(result["rows"][1]["tag_keys"], ["buy_box_missing", "missing"])
        self.assertEqual(result["rows"][2]["tag_keys"], [])

    def test_tag_search_partial_and_filter_pagination(self):
        for search, expected in (("购物", 1), ("价格变化", 1), ("缺失", 1), ("风险项", 2)):
            with self.subTest(search=search):
                self.assertEqual(self.get_data(search=search)["total"], expected)
        self.assertEqual(self.get_data(search="德国")["total"], 1)
        self.assertEqual(self.get_data(metric="risk")["total"], 2)
        attention = self.get_data(metric="attention", page_size=1, page=2)
        self.assertEqual(attention["total"], 0)
        self.assertEqual(attention["total_pages"], 1)
        self.assertEqual(len(attention["rows"]), 0)
        self.assertTrue(all("LIKE" not in query for query, _ in self.connection.queries))

    def test_empty_day_has_no_phantom_risks(self):
        self.connection.records = []
        result = self.get_data()
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["summary"]["risk_count"], 0)
        self.assertEqual(result["summary"]["attention_count"], 0)
        self.assertIn("JOIN `asin_day_data`", self.connection.queries[0][0])

    def test_invalid_or_legacy_deal_values_serialize_as_empty(self):
        for raw in ("not-json", '{"type":"coupon"}', None):
            with self.subTest(raw=raw):
                row = web_app.serialize_day_row(
                    {"id": 1, "data_id": 1, "buy_box": 1, "deal": raw}, {}
                )
                self.assertEqual(row["deal"], [])

    def test_deal_column_migration_expands_short_existing_column(self):
        class MigrationCursor:
            def __init__(self, connection):
                self.connection = connection

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=None):
                self.connection.sql.append(sql)

            def fetchone(self):
                return {"data_type": "varchar"}

        class MigrationConnection:
            def __init__(self):
                self.sql = []
                self.commits = 0

            def cursor(self):
                return MigrationCursor(self)

            def commit(self):
                self.commits += 1

        connection = MigrationConnection()
        web_app.ensure_deal_column(connection)
        self.assertTrue(any("MODIFY COLUMN `deal` MEDIUMTEXT" in sql for sql in connection.sql))
        self.assertEqual(connection.commits, 1)


if __name__ == "__main__":
    unittest.main()
