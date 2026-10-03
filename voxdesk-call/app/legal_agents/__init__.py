"""Legal-domain agent specialisations.

Built on top of the generic ``app.specialized_agents`` registry: this package
adds the legal agent definitions, their billing guard/operations and the
supporting registry wiring. It is the worked example of how a vertical is added
without touching the core agent runtime.
"""

__all__ = [
    "agent_registry",
    "billing_guard",
    "billing_ops",
]
