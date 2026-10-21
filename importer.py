from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql

from .amazon_sites import build_product_link


DEFAULT_EXCEL_PATH = r"C:\Users\Administrator\Desktop\运营链接-模板（全）.xlsx"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3306
DEFAULT_USER = "root"
DEFAULT_PASSWORD = "rootyibai"
DEFAULT_DATABASE = "amazon"
DEFAULT_TABLE = "amazon_data"

COUNTRY_SOURCE_HEADER = "简称"
LABEL_VALUES = {"新品链接", "动销活动", "清仓", "链接异常", "链接挂了"}

EXPLICIT_HEADER_TO_COLUMN = {
    "ASIN": "asin",
    "seller_sku": "seller_sku",
    "销售建议": "remark",
    "国家": "country_name",
    "产品": "name",
    "运营": "operation",
    "小组": "group",
    "接手月份": "select_date",
    "ASIN+国家": "asin_country",
    "标题": "title",
    "五点": "point",
    "A+": "senior_a",
    "广告": "ad_activity",
    "广告活动": "ad_activity",
    "活动": "deal",
    "活动开始时间": "deal_date",
    "活动开始日期": "deal_date",
    "活动结束时间": "deal_end_date",
    "活动结束日期": "deal_end_date",
    "库存备注": "stock_remark",
    "label": "label",
    "图床": "picture",
    "图床链接": "picture",
    "图片": "picture",
    "图片链接": "picture",
    "pickture": "picture",
    "picture": "picture",
    "标签": "label",
}

SPECIAL_HEADER_TO_COLUMN = {
    "销售建议": "remark",
    "国家": "country_name",
}

COLUMN_ALIASES = {
    "asin": ["ASIN"],
    "country_name": ["国家"],
    "remark": ["销售建议"],
    "title": ["标题"],
    "name": ["产品"],
    "seller_sku": ["seller_sku"],
    "point": ["五点"],
    "senior_a": ["A+"],
    "group": ["小组"],
    "select_date": ["接手月份"],
    "obsolete_date": ["淘汰月份"],
    "status": ["状态"],
    "ad_activity": ["广告"],
    "deal": ["活动"],
    "deal_date": ["活动开始时间"],
    "asin_country": ["ASIN+国家"],
}

KEY_COLUMNS = {"asin", "country"}
SOURCE_TEXT_COLUMNS = {
    "title",
    "point",
    "senior_a",
    "ad_activity",
    "deal",
    "deal_date",
    "deal_end_date",
    "stock_remark",
}
DEFAULT_STATUS = 1


@dataclass(frozen=True)
class DbColumn:
    name: str
    extra: str


@dataclass
class ImportStats:
    total_rows: int = 0
    prepared_rows: int = 0
    inserted_rows: int = 0
    matched_existing: int = 0
    updated_existing_rows: int = 0
    obsoleted_rows: int = 0
    reactivated_rows: int = 0
    skipped_existing: int = 0
    skipped_duplicate_in_file: int = 0
    skipped_missing_key: int = 0


