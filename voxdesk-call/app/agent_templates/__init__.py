"""Agent starter templates (PART 18, P18-01).

The catalog is data: seven YAML files under ``templates/``. This package is the
only supported way to read them, so every caller gets the same validation.
"""

from app.agent_templates.catalog import (
    REQUIRED_TEMPLATE_IDS,
    AgentTemplate,
    SimulationCase,
    TemplateError,
    VariableSpec,
    all_templates,
    get_template,
    load_template,
    reload_templates,
)

__all__ = [
    "REQUIRED_TEMPLATE_IDS",
    "AgentTemplate",
    "SimulationCase",
    "TemplateError",
    "VariableSpec",
    "all_templates",
    "get_template",
    "load_template",
    "reload_templates",
]
