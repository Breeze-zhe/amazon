import io
import sqlite3
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from openpyxl import Workbook

import web_app
from amazon_importer.order_data import HEADERS, order_dates, parse_workbook, query_orders


def workbook(rows=None, headers=None):
    book = Workbook()
    sheet = book.active
    sheet.append(list(HEADERS) if headers is None else headers)
    for row in rows if rows is not None else [excel_row()]:
        sheet.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    stream.seek(0)
    return stream


def excel_row(**overrides):
    row = dict(account="shop-US", asin="B012345678", asin_site=None, country="美国",
               product_name="相机", asin_country=None, order_day=datetime(2026, 1, 2, 23, 59),
               package_count=2, sku_count=3, order_status="Shipped", total_amount_cny="12.30")
    row.update(overrides)
    return [row[field] for field in HEADERS.values()]


class SqliteCursor:
    """Execute the production queries on an isolated DB, emulating MySQL lock calls."""
    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.db.cursor()
        self.synthetic = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def execute(self, sql, params=()):
        self.synthetic = None
        if "GET_LOCK" in sql:
            self.synthetic = {"acquired": int(self.connection.lock_available)}
            self.connection.locked = self.connection.lock_available
        elif "RELEASE_LOCK" in sql:
            self.connection.locked = False
            self.synthetic = {"released": 1}
        else:
            self.cursor.execute(sql.replace("%s", "?"), tuple(self.value(v) for v in params))

    @staticmethod
    def value(value):
        return str(value) if isinstance(value, (date, Decimal)) else value

    def executemany(self, sql, rows):
        if self.connection.fail_insert:
            self.execute(sql, rows[0])
            raise RuntimeError("injected write failure")
        self.cursor.executemany(sql.replace("%s", "?"), [tuple(self.value(v) for v in row) for row in rows])

    def fetchone(self):
        return self.synthetic if self.synthetic is not None else dict(self.cursor.fetchone())

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]