def normalize_name(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.strip().lower()
    text = re.sub(r"[\s_\-./\\:：,，;；()（）\[\]【】]+", "", text)
    return text


def clean_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


def normalize_date(value: Any) -> str | None:
    value = clean_value(value)
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return str(value)
    return parsed.date().isoformat()


def normalize_db_value(column: str, value: Any) -> Any:
    if column in SOURCE_TEXT_COLUMNS:
        return clean_value(value)
    if column.endswith("_date") or column == "select_date":
        return normalize_date(value)
    if column == "label":
        normalized = clean_value(value)
        return normalized if normalized in LABEL_VALUES else None
    return clean_value(value)


def normalize_boolean(value: Any) -> int | None:
    value = clean_value(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip().lower()
    if text in {"是", "有", "已完成", "完成", "yes", "y", "true", "1"}:
        return 1
    if text in {"否", "无", "未完成", "未做", "no", "n", "false", "0"}:
        return 0
    return normalize_number(value)


def normalize_number(value: Any) -> int | None:
    value = clean_value(value)
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def read_excel_first_sheet(excel_path: str) -> pd.DataFrame:
    path = Path(excel_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    df = pd.read_excel(path, sheet_name=0, dtype=str, engine="openpyxl")
    df.columns = [str(column).strip() for column in df.columns]
    df = df.where(pd.notna(df), None)
    return df


def connect(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
):
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_columns(connection, table: str) -> list[DbColumn]:
    sql = """
        SELECT COLUMN_NAME, EXTRA
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (table,))
        rows = cursor.fetchall()

    if not rows:
        raise RuntimeError(f"Table not found in selected database: {table}")

    return [DbColumn(name=row["COLUMN_NAME"], extra=row.get("EXTRA") or "") for row in rows]


def insertable_columns(columns: list[DbColumn]) -> list[str]:
    result: list[str] = []
    for column in columns:
        extra = column.extra.lower()
        if "auto_increment" in extra or "generated" in extra:
            continue
        result.append(column.name)
    return result


def build_excel_header_index(headers: list[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for header in headers:
        normalized = normalize_name(header)
        if normalized and normalized not in index:
            index[normalized] = header
    return index


def find_header_for_column(column: str, header_index: dict[str, str]) -> str | None:
    normalized_column = normalize_name(column)
    if normalized_column in header_index:
        return header_index[normalized_column]

    for alias in COLUMN_ALIASES.get(column, []):
        normalized_alias = normalize_name(alias)
        if normalized_alias in header_index:
            return header_index[normalized_alias]

    return None


def build_column_mapping(
    excel_headers: list[str],
    db_columns: list[str],
) -> tuple[dict[str, str], list[str]]:
    header_index = build_excel_header_index(excel_headers)
    db_column_set = set(db_columns)
    mapping: dict[str, str] = {}

    for excel_header, db_column in {
        **EXPLICIT_HEADER_TO_COLUMN,
        **SPECIAL_HEADER_TO_COLUMN,
    }.items():
        if db_column in db_column_set and normalize_name(excel_header) in header_index:
            mapping[db_column] = header_index[normalize_name(excel_header)]

    for db_column in db_columns:
        if db_column in mapping or db_column == "country":
            continue
        header = find_header_for_column(db_column, header_index)
        if header:
            mapping[db_column] = header

    mapped_headers = set(mapping.values())
    unmapped_headers = [
        header
        for header in excel_headers
        if header not in mapped_headers and normalize_name(header) != normalize_name(COUNTRY_SOURCE_HEADER)
    ]
    return mapping, unmapped_headers


def get_cell(row: pd.Series, header: str | None) -> Any:
    if not header:
        return None
    return clean_value(row.get(header))


def country_from_short_name(short_name: Any) -> str | None:
    value = clean_value(short_name)
    if value is None:
        return None
    text = str(value).strip()
    return text[-1:] or None


def collect_candidate_rows(
    df: pd.DataFrame,
    mapping: dict[str, str],
    db_columns: list[str],
) -> tuple[list[dict[str, Any]], ImportStats]:
    stats = ImportStats(total_rows=len(df))
    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    short_header = build_excel_header_index(list(df.columns)).get(normalize_name(COUNTRY_SOURCE_HEADER))
    asin_header = mapping.get("asin")

    for _, excel_row in df.iterrows():
        asin = get_cell(excel_row, asin_header)
        country = country_from_short_name(get_cell(excel_row, short_header))

        if not asin or not country:
            stats.skipped_missing_key += 1
            continue

        key = (str(asin).strip(), str(country).strip())
        if key in seen_keys:
            stats.skipped_duplicate_in_file += 1
            continue
        seen_keys.add(key)

        db_row: dict[str, Any] = {}
        for db_column, excel_header in mapping.items():
            # Lifecycle fields are managed by synchronization, not by Excel.
            if db_column in {"status", "obsolete_date"}:
                continue
            value = normalize_db_value(db_column, get_cell(excel_row, excel_header))
            if value is not None:
                db_row[db_column] = value

        if "asin" in db_columns:
            db_row["asin"] = key[0]
        if "country" in db_columns:
            db_row["country"] = key[1]
        if "status" in db_columns:
            db_row["status"] = DEFAULT_STATUS
        if "link" in db_columns:
            db_row["link"] = build_product_link(key[1], key[0])

        rows.append(db_row)

    stats.prepared_rows = len(rows)
    return rows, stats


def fetch_existing_keys(connection, table: str, rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    if not rows:
        return set()

    keys = [(str(row["asin"]), str(row["country"])) for row in rows]
    existing: set[tuple[str, str]] = set()
    select_sql = (
        f"SELECT asin, country FROM {quote_identifier(table)} "
        "WHERE asin = %s AND country = %s LIMIT 1"
    )

    with connection.cursor() as cursor:
        for asin, country in keys:
            cursor.execute(select_sql, (asin, country))
            row = cursor.fetchone()
            if row:
                existing.add((str(row["asin"]), str(row["country"])))

    return existing


def fetch_existing_rows(connection, table: str) -> dict[tuple[str, str], dict[str, Any]]:
    existing: dict[tuple[str, str], dict[str, Any]] = {}
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT * FROM {quote_identifier(table)}")
        for row in cursor.fetchall():
            key = (str(row.get("asin") or "").strip(), str(row.get("country") or "").strip())
            if key not in existing:
                existing[key] = row
    return existing


def build_existing_updates(
    candidate_row: dict[str, Any],
    existing_row: dict[str, Any],
) -> tuple[dict[str, Any], bool, bool]:
    updates: dict[str, Any] = {}
    for column, value in candidate_row.items():
        if column in KEY_COLUMNS or column in {"id", "status", "obsolete_date"}:
            continue
        if value is None or value == "":
            continue
        current_value = existing_row.get(column)
        if str(current_value or "") != str(value):
            updates[column] = value

    imported_fields_changed = bool(updates)
    status_value = str(
        existing_row.get("status") if existing_row.get("status") is not None else ""
    ).strip()
    reactivated = status_value == "0"
    if status_value != "1":
        updates["status"] = DEFAULT_STATUS
    if existing_row.get("obsolete_date") not in (None, ""):
        updates["obsolete_date"] = None
    return updates, imported_fields_changed, reactivated


def build_obsolete_updates(
    existing_row: dict[str, Any],
    obsolete_day: date,
) -> tuple[dict[str, Any], bool]:
    status_value = str(
        existing_row.get("status") if existing_row.get("status") is not None else ""
    ).strip()
    newly_obsoleted = status_value != "0"
    updates: dict[str, Any] = {}
    if newly_obsoleted:
        updates["status"] = 0
        updates["obsolete_date"] = obsolete_day
    elif existing_row.get("obsolete_date") in (None, ""):
        updates["obsolete_date"] = obsolete_day
    return updates, newly_obsoleted


def update_row_by_key(
    connection,
    table: str,
    key: tuple[str, str],
    updates: dict[str, Any],
) -> int:
    if not updates:
        return 0
    set_sql = ", ".join(f"{quote_identifier(column)} = %s" for column in updates)
    sql = (
        f"UPDATE {quote_identifier(table)} "
        f"SET {set_sql} "
        "WHERE asin = %s AND country = %s"
    )
    params = [*updates.values(), *key]
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.rowcount


def update_existing_imported_fields(
    connection,
    table: str,
    rows: list[dict[str, Any]],
    existing_rows: dict[tuple[str, str], dict[str, Any]],
) -> tuple[int, int]:
    updated = 0
    reactivated = 0
    for row in rows:
        key = (str(row["asin"]), str(row["country"]))
        existing_row = existing_rows.get(key)
        if not existing_row:
            continue
        updates, imported_fields_changed, row_reactivated = build_existing_updates(row, existing_row)
        update_row_by_key(connection, table, key, updates)
        updated += int(imported_fields_changed)
        reactivated += int(row_reactivated)
    return updated, reactivated


def mark_obsolete_rows(
    connection,
    table: str,
    source_keys: set[tuple[str, str]],
    existing_rows: dict[tuple[str, str], dict[str, Any]],
    obsolete_day: date,
) -> int:
    obsoleted = 0
    for key, existing_row in existing_rows.items():
        if key in source_keys:
            continue
        updates, newly_obsoleted = build_obsolete_updates(existing_row, obsolete_day)
        update_row_by_key(connection, table, key, updates)
        obsoleted += int(newly_obsoleted)
    return obsoleted


def insert_rows(connection, table: str, rows: list[dict[str, Any]], batch_size: int) -> int:
    if not rows:
        return 0

    all_columns = sorted({column for row in rows for column in row})
    placeholders = ", ".join(["%s"] * len(all_columns))
    columns_sql = ", ".join(quote_identifier(column) for column in all_columns)
    sql = f"INSERT INTO {quote_identifier(table)} ({columns_sql}) VALUES ({placeholders})"

    inserted = 0
    with connection.cursor() as cursor:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            values = [[row.get(column) for column in all_columns] for row in batch]
            cursor.executemany(sql, values)
            inserted += cursor.rowcount

    return inserted


def import_excel_data(
    connection,
    excel_path: str = DEFAULT_EXCEL_PATH,
    table: str = DEFAULT_TABLE,
    batch_size: int = 500,
    dry_run: bool = False,
) -> tuple[ImportStats, list[str]]:
    """Synchronize the first Excel sheet with product data in one transaction."""
    df = read_excel_first_sheet(excel_path)
    columns = fetch_columns(connection, table)
    db_columns = insertable_columns(columns)
    mapping, unmapped_headers = build_column_mapping(list(df.columns), db_columns)

    missing_required = [
        column
        for column in ("asin", "country", "status", "obsolete_date")
        if column not in db_columns
    ]
    if missing_required:
        raise RuntimeError(f"Target table is missing required columns: {', '.join(missing_required)}")

    header_index = build_excel_header_index(list(df.columns))
    missing_source_headers = []
    if "asin" not in mapping:
        missing_source_headers.append("ASIN")
    if normalize_name(COUNTRY_SOURCE_HEADER) not in header_index:
        missing_source_headers.append(COUNTRY_SOURCE_HEADER)
    if missing_source_headers:
        raise RuntimeError(
            f"Source Excel is missing required headers: {', '.join(missing_source_headers)}"
        )

    rows, stats = collect_candidate_rows(df, mapping, db_columns)
    if not rows:
        raise RuntimeError("Source Excel contains no valid ASIN + country links; database was not changed")

    existing_rows = fetch_existing_rows(connection, table)
    insert_candidates = [
        row
        for row in rows
        if (str(row["asin"]), str(row["country"])) not in existing_rows
    ]
    stats.matched_existing = len(rows) - len(insert_candidates)
    source_keys = {(str(row["asin"]), str(row["country"])) for row in rows}
    sync_day = date.today()

    for row in rows:
        existing_row = existing_rows.get((str(row["asin"]), str(row["country"])))
        if not existing_row:
            continue
        _, imported_fields_changed, reactivated = build_existing_updates(row, existing_row)
        stats.updated_existing_rows += int(imported_fields_changed)
        stats.reactivated_rows += int(reactivated)
    for key, existing_row in existing_rows.items():
        if key not in source_keys:
            _, newly_obsoleted = build_obsolete_updates(existing_row, sync_day)
            stats.obsoleted_rows += int(newly_obsoleted)

    if dry_run:
        stats.inserted_rows = len(insert_candidates)
        connection.rollback()
        return stats, unmapped_headers

    try:
        updated, reactivated = update_existing_imported_fields(
            connection, table, rows, existing_rows,
        )
        obsoleted = mark_obsolete_rows(
            connection, table, source_keys, existing_rows, sync_day,
        )
        stats.updated_existing_rows = updated
        stats.reactivated_rows = reactivated
        stats.obsoleted_rows = obsoleted
        stats.inserted_rows = insert_rows(connection, table, insert_candidates, batch_size)
        connection.commit()
        return stats, unmapped_headers
    except Exception:
        connection.rollback()
        raise
