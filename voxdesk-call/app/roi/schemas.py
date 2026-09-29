from __future__ import annotations
from typing import Any, Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field

class ScopedInput(BaseModel):
 model_config=ConfigDict(extra="forbid")
 environment_id:uuid.UUID
 period:str=Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")

class BaselineInput(ScopedInput):
 volume:float|None=Field(default=None,ge=0);labor_hours:float|None=Field(default=None,ge=0);labor_cost:float|None=Field(default=None,ge=0);handle_time:float|None=Field(default=None,ge=0);escalation_rate:float|None=Field(default=None,ge=0,le=1);system_cost:float|None=Field(default=None,ge=0)
 source_references:list[str]=Field(default_factory=list,max_length=100);assumptions_version:str=Field(default="1",min_length=1,max_length=100)

class OutcomeInput(ScopedInput):
 interactions_handled:int|None=Field(default=None,ge=0);tasks_completed:int|None=Field(default=None,ge=0);records_updated:int|None=Field(default=None,ge=0);escalations:int|None=Field(default=None,ge=0);successful_actions:int|None=Field(default=None,ge=0);minutes_handled:float|None=Field(default=None,ge=0);human_hours:float|None=Field(default=None,ge=0)
 source_references:list[str]=Field(default_factory=list,max_length=100);idempotency_key:str=Field(min_length=8,max_length=200)

class CostInput(ScopedInput):
 component:Literal["llm_cost","stt_cost","tts_cost","telephony_cost","connector_cost","infrastructure_cost","labor_review_cost"]
 amount:float|None=Field(default=None,ge=0);currency:str=Field(min_length=3,max_length=8);source_reference:str=Field(min_length=1,max_length=500)

class KPIInput(BaseModel):
 model_config=ConfigDict(extra="forbid")
 environment_id:uuid.UUID;key:str=Field(min_length=1,max_length=100);name:str=Field(min_length=1,max_length=200);source_definition:dict[str,Any];formula:dict[str,Any];unit:str=Field(min_length=1,max_length=40);aggregation:str=Field(min_length=1,max_length=40)

class CalculateInput(BaseModel):
 model_config=ConfigDict(extra="forbid")
 environment_id:uuid.UUID;baseline_id:uuid.UUID;outcome_id:uuid.UUID

class MeasurementPeriod(BaseModel):
 period:str;data_quality:Literal["available","NOT_AVAILABLE"];source_count:int=0;reason:str|None=None

class ROIResultSchema(BaseModel):
 id:str;period:str;data_quality:str;calculation_version:str;assumptions_version:str;result:dict[str,Any];generated_at:str|None=None
