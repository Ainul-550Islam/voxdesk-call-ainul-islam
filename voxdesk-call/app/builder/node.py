"""Batch-07 Extension — visual flow builder contract (drag-and-drop nodes).
Not a full UI; defines node types that would back a builder."""
from __future__ import annotations

class FlowNode:
    def __init__(self, node_type: str, params: dict) -> None:
        self.node_type = node_type
        self.params = params
