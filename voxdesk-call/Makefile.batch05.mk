# Makefile.batch05.mk — Integrations v2 verification targets (Batch 05).

PYTHON ?= python3
INT2_EVIDENCE_DIR ?= evidence/integrations_v2_live

.PHONY: int2-test int2-live-test int2-ui-build int2-ui-test int2-matrix

int2-test:
	$(PYTHON) -m pytest tests/integrations_v2 -m "not live" -q

int2-live-test:
	@mkdir -p $(INT2_EVIDENCE_DIR)
	$(PYTHON) -m pytest tests/integrations_v2 -m live --junitxml=$(INT2_EVIDENCE_DIR)/junit_live.xml -q

int2-ui-build:
	cd dashboard-next && npx tsc --noEmit && npm run build

int2-ui-test:
	cd dashboard-next && npx vitest run app/integrations-v2

int2-matrix:
	$(PYTHON) -c 'import json; from app.integrations_v2.register_all import PROVIDERS_V2, is_provider_env_configured; from app.integrations_v2.matrix import resolve_provider_verification; out=[cls.descriptor(env_configured=is_provider_env_configured(s), verification=resolve_provider_verification(s, conn_row=None, env_configured=is_provider_env_configured(s))[0]) for s, cls in PROVIDERS_V2.items()]; print(json.dumps(out, indent=2))'
