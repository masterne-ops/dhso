from pydantic import BaseModel
from typing import Any, Dict, Optional


class StatePayload(BaseModel):
    period_type: str                 # 'week' | 'month'
    state: Dict[str, Any]            # {targets, manual, factor_active, custom_factors, library_factors, todos, questions, notes}


class OverviewItemPatch(BaseModel):
    kind: str                        # 'todo' | 'question'
    index: int
    done: bool
    text: Optional[str] = None       # 有则按原文校对，防列表已变仍勾错行


class SpecialPinPayload(BaseModel):
    factor_id: str
    target: Optional[float] = None


class SalesEvalPatch(BaseModel):
    side: str = "all"
    org: Optional[str] = None
    person: Optional[str] = None
    item_id: Optional[str] = None          # 规定动作 id；有则写阈值
    preset_value: Optional[float] = None   # null = 删除本对象覆盖，回落继承
    note: Optional[str] = None             # 分析小结


class SpecialTargetPayload(BaseModel):
    factor_id: str
    target: Optional[float] = None   # null = 清空


class BudgetPayload(BaseModel):
    geo_key: str
    period_key: str
    level: str                       # authorized | activated_v1 | activated | senior
    amount: Optional[float] = None   # null = 清空该格预算
