#!/usr/bin/env python3
"""批量校验/导入市场推广六类 Excel。

用法：
  python migrations/import_marketing_roi.py /path/to/*.xlsx --dry-run
  python migrations/import_marketing_roi.py /path/to/*.xlsx --db db/product_flow.db
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from _marketing_roi import (  # noqa: E402
    SOURCE_LABELS,
    import_marketing_files,
    parse_marketing_file,
    validate_reconciliation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="市场推广费用专题数据导入")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--db", type=Path, default=ROOT / "db" / "product_flow.db")
    parser.add_argument("--dry-run", action="store_true", help="只校验，不写数据库")
    parser.add_argument("--force", action="store_true", help="即使文件指纹已存在也重新导入")
    args = parser.parse_args()

    parsed = [parse_marketing_file(path) for path in args.files]
    preview = [
        {
            "file": item.name,
            "source": SOURCE_LABELS[item.source_type],
            "snapshot": item.snapshot_date,
            "rows": item.row_count,
            "warnings": item.warnings,
        }
        for item in parsed
    ]
    print(json.dumps({"files": preview, "reconciliation": validate_reconciliation(parsed)}, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0

    result = import_marketing_files(args.files, args.db, force=args.force)
    print(json.dumps({"database": str(args.db), "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
