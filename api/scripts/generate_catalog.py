from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.catalog import write_catalog


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the fixed API resource catalog from SQLite and 数据库说明.md"
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--database-doc", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    catalog = write_catalog(
        args.output,
        database_doc=args.database_doc,
        database_path=args.database,
    )
    print(json.dumps({
        "output": str(args.output) if args.output else "configured path",
        "resources": len(catalog["resources"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
