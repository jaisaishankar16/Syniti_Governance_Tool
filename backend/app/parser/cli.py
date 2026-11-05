"""Command-line interface to parse T-SQL queries and pretty-print ParsedQuery."""

from __future__ import annotations

import json
import sys

from .models import ParseError
from .sql_parser import SqlParser


def main() -> None:
    if len(sys.argv) > 1:
        sql = sys.argv[1]
    else:
        # Read from stdin if no argument provided
        sql = sys.stdin.read().strip()

    if not sql:
        print('Usage: python -m app.parser.cli "<sql_query>"', file=sys.stderr)
        sys.exit(1)

    parser = SqlParser()
    try:
        parsed = parser.parse(sql)
        print(json.dumps(parsed.model_dump(), indent=2, default=str))
    except ParseError as pe:
        print(f"Error: {pe}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
