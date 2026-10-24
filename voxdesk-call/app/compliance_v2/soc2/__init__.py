"""Evidence automation for the controls this repository implements.

Nothing in this package asserts an audit outcome. It collects facts from real
sources, reports the status of each control as PASS, FAIL, NOT_CONFIGURED or
ERROR, and stores the result with a content hash.
"""

from __future__ import annotations

from app.compliance_v2.soc2.collectors_base import (
    EVIDENCE_ERROR,
    EVIDENCE_FAIL,
    EVIDENCE_NOT_CONFIGURED,
    EVIDENCE_PASS,
    CollectorResult,
    EvidenceCollector,
    EvidenceItem,
)
from app.compliance_v2.soc2.control_map import COLLECTOR_CONTROLS, ControlMap, load_control_map
from app.compliance_v2.soc2.runner import EvidenceRun, run_evidence

__all__ = [
    "COLLECTOR_CONTROLS",
    "EVIDENCE_ERROR",
    "EVIDENCE_FAIL",
    "EVIDENCE_NOT_CONFIGURED",
    "EVIDENCE_PASS",
    "CollectorResult",
    "ControlMap",
    "EvidenceCollector",
    "EvidenceItem",
    "EvidenceRun",
    "load_control_map",
    "run_evidence",
]
