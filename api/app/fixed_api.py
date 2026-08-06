from __future__ import annotations

from typing import Any

from .data_access import QueryRejected, execute_readonly
from .models import (
    AggregateMetric,
    FilterCondition,
    ResourceAggregateRequest,
    ResourceSearchRequest,
    SortField,
)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _column_names(resource: dict) -> set[str]:
    return {column["name"] for column in resource["columns"]}


def _require_column(name: str, columns: set[str]) -> str:
    if name not in columns:
        raise QueryRejected("unknown_field", f"Unknown or unavailable field: {name}")
    return name


def _literal_like(value: Any) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_filters(
    filters: list[FilterCondition],
    columns: set[str],
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for index, condition in enumerate(filters):
        field = _quote(_require_column(condition.field, columns))
        key = f"f_{index}"
        operator = condition.operator
        if operator in {"is_null", "not_null"}:
            clauses.append(f"{field} IS {'NOT ' if operator == 'not_null' else ''}NULL")
            continue
        if operator in {"in", "not_in"}:
            if not isinstance(condition.value, list) or not condition.value:
                raise QueryRejected(
                    "invalid_filter",
                    f"{operator} requires a non-empty array",
                )
            if len(condition.value) > 1000:
                raise QueryRejected("invalid_filter", "in/not_in accepts at most 1000 values")
            names = []
            for value_index, value in enumerate(condition.value):
                item_key = f"{key}_{value_index}"
                params[item_key] = value
                names.append(f":{item_key}")
            clauses.append(
                f"{field} {'NOT IN' if operator == 'not_in' else 'IN'} "
                f"({', '.join(names)})"
            )
            continue
        if operator == "between":
            if not isinstance(condition.value, list) or len(condition.value) != 2:
                raise QueryRejected("invalid_filter", "between requires a two-item array")
            params[f"{key}_start"] = condition.value[0]
            params[f"{key}_end"] = condition.value[1]
            clauses.append(f"{field} BETWEEN :{key}_start AND :{key}_end")
            continue
        if operator in {"contains", "starts_with", "ends_with"}:
            literal = _literal_like(condition.value)
            if operator == "contains":
                literal = f"%{literal}%"
            elif operator == "starts_with":
                literal = f"{literal}%"
            else:
                literal = f"%{literal}"
            params[key] = literal
            clauses.append(f"{field} LIKE :{key} ESCAPE '\\'")
            continue
        sql_operator = {
            "eq": "=",
            "ne": "!=",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
        }[operator]
        params[key] = condition.value
        clauses.append(f"{field} {sql_operator} :{key}")
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def _build_order(
    order_by: list[SortField],
    allowed: set[str],
) -> str:
    if not order_by:
        return ""
    parts = []
    for item in order_by:
        _require_column(item.field, allowed)
        parts.append(f"{_quote(item.field)} {item.direction.upper()}")
    return " ORDER BY " + ", ".join(parts)


def execute_search(
    resource: dict,
    payload: ResourceSearchRequest,
    *,
    patterns: list[str],
    max_rows: int,
):
    columns = _column_names(resource)
    selected = payload.fields or [column["name"] for column in resource["columns"]]
    if not selected:
        raise QueryRejected("no_fields", "Resource has no visible fields")
    for field in selected:
        _require_column(field, columns)
    where_sql, params = _build_filters(payload.filters, columns)
    order_sql = _build_order(payload.order_by, columns)
    limit = min(payload.limit, max_rows)
    params["_limit"] = limit + 1
    params["_offset"] = payload.offset
    sql = (
        f"SELECT {', '.join(_quote(field) for field in selected)} "
        f"FROM {_quote(resource['name'])}"
        f"{where_sql}{order_sql} LIMIT :_limit OFFSET :_offset"
    )
    return execute_readonly(
        sql,
        params,
        patterns=patterns,
        max_rows=limit,
    )


def _metric_sql(
    metric: AggregateMetric,
    columns: set[str],
    index: int,
) -> tuple[str, str]:
    function = metric.function
    if function == "count" and metric.field is None:
        expression = "COUNT(*)"
        default_alias = "count"
    else:
        if not metric.field:
            raise QueryRejected(
                "invalid_metric",
                f"{function} requires a field",
            )
        field = _quote(_require_column(metric.field, columns))
        if function == "count_distinct":
            expression = f"COUNT(DISTINCT {field})"
        else:
            expression = f"{function.upper()}({field})"
        default_alias = f"{function}_{metric.field}"
    alias = metric.alias or default_alias or f"metric_{index}"
    return f"{expression} AS {_quote(alias)}", alias


def execute_aggregate(
    resource: dict,
    payload: ResourceAggregateRequest,
    *,
    patterns: list[str],
    max_rows: int,
):
    columns = _column_names(resource)
    for field in payload.group_by:
        _require_column(field, columns)
    metric_parts: list[str] = []
    aliases: list[str] = []
    for index, metric in enumerate(payload.metrics):
        expression, alias = _metric_sql(metric, columns, index)
        if alias in aliases or alias in payload.group_by:
            raise QueryRejected("duplicate_alias", f"Duplicate output field: {alias}")
        metric_parts.append(expression)
        aliases.append(alias)
    selected = [_quote(field) for field in payload.group_by] + metric_parts
    where_sql, params = _build_filters(payload.filters, columns)
    group_sql = (
        " GROUP BY " + ", ".join(_quote(field) for field in payload.group_by)
        if payload.group_by
        else ""
    )
    order_sql = _build_order(
        payload.order_by,
        set(payload.group_by) | set(aliases),
    )
    limit = min(payload.limit, max_rows)
    params["_limit"] = limit + 1
    sql = (
        f"SELECT {', '.join(selected)} FROM {_quote(resource['name'])}"
        f"{where_sql}{group_sql}{order_sql} LIMIT :_limit"
    )
    return execute_readonly(
        sql,
        params,
        patterns=patterns,
        max_rows=limit,
    )

