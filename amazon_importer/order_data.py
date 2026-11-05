"""Order workbook validation, transactional replacement, and 30-day reporting."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from flask import jsonify, request
from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel


HEADERS = {
    "账号": "account", "ASIN": "asin", "ASIN+站点": "asin_site",
    "国家": "country", "中文名": "product_name", "辅助列": "asin_country",
    "下单时间": "order_day", "包裹数量": "package_count", "sku数量": "sku_count",
    "平台订单状态": "order_status", "订单总金额(元)": "total_amount_cny",
}
OPTIONAL = {"asin_site", "product_name", "asin_country"}
LIMITS = {"account": 32, "asin": 20, "asin_site": 64, "country": 100,
          "product_name": 255, "asin_country": 512, "order_status": 32}
IMPORT_LOCK = "amazon_data_view:order_import"


def text_cell(value):
    return "" if value is None else str(value).strip()


def header_key(value):
    return re.sub(r"\s+", "", text_cell(value)).lower().translate(str.maketrans("＋（）", "+()"))


def order_dates(latest):
    if not latest:
        return []
    end = latest if isinstance(latest, date) else date.fromisoformat(str(latest)[:10])
    return [(end - timedelta(days=offset)).isoformat() for offset in range(30)]


def aggregate_orders(records):
    """One stored row per account, product/country and day, including mixed statuses."""
    groups = {}
    for record in records:
        key = (record["account"].casefold(), record["asin_country"].casefold(), record["order_day"])
        if key not in groups:
            groups[key] = {**record, "package_count": 0, "sku_count": 0,
                           "total_amount_cny": Decimal("0.00"), "_statuses": set()}
        group = groups[key]
        status = re.sub(r"\s+", "", record["order_status"]).lower()
        group["_statuses"].add(status)
        for field in ("package_count", "sku_count", "total_amount_cny"):
            group[field] += record[field]
        if not group["product_name"] and record["product_name"]:
            group["product_name"] = record["product_name"]
    result = list(groups.values())
    for group in result:
        statuses = group.pop("_statuses")
        if len(statuses) > 1:
            group["order_status"] = "Mixed"
        if (group["package_count"] > 4294967295 or group["sku_count"] > 4294967295 or
                abs(group["total_amount_cny"]) >= Decimal("10000000000")):
            raise ValueError(f"合并后数值超出数据库范围：{group['account']} / {group['asin_country']} / {group['order_day']}")
    return result


def migrate_order_schema(connection):
    """Enforce uniqueness without rewriting existing order data."""
    locked = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 10) AS acquired", (IMPORT_LOCK,))
            locked = (cursor.fetchone() or {}).get("acquired") == 1
            if not locked:
                raise BlockingIOError("订单导入正在进行，暂不能更新表结构")
            cursor.execute("SELECT account, asin_country, order_day, COUNT(*) AS total FROM amz_order_asin_day "
                           "GROUP BY account, asin_country, order_day HAVING COUNT(*) > 1 LIMIT 1")
            if cursor.fetchone():
                raise ValueError("订单表已有重复分组，须先合并历史数据再创建唯一索引；本次未修改表结构")
            cursor.execute("SHOW INDEX FROM amz_order_asin_day")
            indexes = {row["Key_name"] for row in cursor.fetchall()}
            changes = []
            if "uq_order_account_asin_country_day" not in indexes:
                changes.append("ADD UNIQUE KEY uq_order_account_asin_country_day (account, asin_country, order_day)")
            if changes:
                cursor.execute("ALTER TABLE amz_order_asin_day " + ", ".join(changes))
            connection.commit()
            return {"changed": bool(changes)}
    finally:
        if locked:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (IMPORT_LOCK,))


def parse_workbook(stream):
    try:
        book = load_workbook(stream, read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("无法读取 .xlsx 文件，请检查文件格式和内容") from exc
    try:
        sheet = book.worksheets[0]
        iterator = sheet.iter_rows(values_only=True)
        headers = next(iterator, ())
        lookup = {}
        for i, value in enumerate(headers):
            key = header_key(value)
            if key:
                if key in lookup:
                    raise ValueError(f"表头重复：{value}")
                lookup[key] = i
        mapping = {field: lookup[header_key(label)] for label, field in HEADERS.items()
                   if header_key(label) in lookup}
        missing = [label for label, field in HEADERS.items() if field not in OPTIONAL and field not in mapping]
        if missing:
            raise ValueError("缺少表头：" + "、".join(missing))
        records = []
        for row_number, values in enumerate(iterator, 2):
            if all(not text_cell(value) for value in values):
                continue
            raw = {field: values[index] if index < len(values) else None for field, index in mapping.items()}
            try:
                row = {field: text_cell(raw.get(field)) for field in LIMITS}
                for field in ("account", "asin", "country", "order_status"):
                    if not row[field]:
                        raise ValueError(f"{next(k for k, v in HEADERS.items() if v == field)}不能为空")
                row["asin"] = row["asin"].upper()
                for field, expected in (("asin_country", row["asin"] + row["country"]),
                                        ("asin_site", row["asin"] + row["account"])):
                    if row[field] and row[field] != expected:
                        raise ValueError(f"{next(k for k, v in HEADERS.items() if v == field)}与 ASIN、国家或账号不一致")
                    row[field] = expected
                for field, limit in LIMITS.items():
                    if len(row[field]) > limit:
                        raise ValueError(f"{field} 超过 {limit} 字符")
                value = raw.get("order_day")
                if isinstance(value, bool) or value is None:
                    raise ValueError("下单时间无效")
                if isinstance(value, (int, float)):
                    if value < 1:
                        raise ValueError("下单时间无效")
                    value = from_excel(value, book.epoch)
                if isinstance(value, datetime):
                    value = value.date()
                if not isinstance(value, date):
                    value = datetime.fromisoformat(text_cell(value).replace("/", "-")).date()
                if value.year < 1000:
                    raise ValueError("下单时间超出数据库日期范围")
                row["order_day"] = value
                for field in ("package_count", "sku_count", "total_amount_cny"):
                    number = Decimal(text_cell(raw.get(field)).replace(",", ""))
                    if not number.is_finite():
                        raise ValueError(f"{field} 必须为有效数字")
                    if field == "total_amount_cny":
                        if abs(number) >= Decimal("10000000000") or number != number.quantize(Decimal("0.01")):
                            raise ValueError("订单总金额须在字段范围内且最多两位小数")
                        row[field] = number
                    else:
                        if number < 0 or number > 4294967295 or number != number.to_integral_value():
                            raise ValueError(f"{field} 必须为字段范围内的非负整数")
                        row[field] = int(number)
                records.append(row)
            except (ValueError, InvalidOperation, OverflowError, TypeError) as exc:
                raise ValueError(f"Excel 第 {row_number} 行：{exc or '日期或数量格式无效'}") from exc
        if not records:
            raise ValueError("文件没有可导入的数据")
        return records
    finally:
        book.close()


def replace_orders(connection, records):
    """Serialize all imports; replace only complete (account, day) scopes."""
    # Validate aggregate bounds before any deletion, and retain all original totals.
    grouped = aggregate_orders(records)
    locked = False
    lock_name = IMPORT_LOCK
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 10) AS acquired", (lock_name,))
            locked = (cursor.fetchone() or {}).get("acquired") == 1
            if not locked:
                raise BlockingIOError("另一个订单导入正在进行，请稍后重试")
            connection.begin()
            cursor.execute("SELECT DISTINCT asin_country FROM amazon_data WHERE asin_country IS NOT NULL")
            known = {text_cell(row["asin_country"]) for row in cursor.fetchall()}
            scopes = sorted({(row["account"], row["order_day"]) for row in records})
            replaced = 0
            for account, day in scopes:
                cursor.execute("DELETE FROM amz_order_asin_day WHERE account = %s AND order_day = %s", (account, day))
                replaced += cursor.rowcount
            fields = list(HEADERS.values())
            cursor.executemany(
                "INSERT INTO amz_order_asin_day (" + ", ".join(fields) + ") VALUES (" + ", ".join(["%s"] * len(fields)) + ")",
                [tuple(row[field] for field in fields) for row in grouped],
            )
            connection.commit()
            days = [row["order_day"] for row in records]
            return {"input_rows": len(records), "inserted_rows": len(grouped), "replaced_rows": replaced,
                    "unmatched_rows": sum(row["asin_country"] not in known for row in records),
                    "scope_count": len(scopes), "date_start": min(days).isoformat(), "date_end": max(days).isoformat()}
    except Exception:
        if locked:
            connection.rollback()
        raise
    finally:
        if locked:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))


def query_orders(connection, *, page=1, page_size=50, operation="", asin="", country_name=""):
    conditions, params = ["a.status = 1"], []
    for field, value in (("operation", operation), ("country_name", country_name)):
        if value:
            conditions.append(f"TRIM(a.{field}) = %s")
            params.append(value)
    if asin:
        conditions.append("a.asin LIKE %s")
        params.append("%" + asin + "%")
    where = " WHERE " + " AND ".join(conditions)
    with connection.cursor() as cursor:
        cursor.execute("SELECT MAX(order_day) AS latest FROM amz_order_asin_day")
        dates = order_dates(cursor.fetchone()["latest"])
        cursor.execute("SELECT COUNT(*) AS total FROM amazon_data a" + where, params)
        total = int(cursor.fetchone()["total"])
        # Aggregate across accounts and sort all matching products before pagination.
        cursor.execute(
            "SELECT a.id, a.name, a.operation, a.country_name, a.picture, a.asin, a.asin_country, a.link "
            "FROM amazon_data a LEFT JOIN ("
            "SELECT asin_country, SUM(package_count) AS quantity FROM amz_order_asin_day "
            "WHERE order_day = %s GROUP BY asin_country"
            ") latest ON latest.asin_country = a.asin_country" + where +
            " ORDER BY COALESCE(latest.quantity, 0) DESC, a.id DESC LIMIT %s OFFSET %s",
            [dates[0] if dates else None, *params, page_size, (page - 1) * page_size],
        )
        products = cursor.fetchall()
        keys = sorted({row["asin_country"] for row in products if row["asin_country"]})
        daily = {}
        operations = {}
        if dates and keys:
            cursor.execute(
                "SELECT asin_country, order_day, SUM(package_count) AS quantity FROM amz_order_asin_day "
                "WHERE order_day BETWEEN %s AND %s AND asin_country IN (" + ",".join(["%s"] * len(keys)) + ") "
                "GROUP BY asin_country, order_day", [dates[-1], dates[0], *keys],
            )
            for record in cursor.fetchall():
                daily[(record["asin_country"], str(record["order_day"])[:10])] = int(record["quantity"] or 0)
            cursor.execute(
                "SELECT l.asin_country, l.operate_date, t.tag_name AS operation_type, l.remark "
                "FROM amz_product_operation_log l "
                "JOIN amz_tag t ON t.id = l.tag_id "
                "JOIN amz_tag_group g ON g.id = t.group_id AND g.group_name = %s "
                "WHERE l.operate_date BETWEEN %s AND %s AND l.asin_country IN (" +
                ",".join(["%s"] * len(keys)) + ") ORDER BY l.operate_date, l.id",
                ["商品操作类型", dates[-1], dates[0], *keys],
            )
            for record in cursor.fetchall():
                key = (record["asin_country"], str(record["operate_date"])[:10])
                operations.setdefault(key, []).append({
                    "operation_type": record["operation_type"],
                    "remark": record["remark"] or "",
                })
        for row in products:
            row["daily_orders"] = {day: daily.get((row["asin_country"], day), 0) for day in dates}
            row["daily_operations"] = {
                day: operations[(row["asin_country"], day)]
                for day in dates if (row["asin_country"], day) in operations
            }
            row["total_orders"] = sum(row["daily_orders"].values())
    return {"success": True, "rows": products, "dates": dates, "latest_day": dates[0] if dates else None,
            "total": total, "page": page, "page_size": page_size, "total_pages": max(1, (total + page_size - 1) // page_size)}


def register_order_routes(app, connection_factory):
    @app.get("/api/order-data/history")
    def order_history():
        asin_country = request.args.get("asin_country", "").strip()
        start_text = request.args.get("start_date", "").strip()
        end_text = request.args.get("end_date", "").strip()
        if not asin_country or len(asin_country) > 512:
            return jsonify(success=False, message="请提供有效的 ASIN＋国家"), 400
        try:
            if bool(start_text) != bool(end_text):
                raise ValueError("请选择开始日期和结束日期")
            start = date.fromisoformat(start_text) if start_text else None
            end = date.fromisoformat(end_text) if end_text else None
            if start and start > end:
                raise ValueError("开始日期不能晚于结束日期")
        except ValueError as exc:
            return jsonify(success=False, message=f"日期范围无效：{exc}"), 400
        try:
            with connection_factory() as connection:
                with connection.cursor() as cursor:
                    if not start:
                        cursor.execute("SELECT MAX(order_day) AS latest FROM amz_order_asin_day")
                        latest = cursor.fetchone()["latest"]
                        if latest:
                            end = date.fromisoformat(str(latest)[:10])
                            start = end - timedelta(days=29)
                    points = []
                    if start:
                        cursor.execute(
                            "SELECT order_day, SUM(package_count) AS quantity FROM amz_order_asin_day "
                            "WHERE asin_country = %s AND order_day BETWEEN %s AND %s "
                            "GROUP BY order_day ORDER BY order_day", (asin_country, start, end),
                        )
                        daily = {str(row["order_day"])[:10]: int(row["quantity"] or 0) for row in cursor.fetchall()}
                        points = [{"date": (start + timedelta(days=offset)).isoformat(),
                                   "quantity": daily.get((start + timedelta(days=offset)).isoformat(), 0)}
                                  for offset in range((end - start).days + 1)]
            return jsonify(success=True, asin_country=asin_country,
                           start_date=start.isoformat() if start else None,
                           end_date=end.isoformat() if end else None,
                           total_orders=sum(point["quantity"] for point in points), points=points)
        except Exception as exc:
            return jsonify(success=False, message=str(exc)), 500

    @app.get("/api/order-data")
    def order_data():
        try:
            with connection_factory() as connection:
                return jsonify(query_orders(
                    connection, page=max(1, request.args.get("page", 1, type=int)),
                    page_size=min(200, max(1, request.args.get("page_size", 50, type=int))),
                    **{key: request.args.get(key, "").strip() for key in ("operation", "asin", "country_name")},
                ))
        except Exception as exc:
            return jsonify(success=False, message=str(exc)), 500

    @app.get("/api/order-data/filters")
    def order_filters():
        try:
            with connection_factory() as connection:
                with connection.cursor() as cursor:
                    result = {"success": True}
                    for field, key in (("operation", "operations"), ("country_name", "sites")):
                        cursor.execute(f"SELECT DISTINCT TRIM({field}) AS value FROM amazon_data WHERE status = 1 "
                                       f"AND {field} IS NOT NULL AND TRIM({field}) <> '' ORDER BY value")
                        result[key] = [row["value"] for row in cursor.fetchall()]
            return jsonify(result)
        except Exception as exc:
            return jsonify(success=False, message=str(exc)), 500

    @app.post("/api/order-data/import")
    def order_import():
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename or not uploaded.filename.lower().endswith(".xlsx"):
            return jsonify(success=False, message="请选择 .xlsx 订单文件"), 400
        try:
            records = parse_workbook(uploaded)
        except ValueError as exc:
            return jsonify(success=False, message=str(exc)), 400
        try:
            with connection_factory() as connection:
                stats = replace_orders(connection, records)
            return jsonify(success=True, stats=stats)
        except BlockingIOError as exc:
            return jsonify(success=False, message=str(exc)), 409
        except ValueError as exc:
            return jsonify(success=False, message=str(exc)), 400
        except Exception as exc:
            return jsonify(success=False, message=str(exc)), 500
