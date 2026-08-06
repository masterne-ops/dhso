from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    params: dict[str, Any] | list[Any] | None = None
    max_rows: int | None = Field(default=None, ge=1)


class FilterCondition(BaseModel):
    field: str = Field(min_length=1, max_length=128)
    operator: Literal[
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
    ] = "eq"
    value: Any = None


class SortField(BaseModel):
    field: str = Field(min_length=1, max_length=128)
    direction: Literal["asc", "desc"] = "asc"


class ResourceSearchRequest(BaseModel):
    fields: list[str] | None = None
    filters: list[FilterCondition] = Field(default_factory=list, max_length=50)
    order_by: list[SortField] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=100, ge=1)
    offset: int = Field(default=0, ge=0, le=10_000_000)


class AggregateMetric(BaseModel):
    function: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    field: str | None = Field(default=None, max_length=128)
    alias: str | None = Field(default=None, min_length=1, max_length=128)


class ResourceAggregateRequest(BaseModel):
    group_by: list[str] = Field(default_factory=list, max_length=20)
    metrics: list[AggregateMetric] = Field(min_length=1, max_length=20)
    filters: list[FilterCondition] = Field(default_factory=list, max_length=50)
    order_by: list[SortField] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=100, ge=1)


class ResolveRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    context: str | None = Field(default=None, max_length=4000)
