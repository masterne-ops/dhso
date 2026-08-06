from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .config import settings
from .data_access import is_system_denied, open_data_db, table_allowed


HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]*")
PURPOSE_RE = re.compile(r"^\s*-\s*\*\*用途\*\*[：:]\s*(.+?)\s*$")

_cache_lock = Lock()
_cache_mtime: int | None = None
_cache_value: dict | None = None


def _doc_sections(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    sections: dict[str, dict[str, str]] = {}
    domain = "未分类"
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match:
            continue
        level, title = match.groups()
        if level == "##" and title.startswith("域"):
            domain = title
            continue
        if level != "###":
            continue
        end = len(lines)
        for cursor in range(index + 1, len(lines)):
            if lines[cursor].startswith("### "):
                end = cursor
                break
        body = lines[index + 1:end]
        candidates = IDENTIFIER_RE.findall(title.lower())
        purpose = ""
        for body_line in body:
            purpose_match = PURPOSE_RE.match(body_line)
            if purpose_match:
                purpose = purpose_match.group(1).strip()
                break
        if not purpose:
            for body_line in body:
                clean = body_line.strip()
                if clean and not clean.startswith(("#", "-", "|", "```")):
                    purpose = clean[:500]
                    break
        for name in candidates:
            sections[name] = {
                "domain": domain,
                "description": purpose or f"{name} 数据资源",
                "doc_heading": title,
            }
    return sections


def build_catalog(
    *,
    database_doc: Path | None = None,
    database_path: Path | None = None,
) -> dict:
    doc_path = database_doc or settings.database_doc
    sections = _doc_sections(doc_path)
    conn = open_data_db(database_path)
    try:
        objects = conn.execute(
            """
            SELECT name, type
            FROM sqlite_master
            WHERE type IN ('table', 'view')
            ORDER BY name
            """
        ).fetchall()
        resources = []
        for name, object_type in objects:
            if is_system_denied(name):
                continue
            safe_name = name.replace('"', '""')
            columns = conn.execute(f'PRAGMA table_info("{safe_name}")').fetchall()
            doc = sections.get(name.lower(), {})
            resources.append(
                {
                    "name": name,
                    "object_type": object_type,
                    "domain": doc.get("domain", "数据库说明未单列"),
                    "description": doc.get(
                        "description",
                        f"{name} {object_type}，字段口径以数据库说明和字段接口为准",
                    ),
                    "doc_heading": doc.get("doc_heading"),
                    "columns": [
                        {
                            "name": column[1],
                            "declared_type": column[2] or "",
                        }
                        for column in columns
                    ],
                    "interfaces": {
                        "describe": f"/v1/catalog/resources/{name}",
                        "search": f"/v1/data/{name}/search",
                        "aggregate": f"/v1/data/{name}/aggregate",
                    },
                }
            )
        return {
            "catalog_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                "database_doc": doc_path.name,
                "database": (database_path or settings.data_db).name,
            },
            "filter_operators": [
                "eq",
                "ne",
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "not_in",
                "contains",
                "starts_with",
                "ends_with",
                "between",
                "is_null",
                "not_null",
            ],
            "aggregate_functions": [
                "count",
                "count_distinct",
                "sum",
                "avg",
                "min",
                "max",
            ],
            "resources": resources,
        }
    finally:
        conn.close()


def write_catalog(
    output: Path | None = None,
    *,
    database_doc: Path | None = None,
    database_path: Path | None = None,
) -> dict:
    target = output or settings.catalog_file
    catalog = build_catalog(
        database_doc=database_doc,
        database_path=database_path,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return catalog


def load_catalog() -> dict:
    global _cache_mtime, _cache_value
    path = settings.catalog_file
    if not path.exists():
        return build_catalog()
    mtime = path.stat().st_mtime_ns
    with _cache_lock:
        if _cache_value is None or _cache_mtime != mtime:
            _cache_value = json.loads(path.read_text(encoding="utf-8"))
            _cache_mtime = mtime
        return _cache_value


def resource_map() -> dict[str, dict]:
    return {item["name"]: item for item in load_catalog()["resources"]}


def visible_resources(patterns: list[str]) -> list[dict]:
    return [
        item
        for item in load_catalog()["resources"]
        if table_allowed(item["name"], patterns)
    ]


def visible_resource(name: str, patterns: list[str]) -> dict | None:
    item = resource_map().get(name)
    if not item or not table_allowed(name, patterns):
        return None
    return item

