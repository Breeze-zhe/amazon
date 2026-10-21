import unittest

import pandas as pd

from amazon_importer.importer import build_column_mapping, collect_candidate_rows
import web_app
from unittest.mock import patch


class BasicInfoCursor:
    def __init__(self):
        self.sql = ""
        self.params = []
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        self.queries.append((sql, params))

    def fetchone(self):
        if "SUM(CASE" in self.sql:
            return {
                "senior_a_missing": 0,
                "deal_active": 0,
                "ad_inactive": 0,
                "title_unmodified": 0,
            }
        return {"total": 1}

    def fetchall(self):
        return [{"id": 1, "name": "产品A", "operation": "小王"}]


class BasicInfoConnection:
    def __init__(self):
        self.db_cursor = BasicInfoCursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.db_cursor


class OperationImportTests(unittest.TestCase):
    def test_operation_header_maps_and_is_collected(self):
        headers = ["ASIN", "简称", "运营", "价格"]
        db_columns = ["asin", "country", "operation"]
        mapping, unmapped = build_column_mapping(headers, db_columns)

        self.assertEqual(mapping["operation"], "运营")
        self.assertEqual(unmapped, ["价格"])

        rows, stats = collect_candidate_rows(
            pd.DataFrame(
                [{"ASIN": "B0TEST1234", "简称": "UQM", "运营": "小王", "价格": "8"}]
            ),
            mapping,
            db_columns,
        )

        self.assertEqual(rows[0]["operation"], "小王")
        self.assertEqual(rows[0]["country"], "M")
        self.assertEqual(stats.prepared_rows, 1)

    def test_basic_info_returns_and_searches_operation(self):
        connection = BasicInfoConnection()
        with patch.object(web_app, "connect", return_value=connection):
            response = web_app.app.test_client().get("/api/basic-info?search=%E5%B0%8F%E7%8E%8B")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["rows"][0]["operation"], "小王")
        self.assertEqual(
            web_app.DISPLAY_COLUMNS[1:3],
            [("name", "产品"), ("operation", "运营")],
        )
        count_query = connection.db_cursor.queries[1][0]
        self.assertIn("`operation`", count_query)


if __name__ == "__main__":
    unittest.main()
