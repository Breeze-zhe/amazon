import unittest
from unittest.mock import patch

import web_app


class StatusTagCursor:
    def __init__(self, connection):
        self.connection = connection
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = tuple(params)
        self.connection.queries.append((sql, self.params))
        if sql.lstrip().startswith("UPDATE"):
            self.connection.updates.append((sql, self.params))

    def fetchone(self):
        if "SUM(CASE" in self.sql:
            return {key: 0 for key in web_app.BASIC_INFO_METRIC_CONDITIONS}
        if "COUNT(*) AS total" in self.sql:
            return {"total": 0}
        if self.sql.startswith("SELECT *"):
            return dict(self.connection.current)
        return dict(self.connection.current)

    def fetchall(self):
        if "FROM `amz_tag` t" in self.sql:
            return list(self.connection.tags)
        if "FROM `amz_tag_group` g" in self.sql:
            return list(self.connection.groups)
        return []


class StatusTagConnection:
    def __init__(self, *, current=None, tags=None, groups=None):
        self.current = {
            "id": 7,
            "product_status": None,
            "inventory_status": None,
            "ad_status": None,
            "promotion_status": None,
            **(current or {}),
        }
        self.tags = list(tags or [])
        self.groups = list(groups or [])
        self.queries = []
        self.updates = []
        self.commits = 0
        self.rollbacks = 0
        self.db_cursor = StatusTagCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.db_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def enabled_tag(tag_id=11, group_name="产品状态"):
    return {
        "id": tag_id,
        "tag_name": "在售",
        "color": "RGB(40,120,80)",
        "tag_status": 1,
        "group_name": group_name,
        "group_status": 1,
    }


class BasicInfoStatusTagApiTests(unittest.TestCase):
    def call_api(self, connection, method, path, **kwargs):
        with patch.object(web_app, "connect", return_value=connection), \
             patch.object(web_app, "database_config", return_value={}):
            return web_app.app.test_client().open(path, method=method, **kwargs)

    def test_options_return_tag_colors_and_missing_groups_as_empty(self):
        connection = StatusTagConnection(
            groups=[{
                "group_name": "产品状态",
                "group_status": 1,
                "id": 11,
                "tag_name": "在售",
                "color": "RGB(40,120,80)",
                "tag_status": 1,
            }]
        )

        response = self.call_api(connection, "GET", "/api/basic-info/status-tags")
        groups = response.get_json()["groups"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(groups["product_status"]["tags"][0]["color"], "RGB(40,120,80)")
        self.assertEqual(groups["product_status"]["tags"][0]["status"], 1)
        self.assertEqual(groups["inventory_status"], {"status": 0, "tags": []})

    def test_rejects_tag_from_another_group(self):
        connection = StatusTagConnection(tags=[enabled_tag(group_name="库存状态")])

        response = self.call_api(connection, "PATCH", "/api/basic-info/7", json={"product_status": 11})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(connection.updates)
        self.assertEqual(connection.rollbacks, 1)

    def test_rejects_new_disabled_tag_but_allows_unchanged_disabled_tag(self):
        inactive = {**enabled_tag(), "tag_status": 0}
        rejected = StatusTagConnection(tags=[inactive])
        response = self.call_api(rejected, "PATCH", "/api/basic-info/7", json={"product_status": 11})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(rejected.updates)

        retained = StatusTagConnection(current={"product_status": 11}, tags=[inactive])
        response = self.call_api(retained, "PATCH", "/api/basic-info/7", json={"product_status": 11})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(retained.updates[0][1], (11, 7))
        self.assertEqual(retained.commits, 1)

    def test_empty_status_clears_value_and_missing_group_rejects_tag(self):
        clearing = StatusTagConnection(current={"product_status": 11})
        response = self.call_api(clearing, "PATCH", "/api/basic-info/7", json={"product_status": ""})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(clearing.updates[0][1], (None, 7))

        missing = StatusTagConnection()
        response = self.call_api(missing, "PATCH", "/api/basic-info/7", json={"product_status": 99})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(missing.updates)

    def test_old_columns_are_absent_from_basic_info_query_and_edit_contract(self):
        connection = StatusTagConnection()
        with patch.object(web_app, "connect", return_value=connection), \
             patch.object(web_app, "database_config", return_value={}):
            response = web_app.app.test_client().get("/api/basic-info")

        self.assertEqual(response.status_code, 200)
        sql = " ".join(statement for statement, _ in connection.queries)
        self.assertIn("`product_status`", sql)
        self.assertNotIn("`label`", sql)
        self.assertNotIn("`ad_activity`", sql)
        self.assertNotIn("ad_inactive", response.get_json()["summary"])


if __name__ == "__main__":
    unittest.main()
