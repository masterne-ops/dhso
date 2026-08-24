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
