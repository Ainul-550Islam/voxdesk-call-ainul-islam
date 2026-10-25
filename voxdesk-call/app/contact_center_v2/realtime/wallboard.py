"""Change detection for bounded supervisor wallboard streams."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


def without_stream_clock(snapshot: dict) -> dict:
    payload = dict(snapshot)
    payload.pop("streamed_at", None)
    return payload


@dataclass
class WallboardStreamState:
    sent: dict[str, str] = field(default_factory=dict)

    def changed(self, queue_id: str, queue: dict) -> bool:
        digest = hashlib.sha256(
            json.dumps(queue, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if self.sent.get(queue_id) == digest:
            return False
        self.sent[queue_id] = digest
        return True


__all__ = ["WallboardStreamState", "without_stream_clock"]
