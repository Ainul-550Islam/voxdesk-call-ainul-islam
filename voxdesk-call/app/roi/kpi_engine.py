from __future__ import annotations
from typing import Any

_ALLOWED_AGGREGATIONS={"count","sum","average","ratio","rate"}

def validate_kpi_definition(definition:dict[str,Any])->None:
    if not definition.get("key") or not definition.get("name"):
        raise ValueError("KPI key and name are required")
    source=definition.get("source_definition")
    formula=definition.get("formula")
    if not isinstance(source,dict) or not source.get("event_type"):
        raise ValueError("KPI must identify an event_type source")
    if not isinstance(formula,dict) or definition.get("aggregation") not in _ALLOWED_AGGREGATIONS:
        raise ValueError("KPI formula or aggregation is unsupported")
    if definition["aggregation"] in {"sum","average"} and not formula.get("value_field"):
        raise ValueError("sum and average KPI formulas require value_field")
    if definition["aggregation"] in {"ratio","rate"} and not (formula.get("numerator_field") and formula.get("denominator_field")):
        raise ValueError("ratio and rate KPI formulas require numerator and denominator fields")

def evaluate_kpi(definition:dict[str,Any],events:list[dict[str,Any]],*,period:str)->dict:
    if definition.get("period") and definition["period"]!=period:
        return {"value":None,"data_quality":"NOT_AVAILABLE","reason":"KPI period does not match requested period"}
    source=definition.get("source_definition",{});expected_type=source.get("event_type")
    matching=[event for event in events if event.get("event_type")==expected_type and (event.get("period") in (None,period))]
    if not expected_type or not matching:
        return {"value":None,"data_quality":"NOT_AVAILABLE","reason":"required persisted source events are unavailable","period":period,"source_event_count":len(matching)}
    formula=definition.get("formula",{});aggregation=definition.get("aggregation")
    try:
        if aggregation=="count":value=float(len(matching))
        elif aggregation in {"sum","average"}:
            field=formula.get("value_field");values=[float(event[field]) for event in matching if event.get(field) is not None]
            if len(values)!=len(matching):return {"value":None,"data_quality":"NOT_AVAILABLE","reason":"a required source value is missing","period":period}
            value=sum(values) if aggregation=="sum" else sum(values)/len(values)
        elif aggregation in {"ratio","rate"}:
            numerator=formula["numerator_field"];denominator=formula["denominator_field"]
            if any(event.get(numerator) is None or event.get(denominator) is None for event in matching):return {"value":None,"data_quality":"NOT_AVAILABLE","reason":"a required numerator or denominator is missing","period":period}
            num=sum(float(event[numerator]) for event in matching);den=sum(float(event[denominator]) for event in matching)
            if den==0:return {"value":None,"data_quality":"NOT_AVAILABLE","reason":"KPI denominator is zero","period":period}
            value=num/den
        else:return {"value":None,"data_quality":"NOT_AVAILABLE","reason":"unsupported KPI aggregation","period":period}
    except (KeyError,TypeError,ValueError,OverflowError):
        return {"value":None,"data_quality":"INVALID_SOURCE_DATA","reason":"persisted source event values are invalid","period":period}
    return {"value":value,"data_quality":"measured","source_event_count":len(matching),"period":period,"unit":definition.get("unit"),"aggregation":aggregation,"calculation_version":"1"}
