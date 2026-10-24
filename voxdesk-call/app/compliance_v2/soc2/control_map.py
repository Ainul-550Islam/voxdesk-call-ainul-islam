"""SOC 2 control map derived from the repository's control matrix.

The matrix (``docs/COMPLIANCE/SOC2_CONTROL_MATRIX.md``) is the authority for
which control ids exist; this module parses it rather than restating it, so a
control added to the document without a collector fails at import time instead of
being silently unevidenced.

Parsing rules:

* a control id is any ``CCx.y``, ``Ax.y``, ``Cx.y``, ``PIx.y`` or ``Px.y`` token
  inside a bold span before the "Residual Operational Gaps" heading — the table's
  compound cells (``**CC7.1 / CC7.2**``) therefore yield both ids;
* section 2 is prose, not a control table. An id that only appears there
  (``CC9.2`` in the vendor-management note) is reported in ``unmapped``: it is
  named in the document, no automated collector can evidence it, and the honest
  status for it is NOT_CONFIGURED;
* a control named in the *table* with no collector raises
  :class:`ControlMapError`. That is the drift this module exists to catch.

Nothing here claims compliance. The map says which control an automated
collector produces evidence for; whether that evidence satisfies an auditor is
their judgement, not this module's.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

#: Where the matrix lives, relative to the repository root.
MATRIX_PATH = Path("docs/COMPLIANCE/SOC2_CONTROL_MATRIX.md")

#: collector name -> the controls it evidences. This is the contract between the
#: collectors (which declare their own ids) and the matrix; a mismatch raises.
COLLECTOR_CONTROLS: dict[str, tuple[str, ...]] = {
    "access": ("CC6.1", "CC6.2", "CC6.3"),
    "change": ("CC7.1", "CC7.2"),
    "ops": ("CC6.6", "CC6.7", "CC7.3", "A1.1", "A1.2", "C1.1", "C1.2", "PI1.1", "PI1.4"),
    "incident": ("P1.1", "P3.1", "P4.2"),
}

#: Every control id a collector covers, flattened.
MAPPED_CONTROLS: frozenset[str] = frozenset(
    control for controls in COLLECTOR_CONTROLS.values() for control in controls
)

_BOLD_SPAN = re.compile(r"\*\*([^*]+)\*\*")
_CONTROL_ID = re.compile(r"\b(?:CC|A|C|PI|P)\d+\.\d+\b")
_SECTION_TWO = re.compile(r"^##\s*2\.", re.MULTILINE)


class ControlMapError(RuntimeError):
    """The matrix names a control no collector covers, or names no control at all."""


@dataclasses.dataclass(frozen=True)
class ControlMap:
    """Parsed control ids, each mapped to the collector that evidences it."""

    controls: dict[str, str] = dataclasses.field(default_factory=dict)
    unmapped: tuple[str, ...] = ()
    source: str = ""
    document_present: bool = False

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.controls))

    def collector_for(self, control_id: str) -> str:
        return self.controls.get(control_id, "")

    def controls_for_collector(self, collector: str) -> tuple[str, ...]:
        return tuple(
            sorted(control for control, owner in self.controls.items() if owner == collector)
        )

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "document_present": self.document_present,
            "controls": dict(sorted(self.controls.items())),
            "unmapped": list(self.unmapped),
            "collectors": {
                name: self.controls_for_collector(name) for name in sorted(COLLECTOR_CONTROLS)
            },
        }


def find_matrix(root: str | Path | None = None) -> Path:
    """Locate the matrix relative to an explicit root or the working directory."""
    base = Path(root) if root is not None else Path.cwd()
    return base / MATRIX_PATH


def parse_control_ids(text: str) -> tuple[list[str], list[str]]:
    """Return ``(table_ids, residual_section_ids)`` in document order."""
    split = _SECTION_TWO.search(text)
    head = text[: split.start()] if split else text
    tail = text[split.start() :] if split else ""

    def ids_in(block: str) -> list[str]:
        found: list[str] = []
        for span in _BOLD_SPAN.findall(block):
            for match in _CONTROL_ID.findall(span):
                if match not in found:
                    found.append(match)
        return found

    return ids_in(head), ids_in(tail)


def owner_for(control_id: str) -> str:
    """The collector that evidences ``control_id``, or ``""``."""
    for collector, controls in COLLECTOR_CONTROLS.items():
        if control_id in controls:
            return collector
    return ""


def load_control_map(root: str | Path | None = None) -> ControlMap:
    """Parse the matrix and validate coverage."""
    path = find_matrix(root)
    if not path.exists():
        return ControlMap(controls={}, unmapped=(), source=str(path), document_present=False)
    text = path.read_text(encoding="utf-8")
    table_ids, residual_ids = parse_control_ids(text)
    if not table_ids:
        raise ControlMapError(f"no control ids were found in {path}")

    missing = [control for control in table_ids if not owner_for(control)]
    if missing:
        raise ControlMapError(
            "the control matrix names control(s) with no evidence collector: "
            + ", ".join(missing)
        )

    controls = {control: owner_for(control) for control in table_ids}
    unmapped = tuple(control for control in residual_ids if control not in controls)

    declared = {control for controls in COLLECTOR_CONTROLS.values() for control in controls}
    leftover = sorted(declared - set(controls))
    if leftover:
        # A collector evidencing a control the document does not mention is the
        # mirror image of the drift above; report it rather than hide it.
        unmapped = tuple(sorted(set(unmapped) | set(leftover)))

    return ControlMap(
        controls=controls,
        unmapped=unmapped,
        source=str(path),
        document_present=True,
    )


def control_ids(root: str | Path | None = None) -> tuple[str, ...]:
    return load_control_map(root).ids


__all__ = [
    "COLLECTOR_CONTROLS",
    "MAPPED_CONTROLS",
    "MATRIX_PATH",
    "ControlMap",
    "ControlMapError",
    "control_ids",
    "find_matrix",
    "load_control_map",
    "owner_for",
    "parse_control_ids",
]
