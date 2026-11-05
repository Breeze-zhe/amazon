"""CRUD routes for product operation history, linked by amazon_data.asin_country."""
from __future__ import annotations

from datetime import date
import json

from flask import jsonify, request


TABLE = "amz_product_operation_log"
TYPE_GROUP = "商品操作类型"


def parse_log_payload(payload):
    if not isinstance(payload, dict):
        return None, (jsonify(success=False, message="请求内容格式不正确"), 400)
    asin_country = str(payload.get("asin_country") or "").strip()
    remark = str(payload.get("remark") or "").strip()
    try:
        tag_id = int(payload.get("tag_id"))
    except (TypeError, ValueError):
        return None, (jsonify(success=False, message="请选择有效的操作类型"), 400)
    try:
        operate_date = date.fromisoformat(str(payload.get("operate_date") or "").strip())
    except ValueError:
        return None, (jsonify(success=False, message="请选择有效的操作日期"), 400)
    if not asin_country or len(asin_country) > 100:
        return None, (jsonify(success=False, message="请选择有效商品"), 400)
    if tag_id <= 0 or len(remark) > 4000:
        return None, (jsonify(success=False, message="操作类型无效或备注超过 4000 个字符"), 400)
    return {"asin_country": asin_country, "tag_id": tag_id, "remark": remark,
            "operate_date": operate_date}, None


def register_product_operation_routes(app, connection_factory):
    @app.get("/api/product-operation-logs")
    def product_operation_log_list():
        try:
            page = max(request.args.get("page", 1, type=int), 1)
            page_size = min(200, max(1, request.args.get("page_size", 50, type=int)))
            search = (request.args.get("search", "", type=str) or "").strip()
            tag_text = (request.args.get("tag_id", "", type=str) or "").strip()
            conditions, params = ["g.group_name=%s"], [TYPE_GROUP]
            if search:
                like = f"%{search}%"
                conditions.append("(COALESCE(a.name,'') LIKE %s OR COALESCE(a.asin,l.asin) LIKE %s "
                                  "OR COALESCE(t.tag_name,'') LIKE %s OR l.remark LIKE %s)")
                params.extend([like] * 4)
            if tag_text:
                try:
                    tag_id = int(tag_text)
                except ValueError:
                    return jsonify(success=False, message="操作类型无效"), 400
                conditions.append("l.tag_id=%s")
                params.append(tag_id)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            joins = (" FROM amz_product_operation_log l "
                     "LEFT JOIN amazon_data a ON a.asin_country=l.asin_country "
                     "JOIN amz_tag t ON t.id=l.tag_id "
                     "JOIN amz_tag_group g ON g.id=t.group_id")
            with connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) AS total" + joins + where, params)
                    total = int(cursor.fetchone()["total"])
                    cursor.execute(
                        "SELECT l.id, l.asin_country, COALESCE(a.name,'') AS name, "
                        "COALESCE(a.picture,'') AS picture, COALESCE(a.asin,l.asin) AS asin, "
                        "l.tag_id, COALESCE(t.tag_name,'(类型已停用)') AS operation_type, "
                        "COALESCE(t.color,'') AS operation_type_color, l.remark, "
                        "l.operate_date, l.create_time" + joins + where +
                        " ORDER BY l.operate_date DESC, l.id DESC LIMIT %s OFFSET %s",
                        [*params, page_size, (page - 1) * page_size],
                    )
                    rows = [{key: value.isoformat() if hasattr(value, "isoformat") else value
                             for key, value in row.items()} for row in cursor.fetchall()]
            return jsonify(success=True, rows=rows, total=total, page=page, page_size=page_size,
                           total_pages=max(1, (total + page_size - 1) // page_size))
        except Exception as exc:
            return jsonify(success=False, message=str(exc)), 500

    @app.get("/api/product-operation-logs/options")
    def product_operation_log_options():
        try:
            with connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT t.id, t.tag_name FROM amz_tag t "
                                   "JOIN amz_tag_group g ON g.id=t.group_id "
                                   "WHERE g.group_name=%s AND g.status=1 AND t.status=1 "
                                   "ORDER BY t.sort_no, t.id", (TYPE_GROUP,))
                    types = cursor.fetchall()
                    cursor.execute("SELECT asin_country, asin, name, country_name FROM amazon_data "
                                   "WHERE status=1 AND asin_country IS NOT NULL AND TRIM(asin_country)<>'' "
                                   "ORDER BY name, asin, country_name")
                    products = cursor.fetchall()
            return jsonify(success=True, types=types, products=products)
        except Exception as exc:
            return jsonify(success=False, message=str(exc)), 500

    def validated_payload():
        values, error = parse_log_payload(request.get_json(silent=True))
        if error:
            return None, error
        with connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, asin_country, asin, name FROM amazon_data "
                               "WHERE asin_country=%s AND status=1 LIMIT 1", (values["asin_country"],))
                product = cursor.fetchone()
                if not product:
                    return None, (jsonify(success=False, message="商品不存在或已停用"), 400)
                cursor.execute("SELECT t.id FROM amz_tag t JOIN amz_tag_group g ON g.id=t.group_id "
                               "WHERE t.id=%s AND t.status=1 AND g.status=1 AND g.group_name=%s LIMIT 1",
                               (values["tag_id"], TYPE_GROUP))
                if not cursor.fetchone():
                    return None, (jsonify(success=False, message="所选操作类型不存在或已停用"), 400)
        values["asin"] = product["asin"]
        values["product_info"] = json.dumps({"asin_country": product["asin_country"]},
                                             ensure_ascii=False, separators=(",", ":"))
        return values, None

    @app.post("/api/product-operation-logs")
    def product_operation_log_create():
        values, error = validated_payload()
        if error:
            return error
        try:
            with connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO amz_product_operation_log "
                                   "(asin, asin_country, tag_id, remark, product_info, operate_date) "
                                   "VALUES (%s,%s,%s,%s,%s,%s)",
                                   (values["asin"], values["asin_country"], values["tag_id"], values["remark"],
                                    values["product_info"], values["operate_date"]))
                    record_id = cursor.lastrowid
                connection.commit()
            return jsonify(success=True, id=record_id), 201
        except Exception as exc:
            return jsonify(success=False, message=str(exc)), 500

    @app.patch("/api/product-operation-logs/<int:record_id>")
    def product_operation_log_update(record_id):
        values, error = validated_payload()
        if error:
            return error
        try:
            with connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE amz_product_operation_log SET asin=%s, asin_country=%s, tag_id=%s, "
                                   "remark=%s, product_info=%s, operate_date=%s WHERE id=%s",
                                   (values["asin"], values["asin_country"], values["tag_id"], values["remark"],
                                    values["product_info"], values["operate_date"], record_id))
                    if cursor.rowcount == 0:
                        cursor.execute("SELECT id FROM amz_product_operation_log WHERE id=%s", (record_id,))
                        if not cursor.fetchone():
                            return jsonify(success=False, message="商品操作记录不存在"), 404
                connection.commit()
            return jsonify(success=True)
        except Exception as exc:
            return jsonify(success=False, message=str(exc)), 500
