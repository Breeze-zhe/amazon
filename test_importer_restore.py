import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from amazon_importer import importer


class ImporterRestoreTests(unittest.TestCase):
    def test_legacy_header_mappings_are_preserved(self):
        for header, column in importer.EXPLICIT_HEADER_TO_COLUMN.items():
            with self.subTest(header=header):
                mapping, unmapped = importer.build_column_mapping(
                    ["ASIN", "简称", header], ["asin", "country", column],
                )
                self.assertEqual(mapping[column], header)
                self.assertEqual(unmapped, [])

    def test_same_name_and_custom_alias_mappings_are_preserved(self):
        headers = ["ASIN", "简称", "custom_field", "自定义列"]
        with patch.dict(importer.COLUMN_ALIASES, {"custom_alias": ["自定义列"]}):
            mapping, unmapped = importer.build_column_mapping(
                headers, ["asin", "country", "custom_field", "custom_alias"],
            )
        self.assertEqual(mapping["custom_field"], "custom_field")
        self.assertEqual(mapping["custom_alias"], "自定义列")
        self.assertEqual(unmapped, [])

    def test_date_label_and_source_text_normalization(self):
        self.assertEqual(importer.normalize_db_value("select_date", "2026/10/13"), "2026-10-13")
        self.assertEqual(importer.normalize_db_value("label", " 新品链接 "), "新品链接")
        self.assertIsNone(importer.normalize_db_value("label", "未知标签"))
        self.assertEqual(importer.normalize_db_value("deal_date", "待定"), "待定")
        self.assertEqual(importer.normalize_db_value("senior_a", " 已完成 "), "已完成")

    def test_source_lifecycle_fields_do_not_override_active_defaults(self):
        frame = pd.DataFrame([{
            "ASIN": "A1", "简称": "G", "状态": "0", "淘汰月份": "2026/09/01",
        }])
        columns = ["asin", "country", "status", "obsolete_date", "link"]
        mapping, _ = importer.build_column_mapping(list(frame.columns), columns)
        rows, _ = importer.collect_candidate_rows(frame, mapping, columns)
        self.assertEqual(rows[0]["status"], 1)
        self.assertNotIn("obsolete_date", rows[0])
        self.assertEqual(rows[0]["link"], "https://www.amazon.de/dp/A1")

    def run_sync(self, connection, *, dry_run=False):
        frame = pd.DataFrame([
            {"ASIN": "A1", "简称": "U", "产品": "Updated"},
            {"ASIN": "NEW", "简称": "G", "产品": "New product"},
        ])
        columns = [
            importer.DbColumn(column, "")
            for column in ("asin", "country", "name", "status", "obsolete_date", "link")
        ]
        existing = {
            ("A1", "U"): {
                "asin": "A1", "country": "U", "name": "Old", "status": 0,
                "obsolete_date": date(2026, 9, 1),
                "link": "https://www.amazon.com/dp/A1",
            },
            ("OLD", "C"): {
                "asin": "OLD", "country": "C", "status": 1, "obsolete_date": None,
            },
        }
        with (
            patch.object(importer, "read_excel_first_sheet", return_value=frame),
            patch.object(importer, "fetch_columns", return_value=columns),
            patch.object(importer, "fetch_existing_rows", return_value=existing),
        ):
            return importer.import_excel_data(connection, dry_run=dry_run)

    def test_successful_sync_commits_once_after_updates_and_insert(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        stats, unmapped = self.run_sync(connection)

        self.assertEqual(stats.inserted_rows, 1)
        self.assertEqual(stats.matched_existing, 1)
        self.assertEqual(stats.updated_existing_rows, 1)
        self.assertEqual(stats.reactivated_rows, 1)
        self.assertEqual(stats.obsoleted_rows, 1)
        self.assertEqual(unmapped, [])
        self.assertEqual(cursor.execute.call_count, 2)
        cursor.executemany.assert_called_once()
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()

    def test_insert_failure_rolls_back_prior_updates_without_commit(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        cursor.executemany.side_effect = RuntimeError("insert failed")
        with self.assertRaisesRegex(RuntimeError, "insert failed"):
            self.run_sync(connection)

        self.assertEqual(cursor.execute.call_count, 2)
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()

    def test_dry_run_reports_all_changes_without_writes(self):
        connection = MagicMock()
        stats, _ = self.run_sync(connection, dry_run=True)
        self.assertEqual(stats.inserted_rows, 1)
        self.assertEqual(stats.updated_existing_rows, 1)
        self.assertEqual(stats.reactivated_rows, 1)
        self.assertEqual(stats.obsoleted_rows, 1)
        connection.cursor.assert_not_called()
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
