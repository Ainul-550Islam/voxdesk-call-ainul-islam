"""Domain models, schemas, and bounded rule validation for Testing, Simulation, Batch Evaluation, and QA Evidence.

Every test case and test run must bind to an explicit immutable AgentVersion
(or ChatAgentVersion), never silently floating to "latest". Every evaluation
result carries structured evidence and distinguishes assertion failures from
runtime/evaluator errors and empty-rule scorecards (``NO_ASSERTIONS``).
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_REGEX_PATTERN_LENGTH = 512
MAX_JSON_PATH_DEPTH = 12
MAX_JUDGE_RUBRIC_LENGTH = 4000


class TestSuiteStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TestPassPolicy(str, Enum):
    ALL_PASSED = "all_passed"
    MIN_SCORE = "min_score"


class AgentKind(str, Enum):
    VOICE = "voice"
    CHAT = "chat"


class TestRunMode(str, Enum):
    LLM = "llm"
    SIMULATION = "simulation"
    WEB_CALL = "web_call"
    PHONE_CALL = "phone_call"
    CHAT = "chat"


class TestRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    CANCELLED = "cancelled"
    NOT_RUN = "not_run"


class EvaluationRuleType(str, Enum):
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    REGEX = "regex"
    JSON_PATH_EQUALS = "json_path_equals"
    JSON_PATH_EXISTS = "json_path_exists"
    TOOL_CALLED = "tool_called"
    TOOL_NOT_CALLED = "tool_not_called"
    TRANSFER_OCCURRED = "transfer_occurred"
    VARIABLE_EQUALS = "variable_equals"
    TURN_COUNT_MAX = "turn_count_max"
    TURN_COUNT_MIN = "turn_count_min"
    LATENCY_MS_MAX = "latency_ms_max"
    FINAL_STATE_EQUALS = "final_state_equals"
    LLM_JUDGE = "llm_judge"


class EvaluationResultStatus(str, Enum):
    PASSED = "passed"
    FAILED_ASSERTION = "failed_assertion"
    EVALUATION_ERROR = "evaluation_error"
    SKIPPED = "skipped"
    NO_ASSERTIONS = "no_assertions"


class ScorecardStatus(str, Enum):
    PASSED = "PASSED"
    FAILED_ASSERTION = "FAILED_ASSERTION"
    EVALUATION_ERROR = "EVALUATION_ERROR"
    NO_ASSERTIONS = "NO_ASSERTIONS"
    NOT_RUN = "NOT_RUN"


_UNSAFE_REGEX_CONSTRUCTS = re.compile(
    r"\(\?[P<]*[a-zA-Z0-9_]*>.*\)\+|\([^)]*[\+\*][^)]*\)[\+\*]|\(\?\("
)
_SAFE_JSON_PATH_TOKEN = re.compile(
    r"^\$?(?:\.[a-zA-Z0-9_\-]+|\[\d+\]|\['[a-zA-Z0-9_\-]+'\]|\[\"[a-zA-Z0-9_\-]+\"\])+$"
)


def validate_safe_regex_pattern(pattern: str) -> str:
    """Validate that a regex pattern is syntactically valid and bounded."""
    cleaned = (pattern or "").strip()
    if not cleaned:
        raise ValueError("Regex pattern must not be empty.")
    if len(cleaned) > MAX_REGEX_PATTERN_LENGTH:
        raise ValueError(
            f"Regex pattern exceeds maximum length of {MAX_REGEX_PATTERN_LENGTH} characters."
        )
    if _UNSAFE_REGEX_CONSTRUCTS.search(cleaned):
        raise ValueError(
            "Regex pattern contains nested quantifiers or conditional constructs that risk catastrophic backtracking."
        )
    try:
        re.compile(cleaned)
    except re.error as exc:
        raise ValueError(f"Invalid regular expression: {exc}") from exc
    return cleaned


def validate_safe_json_path(path: str) -> str:
    """Validate that a JSONPath expression is a bounded dot/index path."""
    cleaned = (path or "").strip()
    if not cleaned:
        raise ValueError("JSONPath expression must not be empty.")
    normalized = cleaned if cleaned.startswith("$") else f"$.{cleaned.lstrip('.')}"
    if not _SAFE_JSON_PATH_TOKEN.match(normalized):
        raise ValueError(
            f"Unsupported or unsafe JSONPath '{path}'. Only bounded dot/bracket paths (e.g. '$.booking.status') are permitted."
        )
    segments = [seg for seg in re.split(r"\.|\[|\]", normalized.lstrip("$")) if seg]
    if len(segments) > MAX_JSON_PATH_DEPTH:
        raise ValueError(
            f"JSONPath depth {len(segments)} exceeds maximum allowed depth of {MAX_JSON_PATH_DEPTH}."
        )
    return normalized


def _coerce_rule_type(rule_type: EvaluationRuleType | str) -> EvaluationRuleType:
    if isinstance(rule_type, EvaluationRuleType):
        return rule_type
    if hasattr(rule_type, "value"):
        return EvaluationRuleType(str(rule_type.value))
    s = str(rule_type).strip()
    if s.startswith("EvaluationRuleType."):
        s = s.split(".", 1)[1].lower()
    return EvaluationRuleType(s)


def validate_rule_config(rule_type: EvaluationRuleType | str, config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize rule configuration according to rule_type."""
    rt = _coerce_rule_type(rule_type)
    cfg = dict(config or {})

    if rt in (EvaluationRuleType.CONTAINS, EvaluationRuleType.NOT_CONTAINS):
        substring = cfg.get("substring") or cfg.get("value") or cfg.get("text")
        if not isinstance(substring, str) or not substring.strip():
            raise ValueError(f"Rule '{rt.value}' requires a non-empty 'substring' string in config.")
        cfg["substring"] = substring
        cfg.setdefault("case_sensitive", bool(cfg.get("case_sensitive", False)))
        cfg.setdefault("target", str(cfg.get("target") or "assistant_transcript"))
        return cfg

    if rt == EvaluationRuleType.REGEX:
        pattern = cfg.get("pattern") or cfg.get("regex") or cfg.get("value")
        if not isinstance(pattern, str):
            raise ValueError("Rule 'regex' requires a string 'pattern' in config.")
        cfg["pattern"] = validate_safe_regex_pattern(pattern)
        cfg.setdefault("case_sensitive", bool(cfg.get("case_sensitive", False)))
        cfg.setdefault("target", str(cfg.get("target") or "assistant_transcript"))
        return cfg

    if rt == EvaluationRuleType.JSON_PATH_EXISTS:
        path = cfg.get("path") or cfg.get("json_path")
        if not isinstance(path, str):
            raise ValueError("Rule 'json_path_exists' requires a string 'path' in config.")
        cfg["path"] = validate_safe_json_path(path)
        return cfg

    if rt == EvaluationRuleType.JSON_PATH_EQUALS:
        path = cfg.get("path") or cfg.get("json_path")
        if not isinstance(path, str):
            raise ValueError("Rule 'json_path_equals' requires a string 'path' in config.")
        if "expected" not in cfg and "value" not in cfg and "expected_value" not in cfg:
            raise ValueError("Rule 'json_path_equals' requires an 'expected' value in config.")
        cfg["path"] = validate_safe_json_path(path)
        if "expected" not in cfg:
            cfg["expected"] = cfg.get("expected_value", cfg.get("value"))
        return cfg

    if rt in (EvaluationRuleType.TOOL_CALLED, EvaluationRuleType.TOOL_NOT_CALLED):
        tool_name = cfg.get("tool_name") or cfg.get("name") or cfg.get("tool")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(f"Rule '{rt.value}' requires a non-empty 'tool_name' in config.")
        cfg["tool_name"] = tool_name.strip()
        return cfg

    if rt == EvaluationRuleType.TRANSFER_OCCURRED:
        cfg["expected"] = bool(cfg.get("expected", True))
        if cfg.get("destination") is not None:
            cfg["destination"] = str(cfg["destination"]).strip()
        return cfg

    if rt == EvaluationRuleType.VARIABLE_EQUALS:
        var_name = cfg.get("variable_name") or cfg.get("key") or cfg.get("name")
        if not isinstance(var_name, str) or not var_name.strip():
            raise ValueError("Rule 'variable_equals' requires a non-empty 'variable_name' in config.")
        if "expected" not in cfg and "value" not in cfg and "expected_value" not in cfg:
            raise ValueError("Rule 'variable_equals' requires an 'expected' value in config.")
        cfg["variable_name"] = var_name.strip()
        if "expected" not in cfg:
            cfg["expected"] = cfg.get("expected_value", cfg.get("value"))
        return cfg

    if rt in (EvaluationRuleType.TURN_COUNT_MAX, EvaluationRuleType.TURN_COUNT_MIN):
        raw_threshold = (
            cfg.get("threshold")
            if "threshold" in cfg
            else cfg.get("max_turns")
            if "max_turns" in cfg
            else cfg.get("min_turns")
            if "min_turns" in cfg
            else cfg.get("value")
        )
        if raw_threshold is None:
            raise ValueError(f"Rule '{rt.value}' requires an integer 'threshold' in config.")
        threshold = int(raw_threshold)
        if threshold < 0:
            raise ValueError(f"Rule '{rt.value}' threshold must be >= 0.")
        cfg["threshold"] = threshold
        return cfg

    if rt == EvaluationRuleType.LATENCY_MS_MAX:
        raw_max = (
            cfg.get("max_ms")
            if "max_ms" in cfg
            else cfg.get("threshold")
            if "threshold" in cfg
            else cfg.get("value")
        )
        if raw_max is None:
            raise ValueError("Rule 'latency_ms_max' requires an integer 'max_ms' in config.")
        max_ms = int(raw_max)
        if max_ms <= 0:
            raise ValueError("Rule 'latency_ms_max' max_ms must be > 0.")
        cfg["max_ms"] = max_ms
        return cfg

    if rt == EvaluationRuleType.FINAL_STATE_EQUALS:
        expected_state = (
            cfg.get("expected_state") or cfg.get("state") or cfg.get("expected") or cfg.get("value")
        )
        if not isinstance(expected_state, str) or not expected_state.strip():
            raise ValueError("Rule 'final_state_equals' requires a non-empty 'expected_state' in config.")
        cfg["expected_state"] = expected_state.strip()
        return cfg

    if rt == EvaluationRuleType.LLM_JUDGE:
        rubric = cfg.get("rubric") or cfg.get("criteria") or cfg.get("prompt")
        if not isinstance(rubric, str) or len(rubric.strip()) < 5:
            raise ValueError("Rule 'llm_judge' requires a 'rubric' of at least 5 characters.")
        if len(rubric) > MAX_JUDGE_RUBRIC_LENGTH:
            raise ValueError(f"Rule 'llm_judge' rubric exceeds {MAX_JUDGE_RUBRIC_LENGTH} characters.")
        cfg["rubric"] = rubric.strip()
        threshold = float(cfg.get("pass_threshold", 0.7))
        if not (0.0 <= threshold <= 1.0):
            raise ValueError("Rule 'llm_judge' pass_threshold must be between 0.0 and 1.0.")
        cfg["pass_threshold"] = threshold
        return cfg

    return cfg


