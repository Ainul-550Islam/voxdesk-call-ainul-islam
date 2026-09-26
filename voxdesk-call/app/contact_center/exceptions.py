"""Typed ACD failures.

A routing failure is not an assignment. Callers map ``status_code`` and
``as_dict``. Cross-tenant misses stay ``BoundaryDenied`` (404) in the tenancy
package; they are not reported here as a permission error.
"""

from __future__ import annotations


class AcdError(Exception):
    code = "acd_error"
    status_code = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class QueueUnavailable(AcdError):
    code = "queue_unavailable"
    status_code = 409


class NoEligibleAgent(AcdError):
    code = "no_eligible_agent"
    status_code = 409


class AgentUnavailable(AcdError):
    code = "agent_unavailable"
    status_code = 409


class InvalidSkill(AcdError):
    code = "invalid_skill"
    status_code = 422


class OverflowDenied(AcdError):
    code = "overflow"
    status_code = 409


class DuplicateAssignment(AcdError):
    code = "duplicate_assignment"
    status_code = 409


class StaleState(AcdError):
    code = "stale_state"
    status_code = 409


class AcdAuthorizationError(AcdError):
    code = "authorization"
    status_code = 403
