#!/usr/bin/env python3
"""
Run a SQL file against Microsoft SQL Server and write results to an output file.

Usage:
    mssql_runner.py <sql_file> <output_file>

Environment variables required:
    MSSQL_HOST, MSSQL_PORT, MSSQL_DATABASE, MSSQL_USERNAME, MSSQL_PASSWORD
"""

import os
import sys
import re
import pymssql


def get_connection():
    """Build connection using env vars."""
    required = ["MSSQL_HOST", "MSSQL_PORT", "MSSQL_DATABASE",
                "MSSQL_USERNAME", "MSSQL_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(f"Error: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    return pymssql.connect(
        server=os.environ["MSSQL_HOST"],
        port=int(os.environ["MSSQL_PORT"]),
        user=os.environ["MSSQL_USERNAME"],
        password=os.environ["MSSQL_PASSWORD"],
        database=os.environ["MSSQL_DATABASE"],
        autocommit=True,
    )


def split_batches(sql_text):
    """Split SQL on GO batch separators (case-insensitive, on its own line)."""
    batches = re.split(r"^\s*GO\s*$", sql_text, flags=re.MULTILINE | re.IGNORECASE)
    return [b.strip() for b in batches if b.strip()]


def run_sql_file(sql_path, output_path):
    with open(sql_path, "r", encoding="utf-8") as f:
        sql_text = f.read()

    batches = split_batches(sql_text)
    if not batches:
        print("Error: SQL file is empty or contains no statements", file=sys.stderr)
        return 1

    conn = get_connection()
    cursor = conn.cursor()

    with open(output_path, "w", encoding="utf-8") as out:
        for i, batch in enumerate(batches, 1):
            try:
                cursor.execute(batch)
                # Collect all result sets from this batch
                while True:
                    if cursor.description is not None:
                        for row in cursor.fetchall():
                            line = "".join(
                                str(v) if v is not None else "" for v in row
                            )
                            out.write(line)
                            if not line.endswith("\n"):
                                out.write("\n")
                    if not cursor.nextset():
                        break
            except pymssql.Error as e:
                print(f"Error in batch {i}: {e}", file=sys.stderr)
                print(f"Batch content (first 200 chars): {batch[:200]}", file=sys.stderr)
                conn.close()
                return 1

    conn.close()
    return 0


def main():
    if len(sys.argv) != 3:
        print("Usage: mssql_runner.py <sql_file> <output_file>", file=sys.stderr)
        sys.exit(1)

    sql_path, output_path = sys.argv[1], sys.argv[2]
    if not os.path.isfile(sql_path):
        print(f"Error: SQL file not found: {sql_path}", file=sys.stderr)
        sys.exit(1)

    sys.exit(run_sql_file(sql_path, output_path))


if __name__ == "__main__":
    main()
