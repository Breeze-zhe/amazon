import unittest
from datetime import date

from flask import Flask

from amazon_importer.product_operation_logs import parse_log_payload


class ProductOperationPayloadTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def parse(self, payload):
        with self.app.app_context():
            return parse_log_payload(payload)

    def test_valid_payload_trims_values_and_parses_iso_date(self):
        values, error = self.parse({"asin_country": " ASIN美国 ", "tag_id": "12", "remark": " 描述 ", "operate_date": "2026-11-03"})
        self.assertIsNone(error)
        self.assertEqual(values, {"asin_country": "ASIN美国", "tag_id": 12, "remark": "描述", "operate_date": date(2026, 11, 3)})

    def test_missing_or_invalid_payload_fields_are_rejected(self):
        cases = [None, {}, {"asin_country": "", "tag_id": 12, "operate_date": "2026-11-03"},
                 {"asin_country": "ASIN美国", "tag_id": "bad", "operate_date": "2026-11-03"},
                 {"asin_country": "ASIN美国", "tag_id": 12, "operate_date": "2026-02-30"},
                 {"asin_country": "x" * 101, "tag_id": 12, "operate_date": "2026-11-03"},
                 {"asin_country": "ASIN美国", "tag_id": 12, "remark": "x" * 4001, "operate_date": "2026-11-03"}]
        for payload in cases:
            with self.subTest(payload=payload):
                values, error = self.parse(payload)
                self.assertIsNone(values)
                self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
