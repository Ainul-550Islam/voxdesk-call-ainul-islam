"""The workflow library (PART 14, P14-04).

Layout, in dependency order:

* `catalog`            -- what integrations exist, how they authenticate, and
                          which scopes each action needs (pure data);
* `preset_functions`   -- the typed steps an agent may call, with validation;
* `tool_store`         -- install / enable / review per tenant, persisted;
* `pre_call`           -- budgeted context fetch before the call connects;
* `post_call`          -- the plan and single-step semantics after it ends;
* `executor`           -- durable execution on `app.jobs`, one job per step.

Nothing in this package dials a vendor directly: the adapters live under
`app/integrations/` and are injected, which is what lets the whole library be
tested without a network.
"""

from app.workflows import catalog, executor, post_call, pre_call, preset_functions, tool_store

__all__ = [
    "catalog",
    "executor",
    "post_call",
    "pre_call",
    "preset_functions",
    "tool_store",
]
