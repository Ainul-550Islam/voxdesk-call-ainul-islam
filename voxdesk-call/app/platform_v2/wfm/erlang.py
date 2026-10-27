"""Erlang C and Erlang A: how many people does this workload need?

Two models, because a contact centre is two different queues:

* **Erlang C** (M/M/c) assumes nobody ever gives up. It is the standard
  staffing formula and the one every published table is written for, so it is
  what :func:`required_agents` defaults to and what the reference vectors test.
* **Erlang A** (M/M/c+M) adds abandonment with an exponential patience rate.
  Real callers hang up, and a model that ignores that over-staffs a queue whose
  service level is already met — which matters when the number it produces is a
  roster somebody has to work.

Numerical care
--------------
Every term is evaluated in log space with ``math.lgamma`` for the factorial, so
``erlang_c(400, 380)`` — a perfectly ordinary queue — does not overflow. The
classic float implementation dies at roughly 170 agents; the tests pin the
large-traffic case for exactly that reason.

No model here is a promise: the module returns *probabilities* and the caller
decides what service level it is willing to pay for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: A queue cannot be staffed below this many people and still be "stable" for
#: the load, so every search starts here rather than at zero.
MIN_AGENTS = 1
#: Hard stop for a search. Beyond this the workload is not a staffing problem.
MAX_AGENTS = 5000


@dataclass(frozen=True)
class ErlangResult:
    """The measured behaviour of one candidate staffing level."""

    agents: int
    offered_load: float
    probability_wait: float
    service_level: float
    occupancy: float
    abandonment: float = 0.0
    average_wait_seconds: float = 0.0

    @property
    def stable(self) -> bool:
        return self.agents > self.offered_load


def _log_factorial(n: int) -> float:
    return math.lgamma(n + 1)


def _logsumexp(values: list[float]) -> float:
    """Stable ``log(sum(exp(v)))``."""
    if not values:
        return float("-inf")
    top = max(values)
    return top + math.log(sum(math.exp(v - top) for v in values))


def erlang_c(agents: int, offered_load: float) -> float:
    """Probability that an arriving caller has to wait (M/M/c).

    Returns ``1.0`` when the system is unstable (``agents <= offered_load``):
    a queue that cannot drain has every caller wait, which is the honest answer
    and not a division by zero.
    """
    if agents < 0:
        raise ValueError("agents must be non-negative")
    if offered_load <= 0:
        return 0.0
    if agents == 0:
        return 1.0
    if agents <= offered_load:
        return 1.0

    log_a = math.log(offered_load)
    # log(a^k / k!), built incrementally so one queue costs O(c) with no
    # factorial anywhere near an overflow.
    parts = [0.0]
    running = 0.0
    for k in range(1, agents):
        running += log_a - math.log(k)
        parts.append(running)
    tail = running + log_a - math.log(agents)
    log_full = tail + math.log(agents / (agents - offered_load))
    denominator = _logsumexp(parts + [log_full])
    if denominator == float("-inf"):
        return 0.0
    return min(1.0, max(0.0, math.exp(log_full - denominator)))


def service_level(
    agents: int,
    offered_load: float,
    *,
    target_seconds: float,
    average_handle_seconds: float,
) -> float:
    """Fraction of callers answered within ``target_seconds`` (Erlang C).

    ``P(wait < t) = 1 - P(wait > 0) * exp(-(c*mu - lambda) * t)`` with
    ``mu = 1/AHT``.
    """
    if average_handle_seconds <= 0:
        raise ValueError("average_handle_seconds must be positive")
    if target_seconds < 0:
        raise ValueError("target_seconds must be non-negative")
    wait = erlang_c(agents, offered_load)
    if wait <= 0:
        return 1.0
    if agents <= offered_load:
        return 0.0
    mu = 1.0 / average_handle_seconds
    rate = agents * mu - offered_load * mu
    return min(1.0, max(0.0, 1.0 - wait * math.exp(-rate * target_seconds)))


def average_wait_seconds(
    agents: int, offered_load: float, *, average_handle_seconds: float
) -> float:
    """Expected wait in the queue, in seconds (Erlang C: ``C / (c*mu - lambda)``)."""
    wait = erlang_c(agents, offered_load)
    if wait <= 0:
        return 0.0
    if agents <= offered_load:
        # A queue that cannot drain has an unbounded wait. Returning 0.0 here read as
        # "callers are served immediately", which is the opposite of the truth; the
        # staffing search never evaluates an unstable configuration, so nothing that
        # serialises this number can see the infinity.
        return float("inf")
    mu = 1.0 / average_handle_seconds
    return wait / (agents * mu - offered_load * mu)


def erlang_a(
    agents: int,
    offered_load: float,
    *,
    average_handle_seconds: float,
    average_patience_seconds: float,
    target_seconds: float | None = None,
) -> ErlangResult:
    """M/M/c+M state distribution with abandonment (Erlang A).

    ``average_patience_seconds`` is the mean time a caller is willing to wait
    (an exponential patience distribution). States ``n > c`` shrink by the
    abandonment rate, so the queue always drains even when ``agents <= load`` —
    which is the one behaviour that distinguishes this model from Erlang C.

    ``service_level`` means the same thing it means in :func:`service_level` —
    the fraction answered within ``target_seconds`` — but only when that target
    is supplied. Without it there is no answer-time target to compare against, so
    the field reports the probability of being answered immediately, which is also
    what ``probability_wait`` complements. Two different numbers under one name is
    how a 90/20 target silently becomes a 90%-immediate-answer target; pass
    ``target_seconds`` whenever the number will be shown as a service level.
    """
    if average_handle_seconds <= 0 or average_patience_seconds <= 0:
        raise ValueError("handle and patience seconds must be positive")
    if agents < 1:
        raise ValueError("agents must be at least 1")
    if offered_load <= 0:
        return ErlangResult(agents, max(offered_load, 0.0), 0.0, 1.0, 0.0, 0.0, 0.0)

    # Truncation point: past this, total probability is below 1e-15 for every
    # realistic (load, patience) pair, so the missing tail cannot move a metric.
    c = agents
    gamma = average_handle_seconds / average_patience_seconds  # theta/mu
    log_a = math.log(offered_load)
    logs: list[float] = []
    running = 0.0
    for n in range(0, c + 1):
        if n:
            # incremental log(a^n / n!); accumulating the whole term each pass
            # would sum the distribution instead of walking it
            running += log_a - math.log(n)
        logs.append(running)
    queue_log = logs[c]
    for k in range(1, 4000):
        queue_log += log_a - math.log(c + k * gamma)
        logs.append(queue_log)
        if k > 50 and queue_log - max(logs) < -60:
            break
    norm = _logsumexp(logs)
    probs = [math.exp(v - norm) for v in logs]

    p_wait = sum(probs[c:])
    # Abandonment: in state c+k every waiting caller gives up at rate theta, so
    # the fraction of arrivals that abandon is sum_k p_{c+k} * k*theta / (c*mu + k*theta).
    abandon = 0.0
    for k in range(1, len(probs) - c):
        p = probs[c + k]
        if p <= 0:
            continue
        abandon += p * (k * gamma) / (c + k * gamma)
    served = max(1e-12, 1.0 - abandon)
    # Occupancy is measured against the work that is actually served.
    occupancy = (offered_load * served) / c
    aht = average_handle_seconds
    wait_c = erlang_c(c, offered_load * served) if offered_load * served > 0 else 0.0
    mean_wait = 0.0
    if c > offered_load * served:
        mu = 1.0 / aht
        mean_wait = wait_c / (c * mu - offered_load * served * mu)
    if target_seconds is None:
        within_target = 1.0 - p_wait
    elif offered_load * served <= 0 or c <= offered_load * served:
        within_target = 0.0
    else:
        mu = 1.0 / aht
        rate = c * mu - offered_load * served * mu
        within_target = 1.0 - p_wait * math.exp(-rate * max(0.0, float(target_seconds)))
    return ErlangResult(
        agents=c,
        offered_load=offered_load,
        probability_wait=p_wait,
        service_level=min(1.0, max(0.0, within_target)),
        occupancy=min(1.0, occupancy),
        abandonment=min(1.0, max(0.0, abandon)),
        average_wait_seconds=mean_wait,
    )


def required_agents(
    *,
    interval_volume: float,
    average_handle_seconds: float,
    interval_seconds: float = 900.0,
    target_service_level: float,
    target_answer_seconds: float,
    max_occupancy: float = 1.0,
    model: str = "erlang_c",
    average_patience_seconds: float = 300.0,
    max_agents: int = MAX_AGENTS,
) -> ErlangResult:
    """Smallest agent count that meets the service level **and** the occupancy cap.

    The occupancy cap is not decoration: Erlang C will happily satisfy a 90/20
    target at 100% occupancy for a low-volume queue, which produces a roster with
    no time for after-call work. ``max_occupancy`` is the operator's "leave room
    for the work that is not a call".

    Raises ``ValueError`` for an impossible target (>1.0) instead of returning a
    number that can never be met.
    """
    if not 0.0 < target_service_level <= 1.0:
        raise ValueError("target_service_level must be in (0, 1]")
    if target_answer_seconds < 0:
        raise ValueError("target_answer_seconds must be non-negative")
    if average_handle_seconds <= 0:
        raise ValueError("average_handle_seconds must be positive")
    if not 0.0 < max_occupancy <= 1.0:
        raise ValueError("max_occupancy must be in (0, 1]")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if model not in ("erlang_c", "erlang_a"):
        # Silently falling back to Erlang C for an unknown name is how a caller who
        # asked for abandonment modelling gets a number without it.
        raise ValueError(f"unknown model {model!r}; expected erlang_c or erlang_a")
    volume = max(0.0, float(interval_volume))
    if volume == 0:
        return ErlangResult(0, 0.0, 0.0, 1.0, 0.0)
    # Erlangs: how many agents would be busy at 100% occupancy. The interval
    # length is what turns a call count into load, and getting it wrong is the
    # single most common way a staffing number comes out 4x too large.
    offered = volume * average_handle_seconds / interval_seconds

    start = max(MIN_AGENTS, int(math.ceil(offered / max_occupancy)))
    start = max(start, int(math.floor(offered)) + 1)
    for agents in range(start, max_agents + 1):
        if model == "erlang_a":
            result = erlang_a(
                agents,
                offered,
                average_handle_seconds=average_handle_seconds,
                average_patience_seconds=average_patience_seconds,
                # Same meaning as the Erlang C branch: answered within the target.
                target_seconds=target_answer_seconds,
            )
            met = result.service_level >= target_service_level
        else:
            result = ErlangResult(
                agents=agents,
                offered_load=offered,
                probability_wait=erlang_c(agents, offered),
                service_level=service_level(
                    agents,
                    offered,
                    target_seconds=target_answer_seconds,
                    average_handle_seconds=average_handle_seconds,
                ),
                occupancy=min(1.0, offered / agents),
                average_wait_seconds=average_wait_seconds(
                    agents, offered, average_handle_seconds=average_handle_seconds
                ),
            )
            met = result.service_level >= target_service_level
        if met and result.occupancy <= max_occupancy + 1e-9:
            return result
    # Unreachable for sane inputs (occupancy -> 0 as agents grow), but a search
    # that runs out must say so rather than return the last candidate.
    raise ValueError("no agent count met the target within the search bound")


__all__ = [
    "MAX_AGENTS",
    "MIN_AGENTS",
    "ErlangResult",
    "average_wait_seconds",
    "erlang_a",
    "erlang_c",
    "required_agents",
    "service_level",
]
