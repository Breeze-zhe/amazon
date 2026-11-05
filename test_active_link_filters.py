import unittest
from unittest.mock import patch

import web_app
from amazon_importer import day_crawler
from amazon_importer.importer import ImportStats


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self.sql = sql
        self.connection.queries.append((sql, params))

    def fetchall(self):
        return []

    def fetchone(self):
        if "COUNT(*) AS total" in self.sql:
            return {"total": 0}
        if "latest_date" in self.sql:
            return {"latest_date": None}
        return {
            "senior_a_missing": 0,
            "deal_active": 0,
            "title_unmodified": 0,
            "pending_order": 0,
            "adjustment_amount": 0,
            "overdue_reassignment": 0,
            "stocking_method": 0,
        }


class RecordingConnection:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return RecordingCursor(self)


class ActiveLinkFilterTests(unittest.TestCase):
    def test_explicit_crawl_target_still_requires_active_status(self):
        connection = RecordingConnection()
        day_crawler.fetch_targets(connection, data_id=42)

        sql, params = connection.queries[0]
        self.assertIn("status = 1", sql)
        self.assertIn("id = %s", sql)
        self.assertEqual(params, [42])

    def test_basic_info_and_operations_only_query_active_links(self):
        connection = RecordingConnection()
        with patch.object(web_app, "connect", return_value=connection):
            basic_response = web_app.app.test_client().get("/api/basic-info")
            operations_response = web_app.app.test_client().get("/api/basic-info/operations")

        self.assertEqual(basic_response.status_code, 200)
        self.assertEqual(operations_response.status_code, 200)
        sql_text = "\n".join(sql for sql, _ in connection.queries)
        self.assertIn("WHERE `status` = 1", sql_text)

    def test_trend_and_stock_history_join_active_products(self):
        trend_connection = RecordingConnection()
        with patch.object(web_app, "connect", return_value=trend_connection):
            response = web_app.app.test_client().get("/api/day-data/trend?data_id=42")
        self.assertEqual(response.status_code, 200)
        self.assertIn("a.status = 1", trend_connection.queries[0][0])

        history_connection = RecordingConnection()
        with patch.object(web_app, "connect", return_value=history_connection):
            response = web_app.app.test_client().get("/api/stock-data/history?seller_sku=SKU-1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("a.`status` = 1", history_connection.queries[0][0])

    def test_stock_list_and_summary_only_use_active_products(self):
        connection = RecordingConnection()
        with patch.object(web_app, "connect", return_value=connection):
            response = web_app.app.test_client().get("/api/stock-data")

        self.assertEqual(response.status_code, 200, response.get_json())
        sql_text = "\n".join(sql for sql, _ in connection.queries)
        self.assertIn("a_latest.`status` = 1", sql_text)
        self.assertIn("a.`status` = 1", sql_text)

    def test_refresh_response_exposes_lifecycle_stats(self):
        stats = ImportStats(
            total_rows=4,
            prepared_rows=4,
            inserted_rows=1,
            matched_existing=3,
            updated_existing_rows=1,
            obsoleted_rows=1,
            reactivated_rows=1,
        )
        with (
            patch.object(web_app, "connect", return_value=RecordingConnection()),
            patch.object(web_app, "import_excel_data", return_value=(stats, [])),
        ):
            response = web_app.app.test_client().post("/api/basic-info/refresh")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["stats"]
        self.assertEqual(payload["obsoleted_rows"], 1)
        self.assertEqual(payload["reactivated_rows"], 1)


if __name__ == "__main__":
    unittest.main()