class OrderDatabase:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE amazon_data (id INTEGER PRIMARY KEY, name TEXT, operation TEXT, country_name TEXT,
                picture TEXT, asin TEXT, asin_country TEXT, link TEXT, status INTEGER);
            CREATE TABLE amz_order_asin_day (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT COLLATE NOCASE, asin TEXT,
                asin_site TEXT, country TEXT, product_name TEXT, asin_country TEXT COLLATE NOCASE, order_day TEXT,
                package_count INTEGER, sku_count INTEGER, order_status TEXT, total_amount_cny TEXT,
                UNIQUE (account, asin_country, order_day));
            CREATE TABLE amz_tag_group (id INTEGER PRIMARY KEY, group_name TEXT, status INTEGER, sort_no INTEGER);
            CREATE TABLE amz_tag (id INTEGER PRIMARY KEY, group_id INTEGER, tag_name TEXT, color TEXT, sort_no INTEGER, status INTEGER);
            CREATE TABLE amz_product_operation_log (id INTEGER PRIMARY KEY, asin TEXT, asin_country TEXT, tag_id INTEGER,
                remark TEXT, product_info TEXT, create_time TEXT, operate_date TEXT);
            INSERT INTO amz_tag_group VALUES (8, '商品操作类型', 1, 1);
            INSERT INTO amz_tag VALUES (901, 8, '修改图片', '#147d92', 1, 1);
            INSERT INTO amz_tag VALUES (902, 8, '调整价格', '#d46a00', 2, 1);
            INSERT INTO amazon_data VALUES (1, '相机', '小王', '美国', '/image.png', 'B012345678',
                'B012345678美国', 'https://www.amazon.com/dp/B012345678', 1);
            INSERT INTO amazon_data VALUES (2, '第二商品', '小李', '加拿大', '', 'B098765432', 'B098765432加拿大', '', 1);
            INSERT INTO amazon_data VALUES (3, '停用', '小王', '美国', '', 'B011111111', 'B011111111美国', '', 0);
        """)
        self.lock_available = True
        self.locked = False
        self.fail_insert = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return SqliteCursor(self)

    def begin(self):
        self.db.execute("BEGIN")

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def orders(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM amz_order_asin_day ORDER BY id")]


class OrderWorkbookTests(unittest.TestCase):
    def test_field_mapping_formula_fallback_and_decimals(self):
        row = parse_workbook(workbook([excel_row(asin_site='=CONCAT(B2,A2)', asin_country='=B2&D2')]))[0]
        self.assertEqual(row["asin_site"], "B012345678shop-US")
        self.assertEqual(row["asin_country"], "B012345678美国")
        self.assertEqual(row["order_day"], date(2026, 1, 2))
        self.assertEqual(row["package_count"], 2)
        self.assertEqual(row["total_amount_cny"], Decimal("12.30"))

    def test_headers_ignore_spaces_case_and_fullwidth_punctuation(self):
        headers = [" " + h.upper().replace("+", "＋").replace("(", "（").replace(")", "）") + " " for h in HEADERS]
        self.assertEqual(len(parse_workbook(workbook(headers=headers))), 1)

    def test_missing_and_duplicate_headers_rejected(self):
        for headers in (list(HEADERS)[1:], list(HEADERS) + [" ASIN "]):
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                parse_workbook(workbook(headers=headers))

    def test_optional_columns_can_be_missing(self):
        fields = [k for k, v in HEADERS.items() if v not in {"asin_site", "asin_country", "product_name"}]
        original = dict(zip(HEADERS, excel_row()))
        row = parse_workbook(workbook([[original[field] for field in fields]], fields))[0]
        self.assertEqual(row["asin_country"], "B012345678美国")

    def test_bad_rows_include_excel_row_number(self):
        for change in ({"package_count": -1}, {"package_count": 1.5}, {"package_count": "NaN"},
                       {"sku_count": 4294967296}, {"sku_count": None}, {"order_day": "not-a-date"},
                       {"order_day": True}, {"account": ""}, {"country": ""}, {"order_status": ""},
                       {"total_amount_cny": "1.234"}, {"total_amount_cny": "10000000000"},
                       {"asin_country": "B012345678加拿大"}, {"asin_site": "wrong"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "第 3 行"):
                parse_workbook(workbook([excel_row(), excel_row(**change)]))

    def test_blank_rows_ignored_empty_workbook_rejected(self):
        self.assertEqual(len(parse_workbook(workbook([[None] * 11, excel_row()]))), 1)
        with self.assertRaisesRegex(ValueError, "没有可导入"):
            parse_workbook(workbook([]))

    def test_excel_serial_and_local_date_without_timezone_conversion(self):
        self.assertEqual(parse_workbook(workbook([excel_row(order_day=46024)]))[0]["order_day"], date(2026, 1, 2))
        self.assertEqual(parse_workbook(workbook([excel_row(order_day="2026-01-02T00:01:00+08:00")]))[0]["order_day"], date(2026, 1, 2))


class OrderApiTests(unittest.TestCase):
    def setUp(self):
        self.connection = OrderDatabase()
        self.addCleanup(self.connection.db.close)
        patcher = patch.object(web_app, "connect", return_value=self.connection)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = web_app.app.test_client()

    def upload(self, rows=None):
        return self.client.post("/api/order-data/import", data={"file": (workbook(rows), "orders.xlsx")})

    def test_repeated_import_aggregates_all_orders_into_one_row(self):
        for _ in range(2):
            response = self.upload([excel_row(), excel_row()])
            self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["stats"]["replaced_rows"], 1)
        self.assertEqual(response.json["stats"]["input_rows"], 2)
        self.assertEqual(response.json["stats"]["inserted_rows"], 1)
        self.assertEqual(len(self.connection.orders()), 1)
        stored = self.connection.orders()[0]
        self.assertEqual(stored["package_count"], 4)
        self.assertEqual(stored["sku_count"], 6)
        self.assertEqual(Decimal(stored["total_amount_cny"]), Decimal("24.60"))
        result = self.client.get("/api/order-data").json
        self.assertEqual(result["rows"][0]["total_orders"], 4)

    def test_replacement_preserves_other_accounts_and_days_and_updates_status(self):
        self.upload([excel_row(), excel_row(account="other"), excel_row(order_day="2026-01-01")])
        self.upload([excel_row(order_status="Pending", package_count=5)])
        self.assertEqual(len(self.connection.orders()), 3)
        row = query_orders(self.connection, asin="B012345678")["rows"][0]
        self.assertEqual(row["total_orders"], 9)
        self.assertEqual(row["daily_orders"]["2026-01-02"], 7)

    def test_unknown_products_saved_and_reported(self):
        response = self.upload([excel_row(asin="B000000000")])
        self.assertEqual(response.json["stats"]["unmatched_rows"], 1)
        self.assertEqual(len(self.connection.orders()), 1)
        self.assertTrue(all(row["total_orders"] == 0 for row in query_orders(self.connection)["rows"]))

    def test_file_is_source_of_truth_without_status_or_refund_adjustments(self):
        statuses = ["Canceled", "Cancelled", "Refunded", "已退款", "Pending", "Shipped"]
        self.upload([excel_row(order_status=status) for status in statuses])
        self.assertEqual(query_orders(self.connection, asin="B012345678")["rows"][0]["total_orders"], 12)
        self.assertEqual(len(self.connection.orders()), 1)
        stored = self.connection.orders()[0]
        self.assertEqual(stored["order_status"], "Mixed")
        self.assertEqual(stored["package_count"], 12)

    def test_accounts_countries_and_dates_are_separate_groups(self):
        response = self.upload([excel_row(), excel_row(), excel_row(account="other"),
                                excel_row(country="加拿大"), excel_row(order_day="2026-01-01")])
        self.assertEqual(response.json["stats"]["input_rows"], 5)
        self.assertEqual(response.json["stats"]["inserted_rows"], 4)
        self.assertEqual(len(self.connection.orders()), 4)

    def test_case_insensitive_account_grouping_matches_database_uniqueness(self):
        self.assertEqual(self.upload([excel_row(account="SHOP-US"), excel_row(account="shop-US")]).status_code, 200)
        self.assertEqual(len(self.connection.orders()), 1)

    def test_aggregate_overflow_does_not_replace_existing_data(self):
        self.upload()
        original = self.connection.orders()
        response = self.upload([excel_row(package_count=4294967295), excel_row()])
        self.assertEqual(response.status_code, 400)
        self.assertIn("合并后", response.json["message"])
        self.assertEqual(self.connection.orders(), original)

    def test_uniqueness_rejects_duplicate_group_even_outside_importer(self):
        self.upload()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.db.execute("INSERT INTO amz_order_asin_day (account,asin_country,order_day) VALUES ('shop-US','B012345678美国','2026-01-02')")

    def test_date_window_global_latest_filters_and_zeros(self):
        self.upload([excel_row(order_day="2026-01-02"), excel_row(order_day="2025-12-04"),
                     excel_row(order_day="2025-12-03"), excel_row(asin="B000000000", order_day="2026-01-03", order_status="Cancelled")])
        result = self.client.get("/api/order-data?operation=小王&country_name=美国&asin=1234").json
        self.assertEqual(result["dates"], order_dates(date(2026, 1, 3)))
        self.assertEqual(len(result["dates"]), 30)
        self.assertEqual(result["dates"][-1], "2025-12-05")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["total_orders"], 2)
        self.assertEqual(result["rows"][0]["daily_orders"]["2026-01-03"], 0)
        self.assertEqual(self.client.get("/api/order-data?operation=小李&country_name=美国").json["rows"], [])

    def test_order_rows_include_operations_only_for_matching_product_and_day(self):
        self.upload([excel_row(order_day="2026-01-02")])
        self.connection.db.executemany(
            "INSERT INTO amz_product_operation_log (id,asin,asin_country,tag_id,remark,operate_date) VALUES (?,?,?,?,?,?)",
            [(1, "B012345678", "B012345678美国", 901, "更新图片", "2026-01-02"),
             (2, "B012345678", "B012345678美国", 902, "调整价格", "2026-01-02"),
             (3, "B012345678", "B012345678美国", 901, "前一天操作", "2026-01-01"),
             (4, "B012345678", "B012345678加拿大", 901, "其他国家", "2026-01-02"),
             (5, "B012345678", "B012345678美国", 901, "超出窗口", "2025-12-01")],
        )
        result = self.client.get("/api/order-data").json
        us_product = next(row for row in result["rows"] if row["asin_country"] == "B012345678美国")
        self.assertEqual(us_product["daily_orders"]["2026-01-01"], 0)
        self.assertEqual(us_product["daily_operations"]["2026-01-02"], [
            {"operation_type": "修改图片", "remark": "更新图片"},
            {"operation_type": "调整价格", "remark": "调整价格"},
        ])
        self.assertEqual(us_product["daily_operations"]["2026-01-01"], [
            {"operation_type": "修改图片", "remark": "前一天操作"},
        ])
        self.assertNotIn("2025-12-01", us_product["daily_operations"])
        unrelated_product = next(row for row in result["rows"] if row["asin_country"] == "B098765432加拿大")
        self.assertEqual(unrelated_product["daily_operations"], {})

    def test_empty_table_and_pagination(self):
        result = self.client.get("/api/order-data?page_size=1").json
        self.assertEqual(result["dates"], [])
        self.assertIsNone(result["latest_day"])
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["total_pages"], 2)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(self.client.get("/api/order-data?page_size=1&page=2").json["rows"][0]["id"], 1)

    def test_filter_options_only_include_active_products(self):
        result = self.client.get("/api/order-data/filters").json
        self.assertEqual(set(result["operations"]), {"小李", "小王"})
        self.assertEqual(set(result["sites"]), {"美国", "加拿大"})

    def test_latest_day_sort_before_pagination_sums_accounts_and_keeps_zero_orders_last(self):
        self.connection.db.execute("INSERT INTO amazon_data (id,asin,asin_country,operation,country_name,status) "
                                   "VALUES (4,'B044444444','B044444444美国','小王','美国',1)")
        self.connection.commit()
        self.upload([excel_row(package_count=2), excel_row(account="other", package_count=6),
                     excel_row(asin="B098765432", country="加拿大", package_count=7),
                     excel_row(asin="B098765432", country="加拿大", package_count=500, order_day="2026-01-01"),
                     excel_row(asin="B011111111", package_count=100)])
        pages = [self.client.get(f"/api/order-data?page_size=1&page={page}").json for page in (1, 2, 3)]
        self.assertEqual([result["rows"][0]["id"] for result in pages], [1, 2, 4])
        self.assertEqual([result["rows"][0]["daily_orders"]["2026-01-02"] for result in pages], [8, 7, 0])
        self.assertTrue(all(result["total"] == 3 for result in pages))
        self.assertGreater(pages[1]["rows"][0]["total_orders"], pages[0]["rows"][0]["total_orders"])
        filtered = self.client.get("/api/order-data?operation=小王&country_name=美国").json
        self.assertEqual([row["id"] for row in filtered["rows"]], [1, 4])

    def test_latest_day_sort_uses_stable_id_order_for_equal_quantities(self):
        self.upload([excel_row(package_count=5), excel_row(asin="B098765432", country="加拿大", package_count=5)])
        self.assertEqual([row["id"] for row in self.client.get("/api/order-data").json["rows"]], [2, 1])

    def test_invalid_file_rejects_whole_batch_before_deletion(self):
        self.upload()
        original = self.connection.orders()
        response = self.upload([excel_row(package_count=5), excel_row(sku_count=-1)])
        self.assertEqual(response.status_code, 400)
        self.assertIn("第 3 行", response.json["message"])
        self.assertEqual(self.connection.orders(), original)

    def test_write_failure_rolls_back_deleted_and_inserted_rows(self):
        self.upload()
        original = self.connection.orders()
        self.connection.fail_insert = True
        response = self.upload([excel_row(package_count=7)])
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.connection.orders(), original)
        self.assertFalse(self.connection.locked)

    def test_busy_import_returns_conflict_without_touching_data(self):
        self.upload()
        original = self.connection.orders()
        self.connection.lock_available = False
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(self.connection.orders(), original)

    def test_bad_extension_and_bad_archive_rejected(self):
        self.assertEqual(self.client.post("/api/order-data/import").status_code, 400)
        for name in ("orders.csv", "orders.xlsx"):
            response = self.client.post("/api/order-data/import", data={"file": (io.BytesIO(b"invalid"), name)})
            self.assertEqual(response.status_code, 400)

    def test_month_and_leap_year_window(self):
        self.assertEqual(order_dates(date(2024, 3, 1))[-1], "2024-02-01")
        self.assertEqual(order_dates(date(2026, 1, 1))[-1], "2025-12-03")

    def test_history_matches_exact_product_country_and_sums_accounts_in_inclusive_range(self):
        self.upload([excel_row(package_count=2, order_day="2026-01-01"),
                     excel_row(account="other", package_count=4, order_day="2026-01-01"),
                     excel_row(package_count=3, order_day="2026-01-03"),
                     excel_row(package_count=100, order_day="2025-12-31"),
                     excel_row(country="加拿大", package_count=80, order_day="2026-01-01"),
                     excel_row(asin="B012345679", package_count=70, order_day="2026-01-01")])
        response = self.client.get("/api/order-data/history", query_string={
            "asin_country": "B012345678美国", "start_date": "2026-01-01", "end_date": "2026-01-03"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["total_orders"], 9)
        self.assertEqual(response.json["points"], [
            {"date": "2026-01-01", "quantity": 6}, {"date": "2026-01-02", "quantity": 0},
            {"date": "2026-01-03", "quantity": 3}])

    def test_history_default_window_uses_global_latest_date(self):
        self.upload([excel_row(), excel_row(asin="B000000000", order_day="2026-02-01")])
        response = self.client.get("/api/order-data/history", query_string={"asin_country": "B012345678美国"})
        self.assertEqual(response.json["start_date"], "2026-01-03")
        self.assertEqual(response.json["end_date"], "2026-02-01")
        self.assertEqual(len(response.json["points"]), 30)
        self.assertEqual(response.json["total_orders"], 0)

    def test_history_empty_table_and_single_day_with_zero(self):
        response = self.client.get("/api/order-data/history", query_string={"asin_country": "B012345678美国"})
        self.assertEqual(response.json["points"], [])
        self.assertIsNone(response.json["start_date"])
        response = self.client.get("/api/order-data/history", query_string={
            "asin_country": "B012345678美国", "start_date": "2026-01-01", "end_date": "2026-01-01"})
        self.assertEqual(response.json["points"], [{"date": "2026-01-01", "quantity": 0}])

    def test_history_rejects_missing_key_incomplete_invalid_and_reversed_dates(self):
        for params in ({}, {"start_date": "2026-01-01", "end_date": "2026-01-02"},
                       {"asin_country": "key", "start_date": "2026-01-01"},
                       {"asin_country": "key", "start_date": "2026-02-30", "end_date": "2026-03-01"},
                       {"asin_country": "key", "start_date": "2026-03-01", "end_date": "2026-02-01"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/order-data/history", query_string=params).status_code, 400)


if __name__ == "__main__":
    unittest.main()
