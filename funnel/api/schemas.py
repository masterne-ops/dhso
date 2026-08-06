from pydantic import BaseModel
from typing import Any, Dict


class StatePayload(BaseModel):
    period_type: str                 # 'week' | 'month'
    state: Dict[str, Any]            # {targets, manual, factor_active, custom_factors, todos}
