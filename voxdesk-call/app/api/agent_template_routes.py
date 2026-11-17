"""Agent template gallery (PART 18, P18-01).

Three endpoints, in the order an operator uses them:

``GET  /api/agent-templates``               — the gallery, flow omitted
``GET  /api/agent-templates/{id}``          — one template including its flow
``POST /api/agent-templates/{id}/instantiate`` — create a DRAFT agent from it

Design notes
------------
* **Instantiation always creates a DRAFT.** A template that could create an
  active, calling agent in one request is a footgun: nothing has been reviewed,
  no phone number is attached, and the caller has not seen a test call. The
  agent is created with ``status="draft"`` and its config/flow copied in, so the
  operator can inspect it in the builder before anything dials.
* **The template is a starting point, not a binding.** The created agent owns a
  deep copy. Editing the agent never writes back to the template file.
* **Nothing here touches the network or a provider.** Instantiating a template
  needs no API key, which is what makes ``make demo`` work with fake providers.
* **Tenant scoping comes from the token**, through the same
  ``require_permission`` dependency every other agent route uses. A template id
  is global; the agent is not.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_templates import TemplateError, all_templates, get_template
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import Agent
from app.db.session import get_session

router = APIRouter(prefix="/api/agent-templates", tags=["agent-templates"])

#: Agent names are shown in lists and spoken in some prompts; keep them sane.
_MAX_NAME = 120
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


class InstantiateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"name": "Front desk — Dhaka", "config_overrides": {"voice": "vd-voice-sadia"}}]})

    name: str | None = Field(default=None, max_length=_MAX_NAME)
    #: Overrides applied on top of the template config, e.g. a real voice id.
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class InstantiatedAgent(BaseModel):
    id: str
    name: str
    status: str
    template_id: str
    external_key: str


def _external_key(tenant_id: uuid.UUID, template_id: str, name: str) -> str:
    """A stable, unique, human-recognisable key for the new agent.

    ``external_key`` is unique per tenant, so a second instantiation of the same
    template must not collide — the caller's name disambiguates it.
    """
    slug = _NOT_SLUG.sub("-", name.lower()).strip("-")[:32] or template_id
    return f"{template_id}-{slug}"[:_MAX_NAME]


@router.get("")
async def list_templates(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
) -> dict[str, Any]:
    """The gallery. Flows are omitted: they are large and the list is a menu."""
    return {
        "templates": [t.as_dict(include_flow=False) for t in all_templates()],
        "total": len(all_templates()),
    }


@router.get("/{template_id}")
async def read_template(
    template_id: str = Path(..., examples=["receptionist"]),
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
) -> dict[str, Any]:
    """One starter template in full: flow, tools, variables, analysis schema, simulation cases, compliance notes."""
    try:
        template = get_template(template_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown template") from None
    return template.as_dict(include_flow=True)


@router.post("/{template_id}/instantiate", status_code=status.HTTP_201_CREATED)
async def instantiate_template(
    template_id: str,
    payload: InstantiateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> InstantiatedAgent:
    """Create a draft agent from a template.

    The result is always a draft: a template that went straight to live would be a production agent nobody reviewed. 409 if the derived external key is already taken in this workspace.
    """
    try:
        template = get_template(template_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown template") from None
    except TemplateError as exc:  # a shipped template that does not validate
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"template catalog is unhealthy: {exc}",
        ) from exc

    name = (payload.name or template.name).strip()[:_MAX_NAME] or template.name

    config: dict[str, Any] = dict(template.config)
    config["template_id"] = template.id
    config["flow"] = dict(template.flow)  # the agent owns a copy
    config["dynamic_variables"] = [v.as_dict() for v in template.dynamic_variables]
    config["analysis_schema"] = dict(template.analysis_schema)
    config["tools"] = [dict(t) for t in template.tools]
    config["kb_placeholders"] = list(template.kb_placeholders)
    config["compliance_notes"] = list(template.compliance_notes)
    config.update(payload.config_overrides)

    external_key = _external_key(ctx.tenant_id, template.id, name)

    existing = await session.scalar(
        select(func.count())
        .select_from(Agent)
        .where(Agent.tenant_id == ctx.tenant_id, Agent.external_key == external_key)
    )
    if existing:
        # Fail loudly rather than silently renaming: the operator asked for this
        # name and deserves to know it is taken.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"an agent with the external key {external_key!r} already exists",
        )

    agent = Agent(
        tenant_id=ctx.tenant_id,
        external_key=external_key,
        name=name,
        description=template.summary,
        agent_type="voice",
        status="draft",
        current_draft_config=config,
        created_by_user_id=getattr(ctx, "user_id", None),
        updated_by_user_id=getattr(ctx, "user_id", None),
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)

    return InstantiatedAgent(
        id=str(agent.id),
        name=agent.name,
        status=agent.status,
        template_id=template.id,
        external_key=agent.external_key,
    )
