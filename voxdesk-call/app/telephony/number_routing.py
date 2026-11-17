"""Weighted number routing (PART 12, P12-23).

A phone number used to point at exactly one agent. Customers who wanted to split traffic (a 90/10
canary, two languages, two campaigns) had to put a router in front of us. This module lets a number
carry a weighted list instead.

Three rules make it usable in production:

* **Deterministic.** The same caller on the same number always reaches the same agent, because the
  bucket is a hash of ``number + caller`` rather than a random draw. A caller who gets the new agent
  does not flip back to the old one on their second call.
* **Backwards compatible.** The legacy single-agent fields remain readable aliases, so every
  existing number keeps working and every existing query keeps compiling.
* **Total weight is not trusted.** Weights are normalised, so 90/10 and 9/1 and 0.9/0.1 are the
  same configuration; a zero-total set falls back to the first entry rather than dividing by zero.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: Where a number's routing list applies.
ROUTING_CHANNELS: tuple[str, ...] = ("inbound", "outbound", "sms")


class RoutingError(ValueError):
    """A routing table that cannot be honoured."""


@dataclass(frozen=True)
class AgentWeight:
    agent_id: str
    weight: float = 1.0
    label: str = ""

    def __post_init__(self) -> None:
        if not str(self.agent_id or "").strip():
            raise ValueError("a weighted entry needs an agent id")
        try:
            weight = float(self.weight)
        except (TypeError, ValueError) as exc:
            raise ValueError("weights must be finite numbers") from exc
        if not math.isfinite(weight):
            raise ValueError("weights must be finite numbers")
        if weight < 0:
            raise ValueError("weights cannot be negative")
        object.__setattr__(self, "agent_id", str(self.agent_id).strip())
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "label", str(self.label or ""))

    def as_dict(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "weight": float(self.weight), "label": self.label}


@dataclass(frozen=True)
class NumberRouting:
    """The weighted lists for one number, per channel."""

    inbound: tuple[AgentWeight, ...] = ()
    outbound: tuple[AgentWeight, ...] = ()
    sms: tuple[AgentWeight, ...] = ()
    #: Legacy single-agent fields, kept as read aliases.
    legacy_inbound_agent_id: str = ""
    legacy_outbound_agent_id: str = ""
    legacy_sms_agent_id: str = ""

    def __post_init__(self) -> None:
        for channel in ROUTING_CHANNELS:
            entries = getattr(self, channel)
            ids = [entry.agent_id for entry in entries]
            if len(set(ids)) != len(ids):
                raise RoutingError(f"duplicate agent in {channel} routing: {sorted(ids)}")
            total = sum(float(entry.weight) for entry in entries)
            if not math.isfinite(total):
                raise RoutingError(f"{channel} routing has a non-finite total weight")
            if entries and total <= 0:
                raise RoutingError(f"{channel} routing has no weight to distribute")

    # -- read aliases -----------------------------------------------------
    def entries_for(self, channel: str) -> tuple[AgentWeight, ...]:
        if channel not in ROUTING_CHANNELS:
            raise RoutingError(f"unknown channel {channel!r}; expected one of {list(ROUTING_CHANNELS)}")
        entries = getattr(self, channel)
        if entries:
            return entries
        legacy = getattr(self, f"legacy_{channel}_agent_id", "")
        return (AgentWeight(legacy),) if legacy else ()

    @property
    def is_weighted(self) -> bool:
        """True when at least one channel has more than one candidate."""
        return any(len(self.entries_for(channel)) > 1 for channel in ROUTING_CHANNELS)

    # -- selection --------------------------------------------------------
    def select(self, channel: str, *, caller: str = "", salt: str = "") -> str:
        """Pick an agent for this caller, deterministically."""
        entries = self.entries_for(channel)
        if not entries:
            raise RoutingError(f"no agents configured for {channel} routing")
        if len(entries) == 1:
            return entries[0].agent_id

        total = sum(float(entry.weight) for entry in entries)
        digest = hashlib.sha256(f"{salt}|{caller}".encode("utf-8", "replace")).hexdigest()
        point = (int(digest[:12], 16) / float(0xFFFFFFFFFFFF)) * total
        running = 0.0
        for entry in entries:
            running += float(entry.weight)
            if point < running:
                return entry.agent_id
        return entries[-1].agent_id  # floating point edge

    def bucket(self, channel: str, *, caller: str = "", salt: str = "") -> int:
        """Index of the chosen entry — useful for logging why a call went where it did."""
        chosen = self.select(channel, caller=caller, salt=salt)
        for index, entry in enumerate(self.entries_for(channel)):
            if entry.agent_id == chosen:
                return index
        return 0

    def as_dict(self) -> dict[str, Any]:
        return {
            channel: [entry.as_dict() for entry in self.entries_for(channel)]
            for channel in ROUTING_CHANNELS
        }


def build_routing(
    payload: Mapping[str, Any] | None,
    *,
    legacy_inbound_agent_id: str = "",
    legacy_outbound_agent_id: str = "",
    legacy_sms_agent_id: str = "",
) -> NumberRouting:
    """Build routing from stored JSON, falling back to the legacy single-agent columns."""

    def entries(channel: str) -> tuple[AgentWeight, ...]:
        raw = (payload or {}).get(channel)
        if raw is None:
            raw = []
        if isinstance(raw, Mapping):
            raw = [{"agent_id": key, "weight": value} for key, value in raw.items()]
        if not isinstance(raw, (list, tuple)):
            raise RoutingError(f"{channel} routing must be a list or agent-to-weight mapping")
        out: list[AgentWeight] = []
        for index, item in enumerate(raw):
            if isinstance(item, AgentWeight):
                out.append(item)
            elif isinstance(item, Mapping) and item.get("agent_id"):
                try:
                    out.append(
                        AgentWeight(
                            agent_id=str(item["agent_id"]),
                            weight=float(item.get("weight", 1.0)),
                            label=str(item.get("label") or ""),
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise RoutingError(
                        f"invalid {channel} routing entry at index {index}: {exc}"
                    ) from exc
            elif isinstance(item, str) and item.strip():
                out.append(AgentWeight(item.strip()))
            else:
                raise RoutingError(f"invalid {channel} routing entry at index {index}")
        return tuple(out)

    return NumberRouting(
        inbound=entries("inbound"),
        outbound=entries("outbound"),
        sms=entries("sms"),
        legacy_inbound_agent_id=legacy_inbound_agent_id,
        legacy_outbound_agent_id=legacy_outbound_agent_id,
        legacy_sms_agent_id=legacy_sms_agent_id,
    )


def distribution(routing: NumberRouting, channel: str, callers: Iterable[str]) -> dict[str, int]:
    """How a set of callers would be split — the number a dashboard shows before you save."""
    counts: dict[str, int] = {}
    for caller in callers:
        chosen = routing.select(channel, caller=caller)
        counts[chosen] = counts.get(chosen, 0) + 1
    return counts
