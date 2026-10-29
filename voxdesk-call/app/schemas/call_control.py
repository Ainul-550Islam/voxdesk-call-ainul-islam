"""Request/response contracts for the live call-control API.

These models are the boundary between the dashboard and
:mod:`app.telephony.call_control_service`. Every field is bounded and every
free-text field is length-capped, because each of them ends up either in a
TwiML document or in a durable receipt row.

Two rules shaped the shapes below:

* **A batch reports per item.** ``DtmfBatchResponse`` and
  ``BulkTransferResponse`` each carry one result per requested item. A batch
  that failed as a whole would force the caller to guess which side effects
  actually reached the carrier, and a retry would then duplicate them.
* **Capabilities travel with the answer.** Every response carries the flags
  the active provider confirmed, so the dashboard disables a control it cannot
  honour instead of offering an action that is guaranteed to fail.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---- shared bounds -------------------------------------------------------

#: Twilio's documented DTMF alphabet. ``w``/``W`` are the wait-for-silence
#: digits; anything else (letters, ``<``, ``&``) is either invalid or an
#: injection attempt against the TwiML we build.
DTMF_PATTERN = r"^[0-9#*wW]+$"
_DTMF_RE = re.compile(DTMF_PATTERN)

#: One provider call per digit run, so a batch is capped to keep a single
#: request from turning into an unbounded burst of carrier writes.
MAX_DTMF_BATCH_ITEMS = 25
MAX_DTMF_DIGITS = 32
MAX_BULK_TRANSFER_ITEMS = 25
MAX_WHISPER_LENGTH = 500
MAX_REASON_LENGTH = 500
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MIN_IDEMPOTENCY_KEY_LENGTH = 8

E164_PATTERN = r"^\+[1-9]\d{7,14}$"
_E164_RE = re.compile(E164_PATTERN)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


def normalize_e164(value: str) -> str:
    """Strip the cosmetic separators a dashboard paste always carries."""
    return re.sub(r"[\s\-\(\)]", "", (value or "").strip())


def is_e164(value: str) -> bool:
    return bool(_E164_RE.fullmatch(value or ""))


# ---- pause / resume ------------------------------------------------------


class CallPauseRequest(_Strict):
    """Ask for a live call to be parked on hold.

    ``reason`` is operator-facing audit text only; it is never spoken to the
    caller. ``hold_message`` is, so it is capped and is run through the same
    whisper sanitiser every other spoken string goes through.
    """

    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)
    hold_message: str | None = Field(default=None, max_length=200)


class CallControlCapabilities(_Strict):
    """What the active provider confirmed for this deployment."""

    pause: bool = False
    resume: bool = False
    dtmf: bool = False
    transfer_cancel: bool = False
    whisper: bool = False
    bulk_transfer: bool = False


class CallPauseResponse(_Strict):
    call_id: str
    action: str
    status: str
    provider: str
    provider_status: str
    reason: str | None = None
    idempotency_key: str
    replayed: bool = False
    capabilities: CallControlCapabilities = Field(default_factory=CallControlCapabilities)
    at: str


class CallResumeRequest(_Strict):
    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)


class CallResumeResponse(_Strict):
    call_id: str
    action: str
    status: str
    provider: str
    provider_status: str
    reason: str | None = None
    idempotency_key: str
    replayed: bool = False
    capabilities: CallControlCapabilities = Field(default_factory=CallControlCapabilities)
    at: str


# ---- DTMF batch ----------------------------------------------------------


class DtmfItem(_Strict):
    """One digit run, with its own idempotency key.

    The key is per item and not per batch on purpose: each run is a separate
    carrier side effect, so each one needs its own replay record. A batch-level
    key would make "retry the two that failed" impossible without re-sending
    the three that succeeded.
    """

    digits: str = Field(min_length=1, max_length=MAX_DTMF_DIGITS, pattern=DTMF_PATTERN)
    idempotency_key: str | None = Field(
        default=None, min_length=MIN_IDEMPOTENCY_KEY_LENGTH, max_length=MAX_IDEMPOTENCY_KEY_LENGTH
    )


class DtmfBatchRequest(_Strict):
    items: list[DtmfItem] = Field(min_length=1, max_length=MAX_DTMF_BATCH_ITEMS)


class DtmfItemResult(_Strict):
    index: int
    digits: str
    status: str
    idempotency_key: str
    replayed: bool = False
    error: str | None = None


class DtmfBatchResponse(_Strict):
    call_id: str
    action: str
    provider: str
    items: list[DtmfItemResult]
    sent: int = 0
    replayed: int = 0
    rejected: int = 0
    capabilities: CallControlCapabilities = Field(default_factory=CallControlCapabilities)
    at: str


# ---- transfer cancel / whisper ------------------------------------------


class TransferCancelRequest(_Strict):
    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)
    idempotency_key: str | None = Field(
        default=None, min_length=MIN_IDEMPOTENCY_KEY_LENGTH, max_length=MAX_IDEMPOTENCY_KEY_LENGTH
    )


class TransferCancelResponse(_Strict):
    call_id: str
    action: str
    transfer_state: str
    provider: str
    provider_status: str
    reason: str | None = None
    idempotency_key: str
    replayed: bool = False
    capabilities: CallControlCapabilities = Field(default_factory=CallControlCapabilities)
    at: str


class TransferWhisperRequest(_Strict):
    """Text spoken to the human agent leg only, never to the caller.

    ``MAX_WHISPER_LENGTH`` is the same bound ``transfer_control_routes``
    enforces on ``TransferRequest.whisper``, so a whisper cannot smuggle in
    more than a <Say> should ever carry.
    """

    message: str = Field(min_length=1, max_length=MAX_WHISPER_LENGTH)
    idempotency_key: str | None = Field(
        default=None, min_length=MIN_IDEMPOTENCY_KEY_LENGTH, max_length=MAX_IDEMPOTENCY_KEY_LENGTH
    )

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("whisper message must not be blank")
        return value


class TransferWhisperResponse(_Strict):
    call_id: str
    action: str
    transfer_state: str
    provider: str
    provider_status: str
    #: The sanitized text that was actually delivered. Credentials and phone
    #: digit runs are removed before the string is spoken, so the stored value
    #: is never the raw operator input.
    message: str
    idempotency_key: str
    replayed: bool = False
    capabilities: CallControlCapabilities = Field(default_factory=CallControlCapabilities)
    at: str


# ---- bulk transfer -------------------------------------------------------


class BulkTransferItem(_Strict):
    call_id: str = Field(min_length=32, max_length=64)
    destination: str = Field(min_length=8, max_length=64)
    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)
    idempotency_key: str | None = Field(
        default=None, min_length=MIN_IDEMPOTENCY_KEY_LENGTH, max_length=MAX_IDEMPOTENCY_KEY_LENGTH
    )

    @field_validator("destination")
    @classmethod
    def _validate_destination(cls, value: str) -> str:
        normalized = normalize_e164(value)
        if not is_e164(normalized):
            raise ValueError("destination must be a valid E.164 phone number")
        return normalized


class BulkTransferRequest(_Strict):
    items: list[BulkTransferItem] = Field(min_length=1, max_length=MAX_BULK_TRANSFER_ITEMS)
    idempotency_key: str | None = Field(
        default=None, min_length=MIN_IDEMPOTENCY_KEY_LENGTH, max_length=MAX_IDEMPOTENCY_KEY_LENGTH
    )


class BulkTransferItemResult(_Strict):
    call_id: str
    index: int
    status: str
    transfer_state: str | None = None
    destination: str | None = None
    idempotency_key: str | None = None
    replayed: bool = False
    error_code: str | None = None
    error: str | None = None


class BulkTransferResponse(_Strict):
    batch_id: str
    action: str
    provider: str
    items: list[BulkTransferItemResult]
    total: int = 0
    succeeded: int = 0
    replayed: int = 0
    failed: int = 0
    capabilities: CallControlCapabilities = Field(default_factory=CallControlCapabilities)
    at: str


# ---- workflow outcome write-back ----------------------------------------


class OutcomeWritebackResult(_Strict):
    """What one integration target did with one call outcome."""

    target: str
    status: str
    provider: str | None = None
    external_id: str | None = None
    writeback_id: str | None = None
    replayed: bool = False
    error_code: str | None = None
    error: str | None = None
    at: str
