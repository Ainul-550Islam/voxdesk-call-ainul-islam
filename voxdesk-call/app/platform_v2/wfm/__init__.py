"""Workforce management: history, forecast, staffing, scheduling, adherence.

The modules build on each other in one direction only::

    history  ->  forecast  ->  staffing  ->  scheduler  ->  publish
                     |            |            |
                  accuracy     shifts      time_off / swaps_and_bids
                     |            |
                 intraday    adherence

Reading the dependency arrows is the fastest way to know where a number came
from: nothing below ``forecast`` invents volume, nothing below ``staffing``
invents an agent count, and ``scheduler`` is the only module that can decide a
rota is impossible.
"""

from __future__ import annotations

__all__: list[str] = []
