"""Run once before serving order queries; safe to rerun. Never rewrites order rows."""
import json

from amazon_importer.importer import connect
from amazon_importer.order_data import migrate_order_schema
from web_app import database_config


if __name__ == "__main__":
    with connect(**database_config()) as connection:
        print(json.dumps(migrate_order_schema(connection)))
