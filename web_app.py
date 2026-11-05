from __future__ import annotations

import os
import re
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request

from amazon_importer.importer import (
    DEFAULT_DATABASE,
    DEFAULT_EXCEL_PATH,
    DEFAULT_HOST,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_TABLE,
    DEFAULT_USER,
    connect,
    fetch_columns,
    import_excel_data,
    insertable_columns,
    quote_identifier,
)
from amazon_importer.day_crawler import (
    crawl_amazon_day_data,
    ensure_re_pull_column,
    fetch_targets,
    price_matches_site,
    fetch_completed_day_ids,
    is_hard_block_message,
)
from amazon_importer.crawl_jobs import CrawlJobs, CrawlBusy, crawl_lease
from amazon_importer.crawl_policy import compensation_browser_options


BASE_DIR = Path(__file__).resolve().parent
crawl_jobs = CrawlJobs(BASE_DIR / '.crawl_jobs')
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)


DISPLAY_COLUMNS = [
    ("id", "ID"),
    ("name", "产品"),
    ("operation", "运营"),
    ("picture", "图床"),
    ("asin", "ASIN"),
    ("link", "链接"),
    ("country_name", "国家"),
    ("product_status", "产品状态"),
    ("inventory_status", "库存状态"),
    ("ad_status", "广告状态"),
    ("promotion_status", "促销状态"),
    ("deal_date", "活动开始日期"),
    ("deal_end_date", "活动结束日期"),
    ("stock_remark", "库存备注"),
    ("status", "状态"),
    ("remark", "销售建议"),
]

BASIC_INFO_RECORD_COLUMNS = [
    "id",
    "name",
    "operation",
    "picture",
    "asin",
    "seller_sku",
    "country",
    "country_name",
    "product_status",
    "inventory_status",
    "ad_status",
    "promotion_status",
    "title",
    "point",
    "senior_a",
    "group",
    "select_date",
    "obsolete_date",
    "status",
    "remark",
    "deal",
    "deal_date",
    "deal_end_date",
    "asin_country",
    "link",
    "stock_remark",
]

STATUS_TAG_GROUPS = {
    "product_status": "产品状态",
    "inventory_status": "库存状态",
    "ad_status": "广告状态",
    "promotion_status": "促销状态",
}

BASIC_INFO_METRIC_CONDITIONS = {
    "senior_a_missing": "TRIM(COALESCE(`senior_a`, '')) = '无'",
    "deal_active": "TRIM(COALESCE(`deal`, '')) IN ('是', '开启', '已开启')",
    "title_unmodified": "TRIM(COALESCE(`title`, '')) = '未修改'",
}


EDITABLE_BASIC_FIELDS = {
    "name",
    "seller_sku",
    "country_name",
    "title",
    "point",
    "senior_a",
    "group",
    "select_date",
    "obsolete_date",
    "status",
    "remark",
    "deal",
    "deal_date",
    "deal_end_date",
    *STATUS_TAG_GROUPS,
    "stock_remark",
}

TEXT_EDITABLE_FIELDS = {
    "name",
    "seller_sku",
    "country_name",
    "group",
    "remark",
    "title",
    "point",
    "senior_a",
    "deal",
    "deal_date",
    "deal_end_date",
    "stock_remark",
}

DATE_EDITABLE_FIELDS = {"select_date", "obsolete_date"}
BOOLEAN_EDITABLE_FIELDS: set[str] = set()
INTEGER_EDITABLE_FIELDS = {"status"}

EDITABLE_FIELD_LIMITS = {
    "name": 255,
    "seller_sku": 50,
    "country_name": 50,
    "group": 20,
    "remark": 255,
    "title": 100,
    "point": 100,
    "senior_a": 100,
    "deal": 100,
    "deal_date": 100,
    "deal_end_date": 100,
    "stock_remark": 1000,
}

def database_config() -> dict[str, object]:
    return {
        "host": os.getenv("AMAZON_DB_HOST", DEFAULT_HOST),
        "port": int(os.getenv("AMAZON_DB_PORT", DEFAULT_PORT)),
        "user": os.getenv("AMAZON_DB_USER", DEFAULT_USER),
        "password": os.getenv("AMAZON_DB_PASSWORD", DEFAULT_PASSWORD),
        "database": os.getenv("AMAZON_DB_NAME", DEFAULT_DATABASE),
    }


