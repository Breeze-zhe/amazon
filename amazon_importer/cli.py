from __future__ import annotations

import argparse
import sys

from .importer import (
    DEFAULT_DATABASE,
    DEFAULT_EXCEL_PATH,
    DEFAULT_HOST,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_TABLE,
    DEFAULT_USER,
    connect,
    import_excel_data,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Amazon Excel data into MySQL amazon_data.")
    parser.add_argument("--excel", default=DEFAULT_EXCEL_PATH, help="Excel file path.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="MySQL host.")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="MySQL port.")
    parser.add_argument("--user", default=DEFAULT_USER, help="MySQL user.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="MySQL password.")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="MySQL database/schema name.")
    parser.add_argument("--table", default=DEFAULT_TABLE, help="Target table name.")
    parser.add_argument("--batch-size", default=500, type=int, help="Insert batch size.")
    parser.add_argument("--dry-run", action="store_true", help="Read and check data without inserting.")
    parser.add_argument("--show-unmapped", action="store_true", help="Print unmapped Excel headers.")
    return parser.parse_args(argv)


def print_summary(stats, unmapped_headers: list[str], dry_run: bool) -> None:
    action = "would insert" if dry_run else "inserted"
    update_action = "would update existing rows" if dry_run else "updated existing rows"
    print("Import summary")
    print(f"  total rows: {stats.total_rows}")
    print(f"  prepared rows: {stats.prepared_rows}")
    print(f"  {action}: {stats.inserted_rows}")
    print(f"  matched existing asin+country: {stats.matched_existing}")
    print(f"  {update_action}: {stats.updated_existing_rows}")
    print(f"  obsoleted rows: {stats.obsoleted_rows}")
    print(f"  reactivated rows: {stats.reactivated_rows}")
    print(f"  skipped duplicate in file: {stats.skipped_duplicate_in_file}")
    print(f"  skipped missing asin/country: {stats.skipped_missing_key}")
    print(f"  unmapped Excel headers: {len(unmapped_headers)}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        with connect(args.host, args.port, args.user, args.password, args.database) as connection:
            stats, unmapped_headers = import_excel_data(
                connection,
                excel_path=args.excel,
                table=args.table,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
            )

            print_summary(stats, unmapped_headers, args.dry_run)
            if args.show_unmapped and unmapped_headers:
                print("Unmapped headers:")
                for header in unmapped_headers:
                    print(f"  - {header}")

    except Exception as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
