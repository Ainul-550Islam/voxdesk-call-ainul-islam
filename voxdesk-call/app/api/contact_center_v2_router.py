"""One aggregator for the whole Batch 04 contact-center-v2 HTTP surface.

``app/main.py`` mounts this single router (Batch 06). The per-area modules keep
their own prefixes, permissions and tests; this file exists so the entry point
stays one line per surface and so a new CC2 area has exactly one obvious place
to be registered.

Composed surfaces
-----------------
* ``/api/cc2/dialer``        — preview/predictive policy lifecycle and pacing
* ``/api/cc2/softphone``     — sessions, devices, calls, transfers
* ``/api/cc2/softphone/signaling`` — the agent's one-ticket media socket
* ``/api/cc2/assist``        — agent-assist turns, suggestions, scripts
* ``/api/cc2/acd``           — queues, skills, routing, callbacks, wallboard
* ``/api/cc2/agent-states``  — presence, wrap-up, occupancy, intervals
* ``/api/cc2/supervisor``    — supervisor wallboard and occupancy reads
* ``/ws/cc2/assist/{call_id}`` and ``/ws/cc2/supervisor``, plus the short-lived
  stream tickets ``/api/cc2/assist/calls/{call_id}/stream-ticket`` and
  ``/api/cc2/supervisor/stream-ticket``.

Authorisation stays where it already was: every handler declares
``require_permission(...)`` itself, and the sockets resolve their tenant from a
signed, short-lived stream ticket rather than from a header. Mounting does not
add or relax a single permission — ``security_harness/authz_matrix.py`` reads the
dependency tree of each operation and now sees these routes for the first time.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    acd_routes,
    agent_assist_routes,
    agent_states_v2_routes,
    dialer_v2_routes,
    softphone_routes,
    supervisor_v2_routes,
)
from app.api.ws import assist_ws, supervisor_ws
from app.contact_center_v2.softphone import websocket as softphone_signaling

router = APIRouter()

# BATCH06:cc2_surface_composition
router.include_router(dialer_v2_routes.router)
router.include_router(softphone_routes.router)
router.include_router(agent_assist_routes.router)
router.include_router(acd_routes.router)
router.include_router(agent_states_v2_routes.router)
router.include_router(supervisor_v2_routes.router)

# The signaling socket was implemented, tested and never mounted: a softphone
# could register, receive a one-ticket URL and then have nowhere to open it. Its
# router already carries the ``/api/cc2/softphone`` prefix, so it is composed
# next to ``softphone_routes.router`` rather than nested inside it (nesting would
# produce ``/api/cc2/softphone/api/cc2/softphone/signaling``).
router.include_router(softphone_signaling.router)
router.include_router(assist_ws.router)
router.include_router(supervisor_ws.router)

__all__ = ["router"]