# ------------------------------------------------------------ Suite & Case DTOs


class InlineEvaluationRuleSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1, max_length=200)
    rule_type: EvaluationRuleType
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    weight: float = Field(default=1.0, ge=0.0, le=100.0)
    evaluator_version: str = Field(default="v1.0", max_length=32)

    @model_validator(mode="after")
    def _validate_cfg(self) -> "InlineEvaluationRuleSpec":
        self.config = validate_rule_config(self.rule_type, self.config)
        return self


class SimulationInputTurn(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str = Field(default="user")
    content: str = Field(..., min_length=1)
    expect_intent: str | None = None
    expected_contains: str | None = None
    expected_tool: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TestSuiteCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    environment_id: UUID | None = None
    pass_policy: TestPassPolicy = TestPassPolicy.ALL_PASSED
    min_pass_score: float = Field(default=100.0, ge=0.0, le=100.0)


class TestSuiteUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: TestSuiteStatus | None = None
    pass_policy: TestPassPolicy | None = None
    min_pass_score: float | None = Field(default=None, ge=0.0, le=100.0)


class TestSuiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    environment_id: UUID | None = None
    name: str
    description: str
    status: str
    pass_policy: str
    min_pass_score: float
    case_count: int = 0
    rule_count: int = 0
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    created_by: UUID | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TestCaseCreateRequest(BaseModel):
    suite_id: UUID | None = None
    agent_id: str = Field(..., min_length=1, max_length=128)
    agent_kind: AgentKind = AgentKind.VOICE
    agent_version_id: UUID | None = None
    agent_version_number: int = Field(..., ge=1, description="Pinned AgentVersion number (required).")
    name: str = Field(..., min_length=1, max_length=200)
    mode: TestRunMode = TestRunMode.SIMULATION
    input_messages: list[dict[str, Any] | str] = Field(default_factory=list)
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    expected_rules: list[InlineEvaluationRuleSpec] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("input_messages", mode="before")
    @classmethod
    def _normalize_input_messages(cls, value: Any) -> list[dict[str, Any]]:
        if not value:
            return []
        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(value):
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append({"role": "user", "content": text, "turn_index": idx})
            elif isinstance(item, dict):
                content = str(item.get("content") or item.get("text") or item.get("user_says") or "").strip()
                if content:
                    entry = dict(item)
                    entry["role"] = str(item.get("role") or "user")
                    entry["content"] = content
                    normalized.append(entry)
        return normalized


class TestCaseUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    agent_id: str | None = Field(default=None, min_length=1, max_length=128)
    agent_kind: AgentKind | None = None
    agent_version_id: UUID | None = None
    agent_version_number: int | None = Field(default=None, ge=1)
    mode: TestRunMode | None = None
    input_messages: list[dict[str, Any] | str] | None = None
    dynamic_variables: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    expected_rules: list[InlineEvaluationRuleSpec] | None = None
    enabled: bool | None = None


class TestCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    suite_id: UUID | None = None
    agent_id: str
    agent_kind: str
    agent_version_id: UUID | None = None
    agent_version_number: int
    name: str
    mode: str
    input_messages: list[dict[str, Any]]
    dynamic_variables: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    expected_rules: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool
    archived_at: datetime | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------- Evaluation Rule & Result


class EvaluationRuleCreateRequest(BaseModel):
    suite_id: UUID | None = None
    test_case_id: UUID | None = None
    name: str = Field(..., min_length=1, max_length=200)
    rule_type: EvaluationRuleType
    config: dict[str, Any] = Field(default_factory=dict)
    evaluator_version: str = Field(default="v1.0", max_length=32)
    enabled: bool = True
    weight: float = Field(default=1.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def _validate_cfg(self) -> "EvaluationRuleCreateRequest":
        self.config = validate_rule_config(self.rule_type, self.config)
        return self


class EvaluationRuleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    rule_type: EvaluationRuleType | None = None
    config: dict[str, Any] | None = None
    evaluator_version: str | None = Field(default=None, max_length=32)
    enabled: bool | None = None
    weight: float | None = Field(default=None, ge=0.0, le=100.0)


class EvaluationRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    suite_id: UUID | None = None
    test_case_id: UUID | None = None
    name: str
    rule_type: str
    config: dict[str, Any]
    evaluator_version: str
    enabled: bool
    weight: float
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class EvaluationEvidencePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    matched_turn_indices: list[int] = Field(default_factory=list)
    matched_snippets: list[str] = Field(default_factory=list)
    expected: Any = None
    actual: Any = None
    tool_calls_inspected: list[dict[str, Any]] = Field(default_factory=list)
    judge_model: str | None = None
    judge_provider: str | None = None
    judge_prompt_version: str | None = None
    judge_rationale: str | None = None
    is_mock_judge: bool = False
    error_detail: str | None = None


class EvaluationResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    test_run_id: UUID
    evaluation_rule_id: UUID | None = None
    rule_name: str
    rule_type: str
    status: str
    score: float
    weight: float
    evidence: dict[str, Any]
    explanation: str
    evaluator_version: str
    formula_version: str
    created_at: datetime


class QAScorecardSummary(BaseModel):
    status: ScorecardStatus
    overall_score: float | None = None
    pass_threshold: float = 100.0
    formula_version: str = "weighted_v1"
    evaluator_version: str = "v1.0"
    total_rules: int = 0
    enabled_rules: int = 0
    passed_count: int = 0
    failed_assertion_count: int = 0
    evaluation_error_count: int = 0
    skipped_count: int = 0
    weighted_earned: float = 0.0
    weighted_possible: float = 0.0
    explanation: str = ""
    evaluated_at: str | None = None


# --------------------------------------------------------- Run & Execution DTOs


class LLMPlaygroundRunRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    agent_kind: AgentKind = AgentKind.VOICE
    agent_version_number: int = Field(..., ge=1, description="Pinned version number.")
    prompt_override: str | None = None
    user_message: str = Field(..., min_length=1)
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    evaluation_rules: list[InlineEvaluationRuleSpec] = Field(default_factory=list)
    allow_mock_fallback: bool = True


class MultiTurnSimulationRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    agent_kind: AgentKind = AgentKind.VOICE
    agent_version_number: int = Field(..., ge=1, description="Pinned version number.")
    suite_id: UUID | None = None
    test_case_id: UUID | None = None
    mode: TestRunMode = TestRunMode.SIMULATION
    input_messages: list[dict[str, Any] | str] = Field(..., min_length=1)
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    evaluation_rules: list[InlineEvaluationRuleSpec] = Field(default_factory=list)
    allow_mock_fallback: bool = True

    @field_validator("input_messages", mode="before")
    @classmethod
    def _normalize_turns(cls, value: Any) -> list[dict[str, Any]]:
        if not value:
            raise ValueError("input_messages must contain at least one turn.")
        turns: list[dict[str, Any]] = []
        for idx, item in enumerate(value):
            if isinstance(item, str):
                text = item.strip()
                if text:
                    turns.append({"role": "user", "content": text, "turn_index": idx})
            elif isinstance(item, dict):
                content = str(
                    item.get("content") or item.get("text") or item.get("user_says") or ""
                ).strip()
                if content:
                    entry = dict(item)
                    entry["role"] = str(item.get("role") or "user")
                    entry["content"] = content
                    turns.append(entry)
        if not turns:
            raise ValueError("input_messages must contain at least one non-empty user message.")
        return turns


class BatchSuiteRunRequest(BaseModel):
    case_ids: list[UUID] | None = None
    agent_version_override: int | None = Field(default=None, ge=1)
    dynamic_variables_override: dict[str, Any] = Field(default_factory=dict)
    allow_mock_fallback: bool = True


class BatchRunAggregationSummary(BaseModel):
    batch_id: str
    suite_id: UUID
    suite_name: str
    overall_status: TestRunStatus
    total_cases: int
    queued_count: int = 0
    running_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    error_count: int = 0
    cancelled_count: int = 0
    not_run_count: int = 0
    average_score: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    runs: list["TestRunResponse"] = Field(default_factory=list)


class TestRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    environment_id: UUID | None = None
    suite_id: UUID | None = None
    batch_id: str | None = None
    test_case_id: UUID | None = None
    agent_id: str
    agent_kind: str
    agent_version_id: UUID | None = None
    agent_version_number: int
    agent_config_hash: str
    pinned_config_snapshot: dict[str, Any] = Field(default_factory=dict)
    mode: str
    status: str
    is_mock_provider: bool
    provider: str
    model: str
    correlation_id: str
    call_id: UUID | None = None
    chat_session_id: UUID | None = None
    transcript_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    events_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    usage_metadata: dict[str, Any] = Field(default_factory=dict)
    latency_metadata: dict[str, Any] = Field(default_factory=dict)
    final_output: dict[str, Any] = Field(default_factory=dict)
    scorecard_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
    evaluation_results: list[EvaluationResultResponse] = Field(default_factory=list)


class WebCallSessionRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    agent_version_number: int = Field(..., ge=1)
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    initial_user_utterance: str | None = None
    evaluation_rules: list[InlineEvaluationRuleSpec] = Field(default_factory=list)
    allow_mock_fallback: bool = True
    require_live_webrtc: bool = False


class WebCallSessionEventRequest(BaseModel):
    event: str = Field(..., pattern="^(start|stop|interrupt|user_utterance|dtmf|complete)$")
    text: str | None = None
    dtmf_digits: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PhoneCallTestRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    agent_version_number: int = Field(..., ge=1)
    to_number: str = Field(..., min_length=4, max_length=32)
    from_number: str | None = Field(default=None, max_length=32)
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    scripted_user_turns: list[str] = Field(default_factory=list)
    evaluation_rules: list[InlineEvaluationRuleSpec] = Field(default_factory=list)
    require_live_carrier: bool = False
