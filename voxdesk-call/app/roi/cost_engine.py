from __future__ import annotations
from typing import Any
COST_COMPONENTS=("llm_cost","stt_cost","tts_cost","telephony_cost","connector_cost","infrastructure_cost","labor_review_cost")

def calculate_cost(costs: dict[str,Any], *, interactions: int|None, completed_outcomes: int|None, successful_actions: int|None) -> dict:
    absent=[key for key in COST_COMPONENTS if costs.get(key) is None]
    if absent: return {"total_cost":{"value":None,"status":"NOT_AVAILABLE","reason":"missing cost components: "+", ".join(absent)},"cost_per_interaction":{"value":None,"status":"NOT_AVAILABLE","reason":"cost or interaction count unavailable"},"cost_per_completed_outcome":{"value":None,"status":"NOT_AVAILABLE","reason":"cost or completed outcome count unavailable"},"cost_per_successful_action":{"value":None,"status":"NOT_AVAILABLE","reason":"cost or action count unavailable"}}
    total=sum(float(costs[k]) for k in COST_COMPONENTS)
    def ratio(n, label): return {"value":total/n,"status":"recorded_cost_arithmetic","reason":None} if n is not None and n>0 else {"value":None,"status":"NOT_AVAILABLE","reason":label}
    return {"total_cost":{"value":total,"status":"recorded_cost_arithmetic","reason":None},"cost_per_interaction":ratio(interactions,"interaction count unavailable or zero"),"cost_per_completed_outcome":ratio(completed_outcomes,"completed outcome count unavailable or zero"),"cost_per_successful_action":ratio(successful_actions,"successful action count unavailable or zero"),"calculation_version":"1"}

def calculate_capacity(baseline_labor_hours: float|None, post_human_hours: float|None) -> dict:
    if baseline_labor_hours is None or post_human_hours is None:
        return {"observed_human_hours_delta":{"value":None,"data_quality":"NOT_AVAILABLE","reason":"both recorded period inputs are required"},"capacity_unlocked":{"value":None,"data_quality":"NOT_AVAILABLE","reason":"capacity attribution evidence is not configured"}}
    if baseline_labor_hours < 0 or post_human_hours < 0: raise ValueError("hours must be nonnegative")
    value=baseline_labor_hours-post_human_hours
    return {"observed_human_hours_delta":{"value":value,"data_quality":"recorded_inputs_unverified","interpretation":"arithmetic difference only; no causal or capacity claim"},"capacity_unlocked":{"value":None,"data_quality":"NOT_AVAILABLE","reason":"no validated attribution or staffing disposition source is configured"},"calculation_version":"1"}
