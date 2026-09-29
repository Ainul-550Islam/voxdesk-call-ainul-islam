from __future__ import annotations
from typing import Any
from app.governance.hashing import sha256_hex

def evaluate_item(control:Any,item:dict[str,Any],source_references:list[str])->dict[str,Any]:
 rules=dict(getattr(control,"rules",{}) or {});reference=rules.get("source_reference") or getattr(control,"source_reference",None);fingerprint=sha256_hex(item);observed_state={}
 if not reference or reference not in source_references:state,explanation="insufficient_evidence","configured guideline source reference was not supplied"
 elif rules.get("exception_field") and item.get(rules["exception_field"]):
  state,explanation="review_required","configured exception condition requires human review";observed_state[rules["exception_field"]]=item.get(rules["exception_field"])
 elif not rules.get("field") or "expected" not in rules:state,explanation="insufficient_evidence","rule lacks a configured field or expected value"
 else:
  field=rules["field"];observed=item.get(field);expected=rules["expected"];operator=rules.get("operator","equals");observed_state[field]=observed
  if observed is None:state,explanation="insufficient_evidence","required observed field is absent"
  else:
   passed={"equals":observed==expected,"in":observed in expected if isinstance(expected,list) else False,"greater_than":isinstance(observed,(int,float)) and isinstance(expected,(int,float)) and observed>expected,"less_than":isinstance(observed,(int,float)) and isinstance(expected,(int,float)) and observed<expected}.get(operator)
   if passed is None:state,explanation="insufficient_evidence","unsupported configured operator"
   else:state,explanation=("pass","configured rule matched documented input") if passed else ("fail","configured rule did not match documented input")
 severity=control.severity
 return {"control_id":str(control.id),"rule_id":control.control_key,"source_reference":reference,"observed_fingerprint":fingerprint,"observed_state":observed_state,"expected_rule":rules,"result":state,"severity":severity,"review_required":state=="review_required" or severity in {"high","critical"},"explanation":explanation}