def serialize_value(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def clean_stock_cell(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return value


def normalize_stock_number(value):
    value = clean_stock_cell(value)
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) if float(value).is_integer() else value
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def normalize_stock_text(value) -> str | None:
    value = clean_stock_cell(value)
    if value is None:
        return None
    return str(value).strip() or None


def build_header_lookup(headers: list[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for header in headers:
        key = re.sub(r"\s+", "", str(header or "").strip()).lower()
        if key and key not in lookup:
            lookup[key] = header
    return lookup


def find_excel_header(header_lookup: dict[str, str], expected_header: str) -> str | None:
    key = re.sub(r"\s+", "", expected_header.strip()).lower()
    return header_lookup.get(key)


def serialize_stock_row(record: dict[str, object]) -> dict[str, object]:
    return {column: serialize_value(record.get(column)) for column, _ in STOCK_LIST_COLUMNS}


def normalize_edit_text(field_name: str, value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    normalized = value.strip()
    limit = EDITABLE_FIELD_LIMITS.get(field_name)
    if limit and len(normalized) > limit:
        raise ValueError(f"{field_name} must be at most {limit} characters")
    return normalized or None


def normalize_edit_date(field_name: str, value) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date or null")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD)") from exc


def normalize_edit_integer(field_name: str, value, allowed: set[int] | None = None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer or null")
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer or null") from exc
    if str(value).strip() != str(normalized):
        raise ValueError(f"{field_name} must be an integer or null")
    if allowed is not None and normalized not in allowed:
        allowed_values = ", ".join(str(item) for item in sorted(allowed))
        raise ValueError(f"{field_name} must be one of: {allowed_values}")
    return normalized


def normalize_edit_payload(payload: dict) -> dict[str, object]:
    unknown_fields = set(payload) - EDITABLE_BASIC_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unsupported fields: {names}")

    normalized: dict[str, object] = {}
    for field_name, value in payload.items():
        if field_name in TEXT_EDITABLE_FIELDS:
            normalized[field_name] = normalize_edit_text(field_name, value)
        elif field_name in DATE_EDITABLE_FIELDS:
            normalized[field_name] = normalize_edit_date(field_name, value)
        elif field_name in BOOLEAN_EDITABLE_FIELDS:
            normalized[field_name] = normalize_edit_integer(field_name, value, {0, 1})
        elif field_name == "status":
            normalized[field_name] = normalize_edit_integer(field_name, value, {0, 1})
        elif field_name in STATUS_TAG_GROUPS:
            normalized[field_name] = normalize_edit_integer(field_name, value)
    if not normalized:
        raise ValueError("at least one editable field is required")
    return normalized


def parse_integer_metric(value) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def parse_float_metric(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    match = re.search(r"-?\d[\d,\.]*", text)
    if not match:
        return None

    token = match.group(0)
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        parts = token.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            token = token.replace(",", ".")
        else:
            token = token.replace(",", "")
    elif token.count(".") > 1:
        parts = token.split(".")
        token = "".join(parts[:-1]) + "." + parts[-1]

    try:
        return float(token)
    except ValueError:
        return None


def build_date_range(end_day: date, days: int = 7) -> list[date]:
    start_day = end_day - timedelta(days=days - 1)
    return [start_day + timedelta(days=offset) for offset in range(days)]


OLD_DAY_DATA_COLUMNS_UNUSED = [
    ("id", "ID"),
    ("name", "产品"),
    ("asin_country", "ASIN+国家"),
    ("asin", "ASIN"),
    ("country", "国家简称"),
    ("country_name", "国家"),
    ("buy_box", "Buy Box"),
    ("price", "价格"),
    ("deal", "优惠"),
    ("star", "星级"),
    ("reviews", "评论数"),
    ("bsr", "BSR"),
    ("inventory", "库存"),
    ("count_day", "日期"),
]


DAY_DATA_COLUMNS = [
    ("id", "ID"),
    ("name", "产品"),
    ("operation", "运营"),
    ("link", "链接"),
    ("picture", "图床"),
    ("asin_country", "ASIN+国家"),
    ("asin", "ASIN"),
    ("country", "国家简称"),
    ("country_name", "国家"),
    ("buy_box", "Buy Box"),
    ("price", "价格"),
    ("deal", "优惠"),
    ("star", "星级"),
    ("reviews", "评论数"),
    ("bsr", "BSR"),
    ("follow_sale", "跟卖数量"),
    ("inventory", "库存"),
    ("count_day", "日期"),
]

DAY_METRIC_FILTERS = {
    "all",
    "risk",
    "attention",
    "price_changed",
    "rank_up",
    "star_down",
    "reviews_down",
    "missing",
}

DAY_TAG_DEFINITIONS = {
    "rank_up": {"label": "排名上升", "color": "blue", "group": "attention"},
    "star_down": {"label": "星级下降", "color": "yellow", "group": "attention"},
    "reviews_down": {"label": "评论下降", "color": "green", "group": "attention"},
    "price_changed": {"label": "价格变化", "color": "red", "group": "risk"},
    "buy_box_missing": {"label": "购物车无", "color": "purple", "group": "risk"},
    "missing": {"label": "缺失数据", "color": "gray", "group": "attention"},
}

STOCK_TABLE = "stock_data"
TODO_TABLE = "todo_list"
TODO_COLUMNS = ("id", "create_date", "content", "status", "end_date", "important", "remark")

STOCK_METRIC_CONDITIONS = {
    "pending_order": "COALESCE(s.`pending_order`, 0) > 0",
    "adjustment_amount": "COALESCE(s.`adjustment_amount`, 0) > 0",
    "overdue_reassignment": "COALESCE(s.`overdue_reassignment`, 0) > 0",
    "stocking_method": "TRIM(COALESCE(s.`stocking_method`, '')) <> ''",
}

STOCK_LIST_COLUMNS = [
    ("stock_id", "ID"),
    ("name", "产品"),
    ("picture", "图床"),
    ("asin", "ASIN"),
    ("link", "链接"),
    ("seller_sku", "Seller SKU"),
    ("country_name", "国家"),
    ("group", "小组"),
    ("stock_date", "日期"),
    ("available_inventory", "FBA可用库存"),
    ("in_transit", "国际在途"),
    ("pending_order", "厚街待建单数量"),
    ("adjustment_amount", "调整量d待备货数量"),
    ("overdue_reassignment", "货件超期关闭等待改派数量"),
    ("stocking_method", "销售建议头程方式"),
    ("sea_quantity", "销售建议空运数量"),
    ("air_quantity", "销售建议其他运输方式数量"),
]

STOCK_IMPORT_HEADER_TO_COLUMN = {
    "seller_sku": "seller_sku",
    "FBA可用库存": "available_inventory",
    "国际在途": "in_transit",
    "厚街待建单数量": "pending_order",
    "调整量d待备货数量": "adjustment_amount",
    "货件超期关闭等待改派数量": "overdue_reassignment",
    "销售建议头程方式": "stocking_method",
    "销售建议空运数量": "sea_quantity",
    "销售建议其他运输方式数量": "air_quantity",
}

STOCK_IMPORT_NUMERIC_COLUMNS = {
    "available_inventory",
    "in_transit",
    "pending_order",
    "adjustment_amount",
    "overdue_reassignment",
    "sea_quantity",
    "air_quantity",
}


def ensure_follow_sale_column(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'asin_day_data'
              AND COLUMN_NAME = 'follow_sale'
            """
        )
        if int(cursor.fetchone()["total"]) == 0:
            cursor.execute("ALTER TABLE `asin_day_data` ADD COLUMN `follow_sale` VARCHAR(20) NULL")
            connection.commit()


def ensure_day_data_columns(connection) -> None:
    ensure_deal_column(connection)
    ensure_follow_sale_column(connection)
    ensure_re_pull_column(connection)
    table_day = quote_identifier("asin_day_data")
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table_day} SET price = NULL "
            "WHERE COALESCE(buy_box, 0) <> 1 "
            "AND price IS NOT NULL AND TRIM(CAST(price AS CHAR)) <> ''"
        )
        if cursor.rowcount:
            connection.commit()


def ensure_deal_column(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DATA_TYPE AS data_type
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'asin_day_data'
              AND COLUMN_NAME = 'deal'
            LIMIT 1
            """
        )
        column = cursor.fetchone()
        if not column:
            cursor.execute("ALTER TABLE `asin_day_data` ADD COLUMN `deal` MEDIUMTEXT NULL")
            connection.commit()
        elif str(column.get("data_type") or "").lower() not in {
            "tinytext", "text", "mediumtext", "longtext", "json"
        }:
            cursor.execute("ALTER TABLE `asin_day_data` MODIFY COLUMN `deal` MEDIUMTEXT NULL")
            connection.commit()


def fetch_repull_ids(connection, count_day: str, scope_ids: list[int] | None = None) -> list[int]:
    if scope_ids == []:
        return []
    if scope_ids is None:
        scope_ids = [int(target['id']) for target in fetch_targets(connection)]
    completed = fetch_completed_day_ids(connection, date.fromisoformat(count_day), scope_ids)
    return sorted(set(scope_ids) - completed)


def merge_crawl_results(total: dict[str, object], current: dict[str, object]) -> None:
    total_stats = total.setdefault("stats", {})
    current_stats = current.get("stats") or {}
    for key, value in current_stats.items():
        total_stats[key] = int(total_stats.get(key, 0)) + int(value or 0)
    total.setdefault("items", []).extend(current.get("items") or [])


def crawl_with_compensation(
    connection,
    *,
    crawl_kwargs: dict[str, object],
    count_day: str,
    max_rounds: int = 5,
) -> dict[str, object]:
    """Run one initial crawl followed by a bounded number of re-pull rounds."""
    target_kwargs = {
        key: crawl_kwargs.get(key)
        for key in ("data_id", "data_ids", "asin_country", "limit", "exclude_activity")
    }
    scope_ids = [int(target["id"]) for target in fetch_targets(connection, **target_kwargs)]
    combined: dict[str, object] = {"stats": {}, "items": [], "retry_rounds": 0}
    first = crawl_amazon_day_data(connection, **crawl_kwargs)
    merge_crawl_results(combined, first)

    for retry_round in range(1, max(0, int(max_rounds)) + 1):
        if crawl_kwargs.get("stop_event") is not None and crawl_kwargs["stop_event"].is_set():
            break
        pending_ids = fetch_repull_ids(connection, count_day, scope_ids)
        if not pending_ids:
            break
        combined["retry_rounds"] = retry_round
        retry_kwargs = dict(crawl_kwargs)
        retry_kwargs.update(
            {
                "data_id": None,
                "data_ids": pending_ids,
                "asin_country": None,
                "limit": None,
                "fill_missing_only": True,
                "finalize_repull": False,
            }
        )
        current = crawl_amazon_day_data(connection, **retry_kwargs)
        merge_crawl_results(combined, current)

    latest = {int(item["data_id"]): item for item in combined["items"]}
    failed_ids = {
        data_id for data_id, item in latest.items() if item.get("db_action") == "failed"
    }
    remaining_ids = set(fetch_repull_ids(connection, count_day, scope_ids)) | failed_ids
    combined["forced_closed"] = 0
    combined["remaining_re_pull"] = len(remaining_ids)
    combined["complete"] = not remaining_ids
    combined["stats"].update({
        "unique_targets": len(scope_ids),
        "complete_targets": len(scope_ids) - len(remaining_ids),
        "incomplete_targets": len(remaining_ids),
        "failed_targets": len(failed_ids),
    })
    return combined


def metric_is_missing(record: dict[str, object]) -> bool:
    missing_values = {"", "无", "none", "null", "n/a", "na"}
    required_fields = ["bsr", "reviews", "star"]
    buy_box_value = record.get("buy_box")
    buy_box = str(buy_box_value if buy_box_value is not None else "").strip()
    if buy_box not in {"0", "1"}:
        return True
    if buy_box == "1":
        required_fields.append("price")
    return any(
        str(record.get(field) or "").strip().casefold() in missing_values
        for field in required_fields
    )


def build_day_flags(record: dict[str, object], previous: dict[str, object] | None) -> dict[str, bool]:
    has_buy_box = str(record.get("buy_box") or "0").strip() == "1"
    previous_has_buy_box = bool(
        previous and str(previous.get("buy_box") or "0").strip() == "1"
    )
    price_now = parse_float_metric(record.get("price")) if has_buy_box else None
    price_before = (
        parse_float_metric(previous.get("price"))
        if previous_has_buy_box and previous
        else None
    )
    bsr_now = parse_integer_metric(record.get("bsr"))
    bsr_before = parse_integer_metric(previous.get("bsr")) if previous else None
    star_now = parse_float_metric(record.get("star"))
    star_before = parse_float_metric(previous.get("star")) if previous else None
    reviews_now = parse_integer_metric(record.get("reviews"))
    reviews_before = parse_integer_metric(previous.get("reviews")) if previous else None

    return {
        "price_changed": price_now is not None and price_before is not None and price_now != price_before,
        "rank_up": bsr_now is not None and bsr_before is not None and bsr_now > bsr_before,
        "star_down": star_now is not None and star_before is not None and star_now < star_before,
        "reviews_down": reviews_now is not None and reviews_before is not None and reviews_now < reviews_before,
        "missing": metric_is_missing(record),
        "buy_box_missing": str(
            record.get("buy_box") if record.get("buy_box") is not None else ""
        ).strip() == "0",
        "comparison_available": previous is not None,
    }


def build_day_tags(flags: dict[str, bool]) -> list[dict[str, str]]:
    return [
        {"key": key, **DAY_TAG_DEFINITIONS[key]}
        for key in DAY_TAG_DEFINITIONS
        if flags.get(key)
    ]


def serialize_day_row(record: dict[str, object], flags: dict[str, bool]) -> dict[str, object]:
    row = {column: serialize_value(record.get(column)) for column, _ in DAY_DATA_COLUMNS}
    row["data_id"] = serialize_value(record.get("data_id"))
    row["id"] = serialize_value(record.get("id"))
    buy_box_value = record.get("buy_box")
    raw_buy_box = str(buy_box_value if buy_box_value is not None else "").strip()
    row["buy_box"] = raw_buy_box if raw_buy_box in {"0", "1"} else ""
    if row["buy_box"] != "1":
        row["price"] = ""
    row["metric_flags"] = flags
    row["tags"] = build_day_tags(flags)
    row["tag_keys"] = [tag["key"] for tag in row["tags"]]
    raw_deal = record.get("deal")
    if isinstance(raw_deal, (bytes, bytearray)):
        raw_deal = raw_deal.decode("utf-8", errors="replace")
    try:
        parsed_deal = json.loads(str(raw_deal)) if raw_deal else []
    except (TypeError, ValueError):
        parsed_deal = []
    if not isinstance(parsed_deal, list):
        parsed_deal = []
    row["deal"] = [
        {"type": str(item["type"]), "label": str(item["label"])}
        for item in parsed_deal
        if isinstance(item, dict)
        and item.get("type") in {"price_discount", "coupon", "promotion"}
        and item.get("label")
    ]
    return row


def matches_day_search(record: dict[str, object], search: str, tags: list[dict[str, str]]) -> bool:
    lowered = (search or "").strip().casefold()
    if not lowered:
        return True
    values = [
        record.get("name"),
        record.get("operation"),
        record.get("asin"),
        record.get("asin_country"),
        record.get("country"),
        record.get("country_name"),
        *(tag.get("label") for tag in tags),
        *(tag.get("key") for tag in tags),
    ]
    if lowered in {"风险项", "risk"}:
        return any(tag.get("group") == "risk" for tag in tags)
    if lowered in {"关注项", "attention"}:
        return any(tag.get("group") == "attention" for tag in tags)
    return any(lowered in str(value or "").casefold() for value in values)


def fetch_day_records(connection, day_filter: str) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
    table_day = quote_identifier("asin_day_data")
    table_amazon = quote_identifier("amazon_data")

    sql = """
        SELECT
            d.id,
            a.id AS data_id,
            COALESCE(NULLIF(a.name, ''), NULLIF(d.name, ''), '') AS name,
            COALESCE(a.operation, '') AS operation,
            COALESCE(NULLIF(a.asin_country, ''), NULLIF(d.asin_country, ''), '') AS asin_country,
            a.asin,
            a.country,
            a.country_name,
            a.link,
            a.picture,
            d.buy_box,
            d.price,
            d.deal,
            d.star,
            d.reviews,
            d.bsr,
            d.follow_sale,
            d.inventory,
            d.re_pull,
            d.count_day
        FROM {amazon_table} a
        JOIN {day_table} d ON d.data_id = a.id AND d.count_day = %s
        WHERE a.status = 1
        ORDER BY a.id DESC
    """.format(
        amazon_table=table_amazon,
        day_table=table_day,
    )

    with connection.cursor() as cursor:
        cursor.execute(sql, [day_filter])
        records = list(cursor.fetchall())

    data_ids = [int(record["data_id"]) for record in records if record.get("data_id") is not None]
    previous_by_data_id: dict[int, dict[str, object]] = {}
    if data_ids:
        previous_day = (date.fromisoformat(day_filter) - timedelta(days=1)).isoformat()
        placeholders = ", ".join(["%s"] * len(data_ids))
        history_sql = f"""
            SELECT data_id, count_day, price, star, reviews, bsr, buy_box
            FROM {table_day}
            WHERE data_id IN ({placeholders}) AND count_day = %s
        """
        with connection.cursor() as cursor:
            cursor.execute(history_sql, [*data_ids, previous_day])
            for history in cursor.fetchall():
                data_id = int(history["data_id"])
                if data_id not in previous_by_data_id:
                    previous_by_data_id[data_id] = history

    return records, previous_by_data_id


def apply_crawl_env(payload: dict) -> dict[str, str | None]:
    env_updates: dict[str, str] = {
        "AMAZON_ENABLE_SELENIUM": "0",
        "AMAZON_SKIP_DEAL": "1",
        "AMAZON_PRIMARY_ONLY": "1" if payload.get("primary_only", True) else "0",
        "AMAZON_STOP_ON_BLOCK": "1",
        "AMAZON_FALLBACK_LIMIT": "1",
        "AMAZON_SELENIUM_FAST": "0",
        "AMAZON_SELENIUM_ONLY": "0",
        "AMAZON_SELENIUM_DEBUGGER_ADDRESS": "",
        "AMAZON_SELENIUM_SITE_DEBUGGERS": "",
    }
    # Browser crawling is the default for daily data. Callers may explicitly
    # opt out for diagnostics, but a bare API request should use the same
    # browser path as the UI (which is much less likely to hit bot checks).
    if payload.get("use_selenium", True) is not False:
        browser = str(payload.get("browser") or "chrome").strip().lower()
        env_updates["AMAZON_ENABLE_SELENIUM"] = "1"
        env_updates["AMAZON_SELENIUM_BROWSER"] = browser
        # Keep the browser visible by default. A non-headless session is more
        # reliable against Amazon's challenge pages; callers can explicitly
        # request headless mode for controlled environments.
        env_updates["AMAZON_SELENIUM_HEADLESS"] = "1" if payload.get("headless") is True else "0"
        env_updates["AMAZON_SELENIUM_WAIT_SECONDS"] = str(payload.get("wait_seconds") or 3)
        env_updates["AMAZON_SELENIUM_PAGE_TIMEOUT"] = str(payload.get("page_timeout") or 10)
        if "selenium_fast" in payload:
            env_updates["AMAZON_SELENIUM_FAST"] = "1" if payload.get("selenium_fast") is True else "0"
        if "selenium_only" in payload:
            env_updates["AMAZON_SELENIUM_ONLY"] = "1" if payload.get("selenium_only") is True else "0"
        if "primary_only" in payload:
            env_updates["AMAZON_PRIMARY_ONLY"] = "1" if payload.get("primary_only") is True else "0"
        if "stop_on_block" in payload:
            env_updates["AMAZON_STOP_ON_BLOCK"] = "1" if payload.get("stop_on_block") is True else "0"
        if "fallback_limit" in payload:
            env_updates["AMAZON_FALLBACK_LIMIT"] = str(payload.get("fallback_limit") or 2)
        if "skip_deal" in payload:
            env_updates["AMAZON_SKIP_DEAL"] = "1" if payload.get("skip_deal") is True else "0"
        debugger_address = str(payload.get("debugger_address") or "").strip()
        if payload.get("debugger_addresses") is not None:
            if debugger_address:
                raise ValueError("不能同时指定共享浏览器和站点独立浏览器")
            addresses = payload["debugger_addresses"]
            # Validate without changing the process environment first.
            if not isinstance(addresses, dict) or not addresses or any(
                country not in {"U", "M", "G", "E", "C", "F", "I", "S", "A", "J"}
                or not isinstance(address, str)
                or not re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", address)
                or not 1 <= int(address.rsplit(":", 1)[1]) <= 65535
                for country, address in addresses.items()
            ):
                raise ValueError("站点浏览器必须为有效国家和本机端口")
            if len(set(addresses.values())) != len(addresses):
                raise ValueError("每个站点必须使用独立浏览器端口")
            env_updates["AMAZON_SELENIUM_SITE_DEBUGGERS"] = json.dumps(addresses)
        elif debugger_address:
            env_updates["AMAZON_SELENIUM_DEBUGGER_ADDRESS"] = debugger_address
        else:
            default_profile = BASE_DIR / f".amazon_{browser}_profile"
            env_updates["AMAZON_SELENIUM_PROFILE_DIR"] = str(payload.get("profile_dir") or default_profile)

    original_env = {key: os.environ.get(key) for key in env_updates}
    os.environ.update(env_updates)
    return original_env


def restore_env(original_env: dict[str, str | None]) -> None:
    for key, old_value in original_env.items():
        if old_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old_value


@app.get("/")
def index():
    return render_template("index.html", display_columns=DISPLAY_COLUMNS)


@app.get("/api/basic-info")
def basic_info():
    try:
        page = max(request.args.get("page", 1, type=int), 1)
        page_size = min(max(request.args.get("page_size", 50, type=int), 1), 200)
        search = (request.args.get("search", "", type=str) or "").strip()
        operation = (request.args.get("operation", "", type=str) or "").strip()
        metric = (request.args.get("metric", "", type=str) or "").strip()
        if metric not in BASIC_INFO_METRIC_CONDITIONS:
            metric = ""
        offset = (page - 1) * page_size

        columns_sql = ", ".join(quote_identifier(column) for column in BASIC_INFO_RECORD_COLUMNS)
        table_sql = quote_identifier(DEFAULT_TABLE)
        where_parts: list[str] = ["`status` = 1"]
        params: list[object] = []
        if search:
            searchable_columns = [
                "name",
                "operation",
                "asin",
                "seller_sku",
                "country",
                "country_name",
                "group",
                "remark",
                "link",
                "title",
                "point",
                "senior_a",
                "deal",
                "deal_date",
                "deal_end_date",
                "stock_remark",
            ]
            search_like = f"%{search.lower()}%"
            search_parts = [
                f"LOWER(CAST({quote_identifier(column)} AS CHAR)) LIKE %s"
                for column in searchable_columns
            ]
            search_params: list[object] = [search_like] * len(searchable_columns)
            for field_name, group_name in STATUS_TAG_GROUPS.items():
                search_parts.append(
                    "EXISTS (SELECT 1 FROM `amz_tag` st "
                    "JOIN `amz_tag_group` sg ON sg.`id` = st.`group_id` "
                    f"WHERE st.`id` = {table_sql}.{quote_identifier(field_name)} "
                    "AND sg.`group_name` = %s "
                    "AND LOWER(CAST(st.`tag_name` AS CHAR)) LIKE %s)"
                )
                search_params.extend([group_name, search_like])
            where_parts.append("(" + " OR ".join(search_parts) + ")")
            params.extend(search_params)
        if operation:
            where_parts.append("TRIM(COALESCE(`operation`, '')) = %s")
            params.append(operation)
        if metric:
            where_parts.append(BASIC_INFO_METRIC_CONDITIONS[metric])
        where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        config = database_config()
        with connect(**config) as connection:
            with connection.cursor() as cursor:
                active_condition = "`status` = 1"
                summary_fields = ", ".join(
                    f"SUM(CASE WHEN {active_condition} AND {condition} THEN 1 ELSE 0 END) AS {quote_identifier(key)}"
                    for key, condition in BASIC_INFO_METRIC_CONDITIONS.items()
                )
                cursor.execute(f"SELECT {summary_fields} FROM {table_sql}")
                summary_record = cursor.fetchone() or {}
                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM {table_sql} {where_sql}",
                    params,
                )
                total = int(cursor.fetchone()["total"])

                cursor.execute(
                    f"SELECT {columns_sql} FROM {table_sql} {where_sql} "
                    "ORDER BY `id` DESC LIMIT %s OFFSET %s",
                    [*params, page_size, offset],
                )
                records = cursor.fetchall()

        rows = [
            {column: serialize_value(record.get(column)) for column in BASIC_INFO_RECORD_COLUMNS}
            for record in records
        ]
        summary = {
            key: int(summary_record.get(key) or 0)
            for key in BASIC_INFO_METRIC_CONDITIONS
        }
        total_pages = max((total + page_size - 1) // page_size, 1)
        return jsonify(
            {
                "success": True,
                "rows": rows,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "metric": metric,
                "summary": summary,
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.get("/api/basic-info/status-tags")
def basic_info_status_tags():
    try:
        names = list(STATUS_TAG_GROUPS.values())
        placeholders = ", ".join(["%s"] * len(names))
        sql = f"""
            SELECT g.`group_name`, g.`status` AS group_status,
                   t.`id`, t.`tag_name`, t.`color`, t.`status` AS tag_status
            FROM `amz_tag_group` g
            LEFT JOIN `amz_tag` t ON t.`group_id` = g.`id`
            WHERE g.`group_name` IN ({placeholders})
            ORDER BY g.`sort_no`, g.`id`, t.`sort_no`, t.`id`
        """
        with connect(**database_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, names)
                records = cursor.fetchall()

        groups = {
            group_name: {"status": 0, "tags": []}
            for group_name in names
        }
        for record in records:
            group = groups.get(record["group_name"])
            if group is None:
                continue
            group["status"] = int(record.get("group_status") or 0)
            if record.get("id") is not None:
                group["tags"].append({
                    "id": serialize_value(record["id"]),
                    "tag_name": serialize_value(record.get("tag_name")),
                    "color": serialize_value(record.get("color")),
                    "status": int(record.get("tag_status") or 0),
                })
        return jsonify({"success": True, "groups": {
            field: groups[group_name]
            for field, group_name in STATUS_TAG_GROUPS.items()
        }})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.get("/api/settings/tag-groups")
def tag_groups():
    return settings_tag_list("group")


@app.get("/api/settings/tags")
def tags():
    return settings_tag_list("tag")


def settings_tag_list(kind: str):
    """Read-only, paginated lists for the two tag configuration tables."""
    page = max(request.args.get("page", 1, type=int), 1)
    page_size = min(max(request.args.get("page_size", 50, type=int), 1), 200)
    search = (request.args.get("search", "", type=str) or "").strip()
    offset = (page - 1) * page_size
    if kind == "group":
        table = "amz_tag_group"
        fields = "id, group_name, sort_no, status, remark, create_time, updated_time"
        searchable = ("group_name", "remark")
        query = f"SELECT {fields} FROM `{table}`"
    else:
        table = "amz_tag"
        fields = "t.id, t.group_id, g.group_name, t.tag_name, t.color, t.sort_no, t.status, t.remark, t.create_time, t.update_time"
        searchable = ("t.tag_name", "g.group_name", "t.remark", "t.color")
        query = f"SELECT {fields} FROM `{table}` t LEFT JOIN `amz_tag_group` g ON g.id = t.group_id"
    where_sql = ""
    params: list[object] = []
    if search:
        where_sql = " WHERE " + " OR ".join(f"CAST({column} AS CHAR) LIKE %s" for column in searchable)
        params = [f"%{search}%"] * len(searchable)
    try:
        with connect(**database_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) AS total FROM `{table}`" + (" t LEFT JOIN `amz_tag_group` g ON g.id = t.group_id" if kind == "tag" else "") + where_sql, params)
                total = int(cursor.fetchone()["total"])
                cursor.execute(query + where_sql + f" ORDER BY {'id' if kind == 'group' else 't.id'} ASC LIMIT %s OFFSET %s", [*params, page_size, offset])
                rows = [{key: serialize_value(value) for key, value in row.items()} for row in cursor.fetchall()]
        return jsonify({"success": True, "rows": rows, "total": total, "page": page, "page_size": page_size, "total_pages": max((total + page_size - 1) // page_size, 1)})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


def tag_group_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"success": False, "message": "请求内容格式不正确"}), 400)
    name = str(payload.get("group_name") or "").strip()
    remark = str(payload.get("remark") or "").strip()
    try:
        sort_no = int(payload.get("sort_no", 0) or 0)
        status = int(payload.get("status", 1))
    except (TypeError, ValueError):
        return None, (jsonify({"success": False, "message": "排序和状态必须是有效数字"}), 400)
    if not name or len(name) > 64:
        return None, (jsonify({"success": False, "message": "标签组名称为必填，且不能超过 64 个字符"}), 400)
    if len(remark) > 255 or status not in (0, 1):
        return None, (jsonify({"success": False, "message": "备注最多 255 个字符，状态只能为启用或停用"}), 400)
    return {"group_name": name, "sort_no": sort_no, "status": status, "remark": remark or None}, None


@app.post("/api/settings/tag-groups")
def create_tag_group():
    values, error = tag_group_payload()
    if error:
        return error
    try:
        with connect(**database_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO `amz_tag_group` (`group_name`, `sort_no`, `status`, `remark`) VALUES (%s, %s, %s, %s)", tuple(values.values()))
                group_id = cursor.lastrowid
            connection.commit()
        return jsonify({"success": True, "id": group_id}), 201
    except Exception as exc:
        if getattr(exc, "args", [None])[0] == 1062:
            return jsonify({"success": False, "message": "标签组名称已存在"}), 409
        return jsonify({"success": False, "message": str(exc)}), 500


@app.patch("/api/settings/tag-groups/<int:group_id>")
def update_tag_group(group_id: int):
    values, error = tag_group_payload()
    if error:
        return error
    try:
        with connect(**database_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE `amz_tag_group` SET `group_name`=%s, `sort_no`=%s, `status`=%s, `remark`=%s WHERE `id`=%s", (*values.values(), group_id))
                if cursor.rowcount == 0:
                    cursor.execute("SELECT `id` FROM `amz_tag_group` WHERE `id`=%s", (group_id,))
                    if not cursor.fetchone():
                        return jsonify({"success": False, "message": "标签组不存在"}), 404
            connection.commit()
        return jsonify({"success": True})
    except Exception as exc:
        if getattr(exc, "args", [None])[0] == 1062:
            return jsonify({"success": False, "message": "标签组名称已存在"}), 409
        return jsonify({"success": False, "message": str(exc)}), 500


@app.delete("/api/settings/tag-groups/<int:group_id>")
def deactivate_tag_group(group_id: int):
    try:
        with connect(**database_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE `amz_tag_group` SET `status`=0 WHERE `id`=%s", (group_id,))
                if cursor.rowcount == 0:
                    cursor.execute("SELECT `id` FROM `amz_tag_group` WHERE `id`=%s", (group_id,))
                    if not cursor.fetchone():
                        return jsonify({"success": False, "message": "标签组不存在"}), 404
            connection.commit()
        return jsonify({"success": True, "logical_delete": True})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


def tag_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"success": False, "message": "请求内容格式不正确"}), 400)
    name = str(payload.get("tag_name") or "").strip()
    color = str(payload.get("color") or "").strip()
    remark = str(payload.get("remark") or "").strip()
    try:
        group_id = int(payload.get("group_id"))
        sort_no = int(payload.get("sort_no", 0) or 0)
        status = int(payload.get("status", 1))
    except (TypeError, ValueError):
        return None, (jsonify({"success": False, "message": "标签组、排序和状态必须是有效数字"}), 400)
    rgb = re.fullmatch(r"(?:RGB)?\s*\(?\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)?", color, re.IGNORECASE) if color else None
    if color and (not rgb or any(int(channel) > 255 for channel in rgb.groups())):
        return None, (jsonify({"success": False, "message": "请选择有效颜色"}), 400)
    if rgb:
        color = f"RGB({int(rgb.group(1))},{int(rgb.group(2))},{int(rgb.group(3))})"
    if not name or len(name) > 64 or len(color) > 50 or len(remark) > 255 or status not in (0, 1):
        return None, (jsonify({"success": False, "message": "请检查必填项及字段长度；状态只能为启用或停用"}), 400)
    return {"group_id": group_id, "tag_name": name, "color": color or None, "sort_no": sort_no, "status": status, "remark": remark or None}, None


@app.post("/api/settings/tags")
def create_tag():
    values, error = tag_payload()
    if error:
        return error
    try:
        with connect(**database_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT `id` FROM `amz_tag_group` WHERE `id`=%s", (values["group_id"],))
                if not cursor.fetchone():
                    return jsonify({"success": False, "message": "所选标签组不存在"}), 400
                cursor.execute("INSERT INTO `amz_tag` (`group_id`, `tag_name`, `color`, `sort_no`, `status`, `remark`) VALUES (%s, %s, %s, %s, %s, %s)", tuple(values.values()))
                tag_id = cursor.lastrowid
            connection.commit()
        return jsonify({"success": True, "id": tag_id}), 201
    except Exception as exc:
        if getattr(exc, "args", [None])[0] == 1062:
            return jsonify({"success": False, "message": "该标签组中已存在同名标签"}), 409
        return jsonify({"success": False, "message": str(exc)}), 500


@app.patch("/api/settings/tags/<int:tag_id>")
def update_tag(tag_id: int):
    values, error = tag_payload()
    if error:
        return error
    try:
        with connect(**database_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT `id` FROM `amz_tag_group` WHERE `id`=%s", (values["group_id"],))
                if not cursor.fetchone():
                    return jsonify({"success": False, "message": "所选标签组不存在"}), 400
                cursor.execute("UPDATE `amz_tag` SET `group_id`=%s, `tag_name`=%s, `color`=%s, `sort_no`=%s, `status`=%s, `remark`=%s WHERE `id`=%s", (*values.values(), tag_id))
                if cursor.rowcount == 0:
                    cursor.execute("SELECT `id` FROM `amz_tag` WHERE `id`=%s", (tag_id,))
                    if not cursor.fetchone():
                        return jsonify({"success": False, "message": "标签不存在"}), 404
            connection.commit()
        return jsonify({"success": True})
    except Exception as exc:
        if getattr(exc, "args", [None])[0] == 1062:
            return jsonify({"success": False, "message": "该标签组中已存在同名标签"}), 409
        return jsonify({"success": False, "message": str(exc)}), 500


@app.delete("/api/settings/tags/<int:tag_id>")
def deactivate_tag(tag_id: int):
    try:
        with connect(**database_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE `amz_tag` SET `status`=0 WHERE `id`=%s", (tag_id,))
                if cursor.rowcount == 0:
                    cursor.execute("SELECT `id` FROM `amz_tag` WHERE `id`=%s", (tag_id,))
                    if not cursor.fetchone():
                        return jsonify({"success": False, "message": "标签不存在"}), 404
            connection.commit()
        return jsonify({"success": True, "logical_delete": True})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.get("/api/basic-info/operations")
def basic_info_operations():
    try:
        table_sql = quote_identifier(DEFAULT_TABLE)
        with connect(**database_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT DISTINCT TRIM(`operation`) AS operation FROM {table_sql} "
                    "WHERE `status` = 1 "
                    "AND `operation` IS NOT NULL AND TRIM(`operation`) <> '' "
                    "ORDER BY operation"
                )
                operations = [
                    str(record["operation"])
                    for record in cursor.fetchall()
                    if record.get("operation")
                ]
        return jsonify({"success": True, "operations": operations})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.patch("/api/basic-info/<int:record_id>")
def update_basic_info(record_id: int):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"success": False, "message": "JSON object is required"}), 400

    try:
        updates = normalize_edit_payload(payload)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    table_sql = quote_identifier(DEFAULT_TABLE)
    config = database_config()

    try:
        with connect(**config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT `id`, {', '.join(quote_identifier(field) for field in STATUS_TAG_GROUPS)} "
                    f"FROM {table_sql} WHERE `id` = %s LIMIT 1",
                    (record_id,),
                )
                current_record = cursor.fetchone()
                if not current_record:
                    return jsonify({"success": False, "message": "record not found"}), 404

                status_fields = [field for field in updates if field in STATUS_TAG_GROUPS and updates[field] is not None]
                tags_by_id = {}
                if status_fields:
                    tag_ids = sorted({updates[field] for field in status_fields})
                    tag_placeholders = ", ".join(["%s"] * len(tag_ids))
                    cursor.execute(
                        "SELECT t.`id`, t.`status` AS tag_status, g.`group_name`, "
                        "g.`status` AS group_status FROM `amz_tag` t "
                        "JOIN `amz_tag_group` g ON g.`id` = t.`group_id` "
                        f"WHERE t.`id` IN ({tag_placeholders})",
                        tag_ids,
                    )
                    tags_by_id = {int(row["id"]): row for row in cursor.fetchall()}
                for field_name in status_fields:
                    tag_id = updates[field_name]
                    tag = tags_by_id.get(tag_id)
                    if not tag or tag["group_name"] != STATUS_TAG_GROUPS[field_name]:
                        raise ValueError(f"{field_name} must reference a tag in {STATUS_TAG_GROUPS[field_name]}")
                    current_id = current_record.get(field_name)
                    unchanged = current_id is not None and int(current_id) == tag_id
                    if (int(tag.get("tag_status") or 0) != 1 or int(tag.get("group_status") or 0) != 1) and not unchanged:
                        raise ValueError(f"{field_name} must reference an enabled tag")

                set_sql = ", ".join(f"{quote_identifier(field)} = %s" for field in updates)
                values = [updates[field] for field in updates]

                cursor.execute(
                    f"UPDATE {table_sql} SET {set_sql} WHERE `id` = %s",
                    [*values, record_id],
                )
                connection.commit()

                cursor.execute(
                    f"SELECT * FROM {table_sql} WHERE `id` = %s LIMIT 1",
                    (record_id,),
                )
                record = cursor.fetchone()

        return jsonify(
            {
                "success": True,
                "row": {field: serialize_value(value) for field, value in record.items()},
            }
        )
    except ValueError as exc:
        try:
            connection.rollback()
        except (NameError, UnboundLocalError):
            pass
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        try:
            connection.rollback()
        except (NameError, UnboundLocalError):
            pass
        return jsonify({"success": False, "message": str(exc)}), 500


@app.post("/api/basic-info/refresh")
def refresh_basic_info():
    try:
        config = database_config()
        with connect(**config) as connection:
            stats, unmapped_headers = import_excel_data(
                connection,
                excel_path=os.getenv("AMAZON_EXCEL_PATH", DEFAULT_EXCEL_PATH),
                table=DEFAULT_TABLE,
                batch_size=500,
            )

        return jsonify(
            {
                "success": True,
                "message": "连接信息刷新完成",
                "stats": {
                    "total_rows": stats.total_rows,
                    "inserted_rows": stats.inserted_rows,
                    "updated_existing_rows": stats.updated_existing_rows,
                    "obsoleted_rows": stats.obsoleted_rows,
                    "reactivated_rows": stats.reactivated_rows,
                    "matched_existing": stats.matched_existing,
                    "skipped_duplicate_in_file": stats.skipped_duplicate_in_file,
                    "skipped_missing_key": stats.skipped_missing_key,
                },
                "unmapped_headers": unmapped_headers,
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


def run_optimized_crawl(payload, kind, progress=None, stop_event=None):
    """A fast first pass, then separately checkpointed compensation phases."""
    if kind == "compensate":
        payload = compensation_browser_options(
            payload, browser=str(payload.get("compensation_browser") or "chrome")
        )
    config = database_config()
    today = date.today()
    if payload.get("count_day", today.isoformat()) != today.isoformat():
        raise ValueError("只能恢复当天任务，请为今天重新创建任务")
    options = {**payload, "skip_deal": True, "stop_on_block": True,
               "primary_only": kind == "crawl", "fallback_limit": 1}
    original_env = apply_crawl_env(options)
    try:
        with connect(**config) as connection:
            ensure_day_data_columns(connection)
            # Site workers commit through their own connections; summaries
            # must see those commits instead of an old REPEATABLE READ view.
            connection.autocommit(True)
            selectors = {key: payload.get(key) for key in ("data_id", "data_ids", "asin_country", "limit")}
            targets = fetch_targets(connection, **selectors, exclude_activity=payload.get("exclude_activity", True))
            site_count = len({t['country'] for t in targets})
            if (payload.get('pause_on_auth') and site_count > 1
                    and (payload.get('parallel_sites') != 2 or site_count > 2)):
                raise ValueError('Authentication pause supports at most two marketplaces per job')
            def item_progress(record=None, **values):
                if (record and payload.get('pause_on_auth') and stop_event is not None
                        and not stop_event.is_set()
                        and (is_hard_block_message(record.get('error') or '')
                             or any(is_hard_block_message(attempt.get('message') or '')
                                    for attempt in record.get('attempts', []) if not attempt.get('success')))):
                    if progress:
                        progress(pause_reason='authentication_required', authentication_site=record.get('country_code'))
                    stop_event.set()
                if progress:
                    progress(record, **values)
            scope_ids = sorted({int(target["id"]) for target in targets})
            if kind == "compensate":
                scope_ids = fetch_repull_ids(connection, today.isoformat(), scope_ids)
            pending = fetch_repull_ids(connection, today.isoformat(), scope_ids)
            if progress:
                progress(phase="primary" if kind == "crawl" else "compensation",
                         processed=0, total=len(scope_ids), scope_ids=scope_ids,
                         count_day=today.isoformat(), pending=len(pending))
            kwargs = dict(data_ids=scope_ids, resume=payload.get("resume", True),
                          fill_missing_only=kind == "compensate", workers=min(2, max(1, int(payload.get("workers", 2)))),
                          connection_factory=lambda: connect(**config), progress_callback=item_progress if payload.get('pause_on_auth') else progress,
                          stop_event=stop_event, count_day=today)
            if progress and kwargs["resume"]:
                progress(processed=len(scope_ids) - len(pending))
            result = crawl_with_compensation(connection, crawl_kwargs=kwargs,
                                             count_day=today.isoformat(), max_rounds=0)
            result.update(date=today.isoformat(), target_count=len(scope_ids))
            def latest_items_by_id():
                latest = {int(item["data_id"]): item for item in result["items"]}
                return latest
            def failed_ids():
                return {target_id for target_id, item in latest_items_by_id().items()
                        if item.get("db_action") == "failed"}
            def unavailable_ids():
                return {target_id for target_id, item in latest_items_by_id().items()
                        if item.get("db_action") == "link_unavailable"}
            def pending_ids():
                return sorted((set(fetch_repull_ids(connection, today.isoformat(), scope_ids))
                               - unavailable_ids()) | failed_ids())
            if progress:
                first_pending = pending_ids()
                result.update(remaining_re_pull=len(first_pending),
                              remaining_missing=len(first_pending), complete=not first_pending)
                progress(first_result={key: value for key, value in result.items() if key != "items"},
                         pending=len(first_pending))
            # A background job publishes the first result before compensation.
            # Synchronous callers receive the first result immediately.
            rounds = min(2, max(1, int(payload.get("compensation_rounds", 2))))
            compensation_browser = str(payload.get("compensation_browser") or "chrome").lower()
            run_compensation = kind == "compensate" or (progress and payload.get("auto_compensate", True))
            if run_compensation:
                for round_index in range(1 if kind == "compensate" else 0, rounds):
                    pending = pending_ids()
                    if not pending or (stop_event and stop_event.is_set()):
                        break
                    if stop_event:
                        if progress:
                            progress(phase="cooldown", pending=len(pending))
                        if stop_event.wait(30):
                            break
                    if progress:
                        progress(phase="compensation", processed=0, total=len(pending),
                                 round=round_index + 1, pending=len(pending))
                    retry = {**kwargs, "data_ids": pending, "fill_missing_only": True,
                             "resume": False, "workers": 1}
                    compensation_env = apply_crawl_env(
                        compensation_browser_options({**payload, "primary_only": False,
                                                      "skip_deal": True,
                                                      "stop_on_block": True},
                                                      browser=compensation_browser)
                    )
                    try:
                        current = crawl_with_compensation(connection, crawl_kwargs=retry,
                                                          count_day=today.isoformat(), max_rounds=0)
                    finally:
                        restore_env(compensation_env)
                    merge_crawl_results(result, current)
                edge_pending = pending_ids()
                if (compensation_browser != "edge" and edge_pending
                        and not (stop_event and stop_event.is_set())):
                    if progress:
                        progress(phase="compensation", processed=0, total=len(edge_pending),
                                 round=rounds + 1, browser="edge", pending=len(edge_pending))
                    edge_retry = {**kwargs, "data_ids": edge_pending, "fill_missing_only": True,
                                  "resume": False, "workers": 1}
                    edge_env = apply_crawl_env(
                        compensation_browser_options({**payload, "primary_only": False,
                                                      "skip_deal": True,
                                                      "stop_on_block": True}, browser="edge")
                    )
                    try:
                        edge_result = crawl_with_compensation(
                            connection, crawl_kwargs=edge_retry,
                            count_day=today.isoformat(), max_rounds=0)
                    finally:
                        restore_env(edge_env)
                    merge_crawl_results(result, edge_result)
                    result["fallback_browser"] = "edge"
            # Every summary is based on persisted daily records, with a single
            # data_id per marketplace. Log rows and repeated ASINs are not units
            # of progress (one ASIN may be sold in several countries).
            pending = pending_ids()
            result.update(remaining_re_pull=len(pending), remaining_missing=len(pending), complete=not pending)
            unavailable = unavailable_ids()
            valid_scope_count = len(set(scope_ids) - unavailable)
            result["stats"].update(unique_targets=len(set(scope_ids)),
                                   complete_targets=max(0, valid_scope_count-len(pending)),
                                   incomplete_targets=len(pending),
                                   link_unavailable_targets=len(unavailable))
            latest = {int(item["data_id"]): item for item in result["items"]}
            result["stats"]["failed_targets"] = sum(
                latest.get(target_id, {}).get("db_action") == "failed" for target_id in pending)
            if progress:
                progress(pending=len(pending), phase="paused" if stop_event.is_set() else "done")
            return result
    finally:
        restore_env(original_env)


def submit_crawl(kind):
    try:
        payload = request.get_json(silent=True) or {}
        if kind == "compensate":
            payload = compensation_browser_options(payload)
        if payload.get('count_day', date.today().isoformat()) != date.today().isoformat():
            raise ValueError('只能提交当天的抓取任务')
        payload["count_day"] = date.today().isoformat()
        if "data_ids" in payload:
            if not isinstance(payload["data_ids"], list):
                raise ValueError("data_ids 必须为列表")
            payload["data_ids"] = sorted({int(item) for item in payload["data_ids"]})
        for key in ("data_id", "limit", "workers", "compensation_rounds"):
            if payload.get(key) is not None:
                payload[key] = int(payload[key])
        if payload.get("async", False):
            job = crawl_jobs.submit(payload, kind, run_optimized_crawl)
            return jsonify(success=True, job=job), 202
        with crawl_lease(crawl_jobs.lease_path):
            result = run_optimized_crawl(payload, kind)
        return jsonify(success=True, **result)
    except CrawlBusy as exc:
        return jsonify(success=False, message=str(exc)), 409
    except (ValueError, TypeError) as exc:
        return jsonify(success=False, message=str(exc)), 400
    except Exception as exc:
        return jsonify(success=False, message=str(exc)), 500


@app.post("/api/amazon-day-data/crawl")
def amazon_day_data_crawl():
    return submit_crawl("crawl")


@app.post("/api/amazon-day-data/compensate")
def amazon_day_data_compensate():
    return submit_crawl("compensate")


@app.get("/api/amazon-day-data/jobs/latest")
@app.get("/api/amazon-day-data/jobs/<job_id>")
def crawl_job_status(job_id=None):
    return jsonify(success=True, job=crawl_jobs.get(job_id))


@app.post("/api/amazon-day-data/jobs/<job_id>/stop")
def stop_crawl_job(job_id):
    return jsonify(success=True, job=crawl_jobs.stop(job_id))


@app.get('/api/amazon-day-data/scheduled')
def scheduled_status():
    from amazon_importer import scheduled_runner as runner
    from amazon_importer.site_sessions import SITE_NAMES
    state = runner.public_state()
    return jsonify(success=True, batch=state, site_names=SITE_NAMES)


@app.post('/api/amazon-day-data/scheduled/resume-auth')
def resume_scheduled_auth():
    from amazon_importer import scheduled_runner as runner
    from scheduled_crawl import write_log, API_URL, TIMEOUT_SECONDS
    try:
        latest = crawl_jobs.get()
        if latest and latest['status'] in {'queued', 'running', 'stopping'}:
            raise CrawlBusy('请等待当前抓取结束后再补拉认证站点')
        runner.launch_resume(write_log, API_URL, TIMEOUT_SECONDS)
        return jsonify(success=True), 202
    except CrawlBusy as exc:
        return jsonify(success=False, message=str(exc)), 409
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400


@app.post('/api/amazon-day-data/scheduled/stop')
def stop_scheduled_batch():
    from amazon_importer import scheduled_runner as runner
    state = runner.read_state()
    if not state or state.get('status') not in {'running', 'preparing'}:
        return jsonify(success=False, message='没有正在执行的定时批次'), 400
    runner.request_stop(state)
    if state.get('current_job'):
        crawl_jobs.stop(state['current_job'])
    return jsonify(success=True)


@app.post("/api/amazon-day-data/jobs/<job_id>/resume")
def resume_crawl_job(job_id):
    job = crawl_jobs.get(job_id)
    if not job or not job.get("scope_ids"):
        return jsonify(success=False, message="没有可恢复的任务范围，请重新创建任务"), 400
    if job.get("count_day") != date.today().isoformat():
        return jsonify(success=False, message="只能恢复当天任务"), 400
    try:
        payload = crawl_jobs.payload(job_id)
        for selector in ("data_id", "asin_country", "limit"):
            payload.pop(selector, None)
        payload.update(data_ids=job["scope_ids"], resume=True, count_day=job["count_day"])
        resumed = crawl_jobs.submit(payload, "crawl", run_optimized_crawl)
        return jsonify(success=True, job=resumed), 202
    except CrawlBusy as exc:
        return jsonify(success=False, message=str(exc)), 409


@app.get("/api/day-data")
def day_data():
    try:
        page = max(request.args.get("page", 1, type=int), 1)
        page_size = min(max(request.args.get("page_size", 50, type=int), 1), 200)
        search = (request.args.get("search", "", type=str) or "").strip()
        operation_filter = (request.args.get("operation", "", type=str) or "").strip()
        day_value = (request.args.get("date", "", type=str) or "").strip()
        metric_filter = (request.args.get("metric", "all", type=str) or "all").strip()
        if metric_filter not in DAY_METRIC_FILTERS:
            metric_filter = "all"
        day_filter = day_value or date.today().isoformat()
        offset = (page - 1) * page_size

        config = database_config()
        with connect(**config) as connection:
            ensure_day_data_columns(connection)
            records, previous_by_data_id = fetch_day_records(connection, day_filter)

        if operation_filter:
            records = [
                record
                for record in records
                if str(record.get("operation") or "").strip() == operation_filter
            ]

        summary = {
            "risk_count": 0,
            "attention_count": 0,
            "price_changed": 0,
            "rank_up": 0,
            "star_down": 0,
            "reviews_down": 0,
            "missing": 0,
            "buy_box_missing": 0,
            "comparison_available": 0,
        }
        rows_all = []
        for record in records:
            previous = previous_by_data_id.get(int(record["data_id"])) if record.get("data_id") is not None else None
            flags = build_day_flags(record, previous)
            tags = build_day_tags(flags)
            tag_keys = {tag["key"] for tag in tags}
            if search and not matches_day_search(record, search, tags):
                continue
            for key in summary:
                if flags.get(key):
                    summary[key] += 1
            tag_groups = {tag["group"] for tag in tags}
            has_risk = "risk" in tag_groups
            has_attention = "attention" in tag_groups
            if has_risk:
                summary["risk_count"] += 1
            # Risk takes precedence: a row with both risk and attention tags
            # is counted only once under risk, never again as attention.
            if has_attention and not has_risk:
                summary["attention_count"] += 1
            if metric_filter == "all":
                rows_all.append(serialize_day_row(record, flags))
            elif metric_filter == "risk" and (tag_keys & {"price_changed", "buy_box_missing"}):
                rows_all.append(serialize_day_row(record, flags))
            elif metric_filter == "attention" and not has_risk and (tag_keys & {"rank_up", "star_down", "reviews_down", "missing"}):
                rows_all.append(serialize_day_row(record, flags))
            elif flags.get(metric_filter):
                rows_all.append(serialize_day_row(record, flags))

        total = len(rows_all)
        rows = rows_all[offset : offset + page_size]
        total_pages = max((total + page_size - 1) // page_size, 1)
        return jsonify(
            {
                "success": True,
                "rows": rows,
                "summary": summary,
                "metric": metric_filter,
                "tag_definitions": DAY_TAG_DEFINITIONS,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "date": day_filter,
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.get("/api/day-data/trend")
def day_data_trend():
    try:
        data_id = request.args.get("data_id", type=int)
        if not data_id:
            return jsonify({"success": False, "message": "data_id is required"}), 400

        end_day_value = (request.args.get("end_date", "", type=str) or "").strip()
        if end_day_value:
            try:
                end_day = date.fromisoformat(end_day_value)
            except ValueError:
                return jsonify({"success": False, "message": "invalid end_date"}), 400
        else:
            end_day = date.today()

        date_points = build_date_range(end_day, 7)
        start_day = date_points[0]

        table_day = quote_identifier("asin_day_data")
        table_amazon = quote_identifier(DEFAULT_TABLE)
        sql = (
            f"SELECT d.count_day, d.price, d.star, d.reviews, d.bsr, d.buy_box "
            f"FROM {table_day} d "
            f"JOIN {table_amazon} a ON a.id = d.data_id "
            "WHERE d.data_id = %s AND d.count_day BETWEEN %s AND %s "
            "AND a.status = 1 "
            "ORDER BY d.count_day ASC"
        )

        config = database_config()
        with connect(**config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (data_id, start_day, end_day))
                records = cursor.fetchall()

        record_map: dict[str, dict[str, object]] = {}
        for record in records:
            day_key = serialize_value(record.get("count_day"))
            record_map[day_key] = record

        points = []
        for point_day in date_points:
            key = point_day.isoformat()
            record = record_map.get(key, {})
            has_buy_box = str(record.get("buy_box") or "0").strip() == "1" if record else False
            points.append(
                {
                    "count_day": key,
                    "price": serialize_value(record.get("price")) if has_buy_box else "",
                    "price_value": parse_float_metric(record.get("price")) if has_buy_box else None,
                    "bsr": serialize_value(record.get("bsr")) if record else "",
                    "bsr_value": parse_integer_metric(record.get("bsr")) if record else None,
                    "reviews": serialize_value(record.get("reviews")) if record else "",
                    "reviews_value": parse_integer_metric(record.get("reviews")) if record else None,
                    "star": serialize_value(record.get("star")) if record else "",
                    "star_value": parse_float_metric(record.get("star")) if record else None,
                }
            )

        return jsonify(
            {
                "success": True,
                "data_id": data_id,
                "start_date": start_day.isoformat(),
                "end_date": end_day.isoformat(),
                "points": points,
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.get("/api/stock-data")
def stock_data():
    try:
        page = max(request.args.get("page", 1, type=int), 1)
        page_size = min(max(request.args.get("page_size", 50, type=int), 1), 200)
        search = (request.args.get("search", "", type=str) or "").strip()
        metric = (request.args.get("metric", "", type=str) or "").strip()
        if metric not in STOCK_METRIC_CONDITIONS:
            metric = ""
        offset = (page - 1) * page_size

        table_stock = quote_identifier(STOCK_TABLE)
        table_amazon = quote_identifier(DEFAULT_TABLE)
        params: list[object] = []
        active_condition = "a.`status` = 1"
        latest_date_sql = (
            f"SELECT MAX(s_latest.`stock_date`) FROM {table_stock} s_latest "
            f"JOIN {table_amazon} a_latest ON a_latest.`id` = s_latest.`data_id` "
            "WHERE a_latest.`status` = 1"
        )
        latest_condition = f"s.`stock_date` = ({latest_date_sql})"
        where_parts = [active_condition, latest_condition]
        if search:
            where_parts.append(
                "("
                "LOWER(CAST(COALESCE(a.`name`, '') AS CHAR)) LIKE %s OR "
                "LOWER(CAST(COALESCE(a.`asin`, '') AS CHAR)) LIKE %s OR "
                "LOWER(CAST(COALESCE(s.`seller_sku`, a.`seller_sku`, '') AS CHAR)) LIKE %s OR "
                "LOWER(CAST(COALESCE(a.`country_name`, '') AS CHAR)) LIKE %s OR "
                "LOWER(CAST(COALESCE(a.`group`, '') AS CHAR)) LIKE %s OR "
                "LOWER(CAST(COALESCE(s.`stocking_method`, '') AS CHAR)) LIKE %s"
                ")"
            )
            search_like = f"%{search.lower()}%"
            params.extend([search_like] * 6)
        if metric:
            where_parts.append(STOCK_METRIC_CONDITIONS[metric])

        where_sql = " AND ".join(where_parts)
        from_sql = (
            f"FROM {table_stock} s "
            f"JOIN {table_amazon} a ON a.`id` = s.`data_id` "
            f"WHERE {where_sql}"
        )
        select_sql = f"""
            SELECT
                s.`id` AS stock_id,
                s.`data_id`,
                COALESCE(a.`name`, '') AS name,
                COALESCE(a.`picture`, '') AS picture,
                COALESCE(a.`asin`, '') AS asin,
                COALESCE(a.`link`, '') AS link,
                COALESCE(s.`seller_sku`, a.`seller_sku`, '') AS seller_sku,
                COALESCE(a.`country_name`, '') AS country_name,
                COALESCE(a.`group`, '') AS `group`,
                s.`stock_date`,
                s.`available_inventory`,
                s.`in_transit`,
                s.`pending_order`,
                s.`adjustment_amount`,
                s.`overdue_reassignment`,
                s.`stocking_method`,
                s.`sea_quantity`,
                s.`air_quantity`
            {from_sql}
            ORDER BY s.`stock_date` DESC, s.`id` DESC
            LIMIT %s OFFSET %s
        """

        config = database_config()
        with connect(**config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT ({latest_date_sql}) AS latest_date")
                latest_row = cursor.fetchone() or {}
                latest_date = serialize_value(latest_row.get("latest_date"))

                summary_fields = ", ".join(
                    f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END) AS {quote_identifier(key)}"
                    for key, condition in STOCK_METRIC_CONDITIONS.items()
                )
                cursor.execute(
                    f"SELECT {summary_fields} FROM {table_stock} s "
                    f"JOIN {table_amazon} a ON a.`id` = s.`data_id` "
                    f"WHERE {active_condition} AND {latest_condition}"
                )
                summary_record = cursor.fetchone() or {}

                cursor.execute(f"SELECT COUNT(*) AS total {from_sql}", params)
                total = int(cursor.fetchone()["total"])

                cursor.execute(select_sql, [*params, page_size, offset])
                records = cursor.fetchall()

        rows = [serialize_stock_row(record) for record in records]
        summary = {
            key: int(summary_record.get(key) or 0)
            for key in STOCK_METRIC_CONDITIONS
        }
        total_pages = max((total + page_size - 1) // page_size, 1)
        return jsonify(
            {
                "success": True,
                "rows": rows,
                "summary": summary,
                "metric": metric,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "date": latest_date,
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.get("/api/stock-data/history")
def stock_data_history():
    try:
        seller_sku = (request.args.get("seller_sku", "", type=str) or "").strip()
        if not seller_sku:
            return jsonify({"success": False, "message": "seller_sku is required"}), 400

        table_stock = quote_identifier(STOCK_TABLE)
        table_amazon = quote_identifier(DEFAULT_TABLE)
        sql = f"""
            SELECT
                s.`id` AS stock_id,
                s.`data_id`,
                COALESCE(a.`name`, '') AS name,
                COALESCE(a.`picture`, '') AS picture,
                COALESCE(a.`asin`, '') AS asin,
                COALESCE(a.`link`, '') AS link,
                COALESCE(s.`seller_sku`, a.`seller_sku`, '') AS seller_sku,
                COALESCE(a.`country_name`, '') AS country_name,
                COALESCE(a.`group`, '') AS `group`,
                s.`stock_date`,
                s.`available_inventory`,
                s.`in_transit`,
                s.`pending_order`,
                s.`adjustment_amount`,
                s.`overdue_reassignment`,
                s.`stocking_method`,
                s.`sea_quantity`,
                s.`air_quantity`
            FROM {table_stock} s
            JOIN {table_amazon} a ON a.`id` = s.`data_id`
            WHERE a.`status` = 1
              AND COALESCE(s.`seller_sku`, a.`seller_sku`, '') = %s
            ORDER BY s.`stock_date` DESC, s.`id` DESC
        """

        config = database_config()
        with connect(**config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (seller_sku,))
                records = cursor.fetchall()

        return jsonify(
            {
                "success": True,
                "seller_sku": seller_sku,
                "rows": [serialize_stock_row(record) for record in records],
                "total": len(records),
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.post("/api/stock-data/import")
def stock_data_import():
    uploaded_file = request.files.get("file")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"success": False, "message": "请选择要导入的 Excel 文件"}), 400

    try:
        df = pd.read_excel(uploaded_file, sheet_name=0, dtype=object, engine="openpyxl")
        df.columns = [str(column).strip() for column in df.columns]
        df = df.where(pd.notna(df), None)
        header_lookup = build_header_lookup(list(df.columns))
        excel_mapping: dict[str, str] = {}
        missing_headers: list[str] = []
        for expected_header, db_column in STOCK_IMPORT_HEADER_TO_COLUMN.items():
            actual_header = find_excel_header(header_lookup, expected_header)
            if actual_header:
                excel_mapping[db_column] = actual_header
            else:
                missing_headers.append(expected_header)

        if "seller_sku" not in excel_mapping:
            return jsonify({"success": False, "message": "导入文件缺少 seller_sku 表头"}), 400

        config = database_config()
        with connect(**config) as connection:
            stock_columns = set(insertable_columns(fetch_columns(connection, STOCK_TABLE)))
            required_columns = {"data_id", "seller_sku", "stock_date"}
            missing_db_columns = sorted(required_columns - stock_columns)
            if missing_db_columns:
                raise RuntimeError(f"stock_data 表缺少字段: {', '.join(missing_db_columns)}")

            sku_header = excel_mapping["seller_sku"]
            input_skus: list[str] = []
            for _, excel_row in df.iterrows():
                seller_sku = normalize_stock_text(excel_row.get(sku_header))
                if seller_sku and seller_sku not in input_skus:
                    input_skus.append(seller_sku)

            sku_to_data_id: dict[str, int] = {}
            if input_skus:
                placeholders = ", ".join(["%s"] * len(input_skus))
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT `id`, `seller_sku` FROM {quote_identifier(DEFAULT_TABLE)} "
                        f"WHERE `status` = 1 "
                        f"AND `seller_sku` IN ({placeholders})",
                        input_skus,
                    )
                    for record in cursor.fetchall():
                        sku = normalize_stock_text(record.get("seller_sku"))
                        if sku and sku not in sku_to_data_id:
                            sku_to_data_id[sku] = int(record["id"])

            import_date = date.today().isoformat()
            rows: list[dict[str, object]] = []
            skipped_missing_sku = 0
            skipped_unmatched_sku = 0
            skipped_empty_rows = 0

            for _, excel_row in df.iterrows():
                seller_sku = normalize_stock_text(excel_row.get(sku_header))
                if not seller_sku:
                    skipped_missing_sku += 1
                    continue
                data_id = sku_to_data_id.get(seller_sku)
                if not data_id:
                    skipped_unmatched_sku += 1
                    continue

                row: dict[str, object] = {
                    "data_id": data_id,
                    "seller_sku": seller_sku,
                    "stock_date": import_date,
                }
                data_field_count = 0
                for db_column, excel_header in excel_mapping.items():
                    if db_column == "seller_sku" or db_column not in stock_columns:
                        continue
                    value = excel_row.get(excel_header)
                    normalized = (
                        normalize_stock_number(value)
                        if db_column in STOCK_IMPORT_NUMERIC_COLUMNS
                        else normalize_stock_text(value)
                    )
                    row[db_column] = normalized
                    if normalized not in (None, ""):
                        data_field_count += 1

                if data_field_count == 0:
                    skipped_empty_rows += 1
                    continue
                rows.append(row)

            inserted_rows = 0
            if rows:
                insert_columns = sorted({column for row in rows for column in row if column in stock_columns})
                placeholders = ", ".join(["%s"] * len(insert_columns))
                columns_sql = ", ".join(quote_identifier(column) for column in insert_columns)
                insert_sql = f"INSERT INTO {quote_identifier(STOCK_TABLE)} ({columns_sql}) VALUES ({placeholders})"
                values = [[row.get(column) for column in insert_columns] for row in rows]
                with connection.cursor() as cursor:
                    cursor.executemany(insert_sql, values)
                    inserted_rows = cursor.rowcount
                connection.commit()
            else:
                connection.rollback()

        return jsonify(
            {
                "success": True,
                "message": "库存数据导入完成",
                "stats": {
                    "total_rows": len(df),
                    "inserted_rows": inserted_rows,
                    "skipped_missing_sku": skipped_missing_sku,
                    "skipped_unmatched_sku": skipped_unmatched_sku,
                    "skipped_empty_rows": skipped_empty_rows,
                },
                "missing_headers": missing_headers,
                "import_date": import_date,
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.get("/api/todo-list")
def todo_list():
    try:
        table_sql = quote_identifier(TODO_TABLE)
        columns_sql = ", ".join(quote_identifier(column) for column in TODO_COLUMNS)
        metric = (request.args.get("metric", "", type=str) or "").strip()
        if metric not in {"", "pending"}:
            metric = ""
        where_sql = "WHERE COALESCE(`status`, 0) <> 1" if metric == "pending" else ""
        sql = (
            f"SELECT {columns_sql} FROM {table_sql} {where_sql} "
            "ORDER BY "
            "CASE WHEN COALESCE(`status`, 0) = 1 THEN 1 ELSE 0 END ASC, "
            "CASE WHEN COALESCE(`status`, 0) <> 1 THEN COALESCE(`important`, 0) ELSE 0 END DESC, "
            "CASE WHEN COALESCE(`status`, 0) <> 1 AND `end_date` IS NULL THEN 1 ELSE 0 END ASC, "
            "`end_date` ASC, `id` DESC"
        )
        config = database_config()
        with connect(**config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                records = cursor.fetchall()

        rows = [
            {column: serialize_value(record.get(column)) for column in TODO_COLUMNS}
            for record in records
        ]
        return jsonify({
            "success": True,
            "rows": rows,
            "total": len(rows),
            "metric": metric,
        })
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.post("/api/todo-list")
def create_todo():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"success": False, "message": "JSON object is required"}), 400

    content = str(payload.get("content") or "").strip()
    remark = str(payload.get("remark") or "").strip()
    if not content:
        return jsonify({"success": False, "message": "事项内容不能为空"}), 400
    if len(content) > 500:
        return jsonify({"success": False, "message": "事项内容不能超过 500 个字符"}), 400
    if len(remark) > 1000:
        return jsonify({"success": False, "message": "备注不能超过 1000 个字符"}), 400

    try:
        status = int(str(payload.get("status", 0)).strip())
        important = int(str(payload.get("important", 3)).strip())
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "状态或重要程度格式不正确"}), 400
    if status not in {0, 1}:
        return jsonify({"success": False, "message": "状态只能是未完成或已完成"}), 400
    if important not in {1, 2, 3, 4, 5}:
        return jsonify({"success": False, "message": "重要程度必须在 1 到 5 之间"}), 400

    normalized_dates: dict[str, str | None] = {}
    for field_name, default_value in (("create_date", date.today().isoformat()), ("end_date", None)):
        raw_value = str(payload.get(field_name) or default_value or "").strip()
        if not raw_value:
            normalized_dates[field_name] = None
            continue
        try:
            normalized_dates[field_name] = date.fromisoformat(raw_value).isoformat()
        except ValueError:
            label = "创建日期" if field_name == "create_date" else "截止日期"
            return jsonify({"success": False, "message": f"{label}格式不正确"}), 400

    table_sql = quote_identifier(TODO_TABLE)
    config = database_config()
    try:
        with connect(**config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {table_sql} "
                    "(`create_date`, `content`, `status`, `end_date`, `important`, `remark`) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        normalized_dates["create_date"],
                        content,
                        status,
                        normalized_dates["end_date"],
                        important,
                        remark or None,
                    ),
                )
                record_id = cursor.lastrowid
                connection.commit()
                cursor.execute(
                    f"SELECT * FROM {table_sql} WHERE `id` = %s LIMIT 1",
                    (record_id,),
                )
                record = cursor.fetchone()

        row = {column: serialize_value(record.get(column)) for column in TODO_COLUMNS}
        return jsonify({"success": True, "row": row, "message": "待办事项新增成功"}), 201
    except Exception as exc:
        try:
            connection.rollback()
        except (NameError, UnboundLocalError):
            pass
        return jsonify({"success": False, "message": str(exc)}), 500


@app.patch("/api/todo-list/<int:record_id>/complete")
def complete_todo(record_id: int):
    table_sql = quote_identifier(TODO_TABLE)
    config = database_config()
    try:
        with connect(**config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT `id`, `status` FROM {table_sql} WHERE `id` = %s LIMIT 1",
                    (record_id,),
                )
                record = cursor.fetchone()
                if not record:
                    return jsonify({"success": False, "message": "待办事项不存在"}), 404

                if str(record.get("status") or "0") != "1":
                    cursor.execute(
                        f"UPDATE {table_sql} SET `status` = 1 WHERE `id` = %s",
                        (record_id,),
                    )
                    connection.commit()

                cursor.execute(
                    f"SELECT * FROM {table_sql} WHERE `id` = %s LIMIT 1",
                    (record_id,),
                )
                updated_record = cursor.fetchone()

        row = {column: serialize_value(updated_record.get(column)) for column in TODO_COLUMNS}
        return jsonify({"success": True, "row": row, "message": "待办事项已完成"})
    except Exception as exc:
        try:
            connection.rollback()
        except (NameError, UnboundLocalError):
            pass
        return jsonify({"success": False, "message": str(exc)}), 500


from amazon_importer.order_data import register_order_routes
from amazon_importer.product_operation_logs import register_product_operation_routes

register_order_routes(app, lambda: connect(**database_config()))
register_product_operation_routes(app, lambda: connect(**database_config()))


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", "6471")),
        debug=truthy(os.getenv("FLASK_DEBUG", "")),
        use_reloader=False,
    )
