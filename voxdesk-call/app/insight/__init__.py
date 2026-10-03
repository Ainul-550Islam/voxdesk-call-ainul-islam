"""Cited, governed insight generation.

``service`` answers product questions from tenant-scoped knowledge retrieval and
returns citations alongside the generated text, so a caller can always trace a
claim back to the document it came from. Generation goes through the governed
AI gateway, which means budget, policy and guardrail checks apply exactly as
they do for every other model call in the platform.
"""

__all__ = ["service"]
