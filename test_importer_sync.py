import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from amazon_importer import importer


DB_COLUMNS = [
    importer.DbColumn("id", "auto_increment"),
    importer.DbColumn("asin", ""),
    importer.DbColumn("country", ""),
    importer.DbColumn("name", ""),
    importer.DbColumn("link", ""),
    importer.DbColumn("status", ""),
    importer.DbColumn("obsolete_date", ""),
]


class TrackingConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ImporterSyncTests(unittest.TestCase):
    def test_existing_row_updates_nonblank_fields_and_reactivates(self):
        updates, imported_changed, reactivated = importer.build_existing_updates(
            {
                "asin": "A1",
                "country": "U",
                "name": None,
                "link": "https://www.amazon.com/dp/A1",
                "status": 1,
            },
            {
                "asin": "A1",
                "country": "U",
                "name": "Keep me",
                "link": "old link",
                "status": 0,
                "obsolete_date": date(2026, 9, 1),
            },
        )

        self.assertNotIn("name", updates)
        self.assertEqual(updates["link"], "https://www.amazon.com/dp/A1")
        self.assertEqual(updates["status"], 1)
        self.assertIsNone(updates["obsolete_date"])
        self.assertTrue(imported_changed)
        self.assertTrue(reactivated)

    def test_new_candidate_is_active(self):
        frame = pd.DataFrame([{"ASIN": "NEW1", "简称": "U", "产品": "New"}])
        mapping, _ = importer.build_column_mapping(
            list(frame.columns),
            [column.name for column in DB_COLUMNS],
        )
        rows, _ = importer.collect_candidate_rows(
            frame,
            mapping,
            [column.name for column in DB_COLUMNS],
        )

        self.assertEqual(rows[0]["status"], 1)
        self.assertEqual(rows[0]["asin"], "NEW1")
        self.assertEqual(rows[0]["country"], "U")

    def test_obsolete_update_sets_first_date_and_preserves_existing_date(self):
        today = date(2026, 10, 12)
        updates, newly_obsoleted = importer.build_obsolete_updates(
            {"status": 1, "obsolete_date": None},
            today,
        )
        self.assertEqual(updates, {"status": 0, "obsolete_date": today})
        self.assertTrue(newly_obsoleted)

        updates, newly_obsoleted = importer.build_obsolete_updates(
            {"status": 0, "obsolete_date": date(2026, 9, 1)},
            today,
        )
        self.assertEqual(updates, {})
        self.assertFalse(newly_obsoleted)

    def test_dry_run_reports_update_reactivate_and_obsolete_without_writes(self):
        frame = pd.DataFrame([
            {"ASIN": "A1", "简称": "U", "产品": "New name"},
            {"ASIN": "B2", "简称": "G", "产品": None},
        ])
        existing = {
            ("A1", "U"): {
                "asin": "A1", "country": "U", "name": "Old name", "status": 1,
                "obsolete_date": None, "link": "https://www.amazon.com/dp/A1",
            },
            ("B2", "G"): {
                "asin": "B2", "country": "G", "name": "Keep me", "status": 0,
                "obsolete_date": date(2026, 9, 1), "link": "https://www.amazon.de/dp/B2",
            },
            ("C3", "C"): {
                "asin": "C3", "country": "C", "name": "Missing", "status": 1,
                "obsolete_date": None, "link": "https://www.amazon.ca/dp/C3",
            },
        }
        connection = TrackingConnection()

        with (
            patch.object(importer, "read_excel_first_sheet", return_value=frame),
            patch.object(importer, "fetch_columns", return_value=DB_COLUMNS),
            patch.object(importer, "fetch_existing_rows", return_value=existing),
        ):
            stats, _ = importer.import_excel_data(connection, dry_run=True)

        self.assertEqual(stats.matched_existing, 2)
        self.assertEqual(stats.inserted_rows, 0)
        self.assertEqual(stats.updated_existing_rows, 1)
        self.assertEqual(stats.reactivated_rows, 1)
        self.assertEqual(stats.obsoleted_rows, 1)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_empty_valid_source_is_rejected_without_commit(self):
        connection = TrackingConnection()
        frame = pd.DataFrame(columns=["ASIN", "简称", "产品"])
        with (
            patch.object(importer, "read_excel_first_sheet", return_value=frame),
            patch.object(importer, "fetch_columns", return_value=DB_COLUMNS),
        ):
            with self.assertRaisesRegex(RuntimeError, r"no valid ASIN \+ country"):
                importer.import_excel_data(connection)

        self.assertEqual(connection.commits, 0)

    def test_missing_required_source_header_is_rejected(self):
        connection = TrackingConnection()
        frame = pd.DataFrame([{"ASIN": "A1", "产品": "Name"}])
        with (
            patch.object(importer, "read_excel_first_sheet", return_value=frame),
            patch.object(importer, "fetch_columns", return_value=DB_COLUMNS),
        ):
            with self.assertRaisesRegex(RuntimeError, "missing required headers: 简称"):
                importer.import_excel_data(connection)

        self.assertEqual(connection.commits, 0)

    def test_write_failure_rolls_back_the_sync(self):
        connection = TrackingConnection()
        frame = pd.DataFrame([{"ASIN": "A1", "简称": "U", "产品": "Name"}])
        existing = {
            ("A1", "U"): {
                "asin": "A1", "country": "U", "name": "Old", "status": 1,
                "obsolete_date": None, "link": "https://www.amazon.com/dp/A1",
            }
        }
        with (
            patch.object(importer, "read_excel_first_sheet", return_value=frame),
            patch.object(importer, "fetch_columns", return_value=DB_COLUMNS),
            patch.object(importer, "fetch_existing_rows", return_value=existing),
            patch.object(
                importer,
                "update_existing_imported_fields",
                side_effect=RuntimeError("write failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                importer.import_excel_data(connection)

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
