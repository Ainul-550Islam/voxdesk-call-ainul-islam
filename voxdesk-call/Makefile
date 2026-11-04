.PHONY: help up down logs migrate seed test lint deps-check contracts-check api-docs sbom due-diligence-pack verify-truth verify-fe-be verify-sale check-backend check-frontend check-docker check-other check-all test-backend-fast test-backend-full test-migrations dev worker clean \
        staging-proof live-evidence polyglot-evidence security-evidence verify-deploy-assets verify-artifacts \
        frontend-install frontend-test frontend-typecheck frontend-build frontend-check compose-config

FEATURE_EVIDENCE_TESTS := $(shell python3 -c "import yaml; d=yaml.safe_load(open('tests/truth/feature_manifest.yaml')); print(' '.join(sorted({t.split('::')[0] for f in d['features'] for t in f.get('evidence_tests', [])})))")

help:
	@echo "make up                 - start the full local development stack"
	@echo "make migrate            - apply database migrations"
	@echo "make seed               - create a demo tenant"
	@echo "make test               - run the test suite"
	@echo "make lint               - ruff check"
	@echo "make deps-check         - verify every pinned dependency imports + version-matches"
	@echo "make contracts-check    - compile protobuf contracts + cross-check enums/tenancy"
	@echo "make api-docs           - build static OpenAPI reference in docs/api/"
	@echo "make due-diligence-pack - generate SBOMs, third-party licenses, and dist/due-diligence-<date>.zip"
	@echo "make verify-truth       - run repository truth guards and tests/truth suite"
	@echo "make verify-fe-be       - check every dashboard API call against the live FastAPI routes"
	@echo "make staging-proof      - real deploy proof: compose up, migrate, smoke, backup->restore drill, drain, down"
	@echo "make live-evidence      - opt-in cost-guarded live provider/carrier/S2S/KMS/SIP evidence -> evidence/live/"
	@echo "make polyglot-evidence  - parse Go/Rust/C++ CI artifacts -> reports/POLYGLOT_EVIDENCE_SUMMARY.json"
	@echo "make security-evidence  - collect scanner outputs -> reports/SECURITY_EVIDENCE_SUMMARY.json"
	@echo "make verify-deploy-assets - validate Helm/Terraform/Caddy/compose deploy assets (never applies)"
	@echo "make verify-artifacts   - fail if a tracked *.log, *.zip or >1MB file is outside the allowlist"
	@echo "make verify-sale        - full Gate G9 release gate (lint + typecheck + backend/frontend/go/cargo/c++ tests + truth + contracts + SBOM + matrix + due-diligence pack)"
	@echo "make check-backend      - run full backend check & test pipeline (scripts/check_backend.sh)"
	@echo "make check-frontend     - run full frontend check & test pipeline (scripts/check_frontend.sh)"
	@echo "make frontend-check     - typecheck, test, and build both React frontends"
	@echo "make compose-config     - validate the development Compose model"
	@echo "make check-docker       - run full Docker stack check & smoke pipeline (scripts/check_docker.sh)"
	@echo "make check-other        - run polyglot, contracts, truth, security & due-diligence checks (scripts/check_other.sh)"
	@echo "make check-all          - run all 4 check pipelines and generate reports/check/SUMMARY.md (scripts/check_all.sh)"
	@echo "make test-backend-fast  - run boot probe + smoke + truth tests"
	@echo "make test-backend-full  - run chunked pytest suite across all test files"
	@echo "make test-migrations    - verify single Alembic head + upgrade/downgrade/upgrade round-trip"
	@echo "make dev                - run the api locally (no docker)"
	@echo "make down               - stop everything"

up:
	docker compose up -d --build
	@echo "waiting for the database..."
	@sleep 5
	docker compose exec -T api alembic upgrade head
	@echo "VoxDesk is running on http://localhost:8080 (API docs: http://localhost:8080/docs)"

down:
	docker compose down

logs:
	docker compose logs -f api scheduler

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

seed:
	python3 -m scripts.seed_demo_tenant

test:
	python3 -m pytest -q

lint:
	ruff check app tests scripts loadtest

deps-check:
	python3 scripts/verify_dependencies.py

contracts-check:
	python3 scripts/verify_contracts.py

api-docs:
	bash scripts/build_api_docs.sh

due-diligence-pack:
	python3 scripts/make_due_diligence_pack.py

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	python3 -m scripts.scheduler

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

verify-fe-be:
	python3 scripts/verify_frontend_backend_paths.py

# --- Evidence producers ------------------------------------------------------
# Each of these runs a real thing and writes a real file. None of them prints a
# success line for a run that did not happen: a missing tool is BLOCKED or a
# hard failure, never a silently-empty result.

# Deploy proof: bring the check stack up, migrate, smoke every surface, drill
# the backup -> restore path, prove the API drains on SIGTERM, tear it down.
# Writes evidence/staging/$(DATE)/staging_proof.json + raw logs.
staging-proof:
	python3 scripts/staging_certify.py --proof $(if $(EVIDENCE_DIR),--evidence-dir $(EVIDENCE_DIR),) $(if $(SKIP_TEARDOWN),--skip-teardown,)

# Opt-in live provider/carrier/S2S/KMS/SIP evidence. Skips with a named reason
# when the credentials for a check are absent; never fakes a pass.
live-evidence:
	@mkdir -p evidence/live
	python3 scripts/live_evidence_run.py --all --evidence-dir evidence/live

# Parse the Go/Rust/C++ artifacts produced in CI into one summary.
polyglot-evidence:
	@mkdir -p reports
	python3 scripts/polyglot_evidence.py --artifacts-dir artifacts/polyglot --output reports/POLYGLOT_EVIDENCE_SUMMARY.json

# Collect Trivy/Semgrep/gitleaks/npm-audit/hadolint/checkov outputs.
security-evidence:
	@mkdir -p reports
	python3 scripts/security_evidence.py --input-dir artifacts/security --output reports/SECURITY_EVIDENCE_SUMMARY.json

# Helm lint/template/kubeconform, terraform fmt/validate/tflint, caddy validate,
# compose config: validate the buyer-facing deploy assets, never apply them.
verify-deploy-assets:
	bash scripts/verify_deploy_assets.sh

# No tracked log, zip, or oversized blob outside the explicit allowlist.
verify-artifacts:
	python3 scripts/verify_no_tracked_artifacts.py

# verify-hygiene is declared in Makefile.batch01.mk (recipe lives there). This
# rule adds a prerequisite only, so artifact hygiene is part of the same gate
# without duplicating or overriding the recipe.
verify-hygiene: verify-artifacts

verify-truth: verify-fe-be
	python3 scripts/strip_generated_tails.py --check dashboard/src dashboard-next
	python3 scripts/verify_no_filler.py
	python3 scripts/verify_no_fake_success.py
	python3 scripts/verify_retired_references.py
	python3 scripts/verify_no_null_bytes.py
	python3 scripts/strip_padding_markers.py --check
	test ! -s scripts/fake_success_allowlist.txt
	# Tracked environment files must never carry generated secrets
	# (enforced in depth by tests/truth/test_no_tracked_secrets.py).
	test -z "$$(git ls-files -- '.env' '.env.*' ':!*.example')"
	python3 -m pytest tests/truth -q

check-backend:
	bash scripts/check_backend.sh

check-frontend:
	bash scripts/check_frontend.sh

frontend-install:
	cd dashboard && npm ci --no-audit --no-fund
	cd dashboard-next && npm ci --no-audit --no-fund

frontend-test:
	cd dashboard && npm test
	cd dashboard-next && npm test

frontend-typecheck:
	cd dashboard && npm run typecheck
	cd dashboard-next && npm run typecheck

frontend-build:
	cd dashboard && npm run build
	cd dashboard-next && npm run build

frontend-check: frontend-typecheck frontend-test frontend-build

compose-config:
	docker compose -f docker-compose.yml config -q

check-docker:
	bash scripts/check_docker.sh

check-other:
	bash scripts/check_other.sh

check-all:
	bash scripts/check_all.sh

test-backend-fast:
	python3 scripts/boot_probe.py --timeout 30
	python3 -m pytest tests/smoke/test_app_boot.py tests/truth -q

test-backend-full:
	python3 scripts/pytest_chunks.py --chunk-size 25 --per-chunk-timeout 180 --marker "not real_provider and not live" --junit-dir reports/check/chunks --summary reports/check/pytest_summary.json

test-migrations:
	alembic heads
	alembic upgrade head
	alembic downgrade base
	alembic upgrade head
	python3 scripts/migrate.py

verify-sale:
	$(MAKE) verify-truth
	$(MAKE) lint
	$(MAKE) deps-check
	$(MAKE) contracts-check
	$(MAKE) api-docs
	mkdir -p evidence/junit
	TZ=UTC python3 -m pytest $(FEATURE_EVIDENCE_TESTS) --junitxml=evidence/junit/feature_evidence.xml -q
	python3 scripts/generate_feature_matrix.py
	cd dashboard && (test -d node_modules || npm ci --silent) && npx vitest run
	cd dashboard-next && (test -d node_modules || npm ci --silent) && npx tsc --noEmit && npx vitest run
	cd services/realtime/gateway-go && GOPATH=/tmp/gopath GOCACHE=/tmp/gocache GOTOOLCHAIN=auto go test -race ./...
	cd services/signal-go && GOPATH=/tmp/gopath GOCACHE=/tmp/gocache GOTOOLCHAIN=auto go test -race ./...
	cd services/ops && GOPATH=/tmp/gopath GOCACHE=/tmp/gocache GOTOOLCHAIN=auto go test -race ./...
	CARGO_HOME=/tmp/cargo PATH="/tmp/cargo/bin:$$HOME/.cargo/bin:$$PATH" cargo test --manifest-path services/control-plane/Cargo.toml --workspace
	CARGO_HOME=/tmp/cargo PATH="/tmp/cargo/bin:$$HOME/.cargo/bin:$$PATH" cargo test --manifest-path services/realtime/media-engine-rs/Cargo.toml --workspace
	$(MAKE) -C services/media-plane test
	python3 scripts/make_due_diligence_pack.py
	test ! -s scripts/fake_success_allowlist.txt
	ls -lh dist/due-diligence-*.zip dist/SHA256SUMS

# Batch 01: hygiene guards, live proof and gateway load targets.
# Batch 06: wiring, mount and cross-language regression gates.
# Batch 07: platform-v2, infrastructure, SDK and admin-console gates.

# ---------------------------------------------------------------------------
# Batch 01-07 targets.
#
# These used to live in Makefile.batch01.mk ... Makefile.batch07.mk, three of
# which the file above pulled in with `include`. The rules are inlined here so a
# checkout has a single, self-describing build file and the batch fragments can
# be deleted. A rule whose target the main file already defined was dropped
# during the merge (verify-hygiene), so no recipe is defined twice.
# ---------------------------------------------------------------------------

.PHONY: agent-desktop-build agent-desktop-test authz-matrix batch01-help batch04-backend-check batch04-check batch04-dashboard-check batch04-helm-check batch04-sdk-check batch06-check batch07-check batch07-mount batch07-scheduler batch07-schema batch07-wiring boot-check carriers-capabilities carriers-live-test cc2-dialer-sim cc2-test clean-checkout-proof evidence-run int2-live-test int2-matrix int2-test int2-ui-build int2-ui-test kms-live-test live-proof loadtest-gateway openapi-regen pentest-regress residency-check s2s-smoke-live siem-test sip-gateway-build sip-gateway-run sip-gateway-test verify-hygiene verify-wiring

# Batch 02 — live carrier media and speech-to-speech.
#
# Included from the root Makefile by apply_batch02.sh. Every target here is
# additive: nothing in the existing Makefile is replaced, and a target that
# needs a credential says so instead of pretending to work without one.

help-batch02:
	@echo "make sip-gateway-build       - build the SIP media gateway binary"
	@echo "make sip-gateway-test        - run the SIP gateway unit tests with the race detector"
	@echo "make sip-gateway-run         - run the SIP gateway locally"
	@echo "make s2s-smoke-live          - live speech-to-speech smoke test (needs a provider key)"
	@echo "make carriers-live-test      - live carrier credential checks (needs carrier credentials)"
	@echo "make carriers-capabilities   - print the carrier capability matrix of this deployment"

sip-gateway-build:
	cd $(SIP_GATEWAY_DIR) && $(GO_ENV) $(GO) build -o /tmp/voxdesk-sip-gateway ./cmd/sip-gateway

sip-gateway-test:
	cd $(SIP_GATEWAY_DIR) && $(GO_ENV) $(GO) test -race ./...

sip-gateway-run:
	cd $(SIP_GATEWAY_DIR) && $(GO_ENV) $(GO) run ./cmd/sip-gateway

# The live checks refuse to run without the credentials they exercise. They
# exit non-zero when a credential is missing, so a pipeline cannot mistake
# "not configured" for "working".
s2s-smoke-live:
	@if [ -z "$${OPENAI_API_KEY:-}" ] && [ -z "$${GOOGLE_API_KEY:-}" ]; then \
		echo "s2s-smoke-live: set OPENAI_API_KEY or GOOGLE_API_KEY to run the live check" >&2; \
		exit 2; \
	fi
	python3 -m pytest tests/agent/test_live_s2s_openai.py -q -m live

carriers-live-test:
	@if [ -z "$${VONAGE_API_KEY:-}" ] && [ -z "$${SIP_GATEWAY_URL:-}" ]; then \
		echo "carriers-live-test: set VONAGE_API_KEY or SIP_GATEWAY_URL to run the live check" >&2; \
		exit 2; \
	fi
	python3 -m pytest tests/telephony/test_live_vonage_pstn.py tests/telephony/test_live_sip_trunk.py -q -m live

carriers-capabilities:
	python3 -c "import json; from app.telephony.providers.vonage import vonage_media_capabilities; from app.telephony.sip.sip_capabilities import sip_media_capabilities; print(json.dumps({**vonage_media_capabilities(), **sip_media_capabilities()}, indent=2))"
# Batch 03 targets. Included from the repository Makefile by apply_batch03.sh.
#
# Nothing here duplicates an existing target: these are the compliance control
# plane's own gates. `make pentest-regress` is the one to run before shipping;
# it chains the matrix, the SSRF/header probes and the regression suites.

BATCH03_TESTS := tests/compliance_v2 tests/security_v2
BATCH03_SOURCES := app/compliance_v2 app/api/compliance_v2 app/api/compliance_v2_routes.py \
	app/db/compliance_v2_models.py security_harness scripts/compliance_evidence_run.py \
	scripts/verify_audit_bundle.py scripts/authz_matrix_cli.py scripts/check_pii_registry.py \
	alembic/versions/0065_compliance_v2.py

siem-test:
	python3 -m pytest tests/compliance_v2/test_siem_events_mapping.py \
		tests/compliance_v2/test_siem_dispatcher_cursor.py \
		tests/compliance_v2/test_sink_splunk_hec.py \
		tests/compliance_v2/test_sink_datadog.py \
		tests/compliance_v2/test_sink_elastic_bulk.py \
		tests/compliance_v2/test_sink_syslog_cef.py \
		tests/compliance_v2/test_sink_s3_jsonl.py -q

# ---------------------------------------------------------------- block C ---
# Real AWS/GCP/Azure; each test skips with the exact variable names when its
# credentials are absent, so this target is safe to run anywhere.
kms-live-test:
	python3 -m pytest tests/compliance_v2 -m live -q

evidence-run:
	python3 scripts/compliance_evidence_run.py --json evidence/compliance/evidence-summary.json

# ---------------------------------------------------------------- block F ---
authz-matrix:
	python3 scripts/authz_matrix_cli.py --fail-on-gap --json evidence/compliance/authz-matrix.json

pentest-regress: authz-matrix
	python3 -m security_harness.runner --app --strict --evidence-dir evidence/compliance/security
	python3 -m pytest $(BATCH03_TESTS) -m "not live" -q

# ---------------------------------------------------------------- block D ---
residency-check:
	python3 -m pytest tests/compliance_v2/test_residency_policy.py -q

# ------------------------------------------------------------- housekeeping ---
compliance-v2-test:
	python3 -m pytest $(BATCH03_TESTS) -q

compliance-v2-lint:
	python3 -m ruff check $(BATCH03_SOURCES)

compliance-v2-migrate:
	python3 -m alembic upgrade head

# Proves 0065 applies and reverses on its own, without replaying the whole
# chain: stamp the parent revision on an empty database, upgrade, downgrade.
compliance-v2-migration-check:
	@tmp=$$(mktemp -d); \
	DATABASE_URL="sqlite+aiosqlite:///$$tmp/mig.db" python3 -m alembic stamp 0064_carrier_s2s >/dev/null; \
	DATABASE_URL="sqlite+aiosqlite:///$$tmp/mig.db" python3 -m alembic upgrade 0065_compliance_v2 >/dev/null; \
	DATABASE_URL="sqlite+aiosqlite:///$$tmp/mig.db" python3 -m alembic downgrade 0064_carrier_s2s >/dev/null; \
	rm -rf $$tmp; \
	echo "0065_compliance_v2 upgrades and downgrades cleanly"

# Writes a hashed report under evidence/security/. Exit codes: 0 clean,
# 1 a FAIL, 2 a REVIEW under --strict. A SKIP is neither.
compliance-v2-harness:
	python3 -m security_harness.runner --app --evidence-dir evidence/security

compliance-v2-verify: compliance-v2-lint compliance-v2-test compliance-v2-migration-check authz-matrix
	python3 scripts/check_pii_registry.py --json evidence/compliance/pii-registry.json
	python3 scripts/verify_contracts.py --skip-compile
	python3 -m pytest tests/truth -q
# Batch 04 validation entry points. Included by the root Makefile marker.
BATCH04_API := app/api/ws app/api/dialer_v2_routes.py app/api/softphone_routes.py app/api/agent_assist_routes.py app/api/acd_routes.py app/api/agent_states_v2_routes.py app/api/supervisor_v2_routes.py
batch04-check: batch04-backend-check batch04-helm-check batch04-sdk-check batch04-dashboard-check

batch04-backend-check: cc2-test cc2-dialer-sim
	python3 -m compileall -q app/contact_center_v2 $(BATCH04_API) app/db/contact_center_v2_models.py
	python3 -m ruff check app tests scripts
	python3 scripts/route_inventory.py > /dev/null
	@if ! ls dist/due-diligence-*.zip >/dev/null 2>&1; then \
		python3 scripts/make_due_diligence_pack.py; \
	fi
	$(MAKE) verify-truth
	$(MAKE) contracts-check

cc2-test:
	python3 -m pytest -m "not live" tests/contact_center_v2 tests/contact_center -q

cc2-dialer-sim:
	python3 -m pytest tests/contact_center_v2/test_pacing_simulator.py -q

batch04-helm-check:
	@command -v helm >/dev/null || { echo "helm is required for Batch 04 chart validation" >&2; exit 1; }
	helm lint infra/helm/voxdesk
	helm template voxdesk infra/helm/voxdesk > /dev/null
	helm template voxdesk infra/helm/voxdesk --set contactCenterWorkers.enabled=true > /dev/null
	@helm template voxdesk infra/helm/voxdesk --set contactCenterWorkers.enabled=true --show-only templates/contact-center-workers.yaml | grep -q 'kind: Deployment'
	@helm template voxdesk infra/helm/voxdesk --set contactCenterWorkers.enabled=true | grep -q 'kind: PodDisruptionBudget'
	@! helm template voxdesk infra/helm/voxdesk --set contactCenterWorkers.enabled=true --show-only templates/contact-center-workers.yaml | grep -q 'kind: HorizontalPodAutoscaler'
	@! helm template voxdesk infra/helm/voxdesk --set contactCenterWorkers.enabled=true --set contactCenterWorkers.autoscaling.enabled=true > /dev/null 2>&1
	@! helm template voxdesk infra/helm/voxdesk --set contactCenterWorkers.enabled=true --set contactCenterWorkers.cron.enabled=true > /dev/null 2>&1
	@! helm template voxdesk infra/helm/voxdesk --set contactCenterWorkers.enabled=true --set scheduler.enabled=false > /dev/null 2>&1
	helm template voxdesk infra/helm/voxdesk \
		--set coturn.enabled=true \
		--set coturn.externalIP=203.0.113.10 \
		--set-string config.softphoneTwimlAppSid=AP00000000000000000000000000000000 \
		--set-string config.softphoneTwilioApiKeySid=SK00000000000000000000000000000000 > /dev/null
	helm template voxdesk infra/helm/voxdesk \
		--set coturn.enabled=true \
		--set coturn.externalIP=203.0.113.10 \
		--set coturn.tls.enabled=true \
		--set coturn.tls.secretName=voxdesk-coturn-tls \
		--set-string config.softphoneTwimlAppSid=AP00000000000000000000000000000000 \
		--set-string config.softphoneTwilioApiKeySid=SK00000000000000000000000000000000 > /dev/null

batch04-sdk-check:
	npm --prefix sdk/agent-softphone ci
	npm --prefix sdk/agent-softphone run typecheck
	npm --prefix sdk/agent-softphone run build
	npm --prefix sdk/agent-softphone test

agent-desktop-build:
	npm --prefix dashboard-next run build

agent-desktop-test:
	npm --prefix dashboard-next test
	cd dashboard-next && npm exec -- vitest run --config app/agent-desktop/vitest.config.ts

batch04-dashboard-check:
	npm --prefix dashboard-next ci
	$(MAKE) agent-desktop-test
	cd dashboard-next && npm exec -- tsc --noEmit
	$(MAKE) agent-desktop-build
# Makefile.batch05.mk — Integrations v2 verification targets (Batch 05).

PYTHON ?= python3
INT2_EVIDENCE_DIR ?= evidence/integrations_v2_live

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
# Makefile.batch01.mk — repo hygiene, clean-checkout proof, live proof, gateway load.
#
# Include it from the repository Makefile (apply_batch01.sh appends one line):
#
#     include Makefile.batch01.mk
#
# Every target either measures something real or exits non-zero. None of them
# print a success line for a run that did not happen.

PYTHON ?= python3
EVIDENCE_DIR ?= evidence
MEDIA_ENGINE_URL ?=
DATE_UTC := $(shell date -u +%Y-%m-%d)

batch01-help:
	@echo "make verify-hygiene       - zero-byte scan + dead-router scan + docs/code number match"
	@echo "make clean-checkout-proof - fresh clone -> venv -> migrate -> offline pytest, evidence JSON"
	@echo "make live-proof           - real PSTN probe against the configured test number (needs provider keys)"
	@echo "make loadtest-gateway     - ramp real WebSocket sessions against the Go realtime gateway"

clean-checkout-proof:
	bash scripts/make_clean_checkout_proof.sh

live-proof:
	@mkdir -p $(EVIDENCE_DIR)/live
	@test -n "$(LIVE_PROOF_TENANT_ID)" || \
		(echo "LIVE_PROOF_TENANT_ID is required: a live run must be attributable to a tenant" && exit 2)
	$(PYTHON) -m app.telephony.live_proof.pstn_probe \
		--tenant-id $(LIVE_PROOF_TENANT_ID) \
		--minutes $(or $(LIVE_PROOF_MINUTES),1) \
		--max-usd $(or $(LIVE_PROOF_MAX_USD),0.50) \
		--evidence-dir $(EVIDENCE_DIR)/live

loadtest-gateway:
	@mkdir -p $(EVIDENCE_DIR)/capacity
	@test -n "$(VOXDESK_LOADTEST_TENANT_ID)" || \
		(echo "VOXDESK_LOADTEST_TENANT_ID is required: the ramp mints a real access token" && exit 2)
	$(PYTHON) loadtest/gateway_ramp.py \
		--ws-target $(or $(GATEWAY_WS_TARGET),127.0.0.1:8790) \
		--gateway-url $(or $(GATEWAY_HTTP_URL),http://127.0.0.1:8790) \
		--engine-url "$(MEDIA_ENGINE_URL)" \
		--ramp $(or $(GATEWAY_RAMP_STEPS),10,25,50,100,250,500) \
		--step-seconds $(or $(GATEWAY_STEP_SECONDS),10) \
		--output $(EVIDENCE_DIR)/capacity/gateway_ramp_$(DATE_UTC).json
# Makefile.batch06.mk — wiring, mount and regression gates for the Batch 06 repair.
#
# Batch 02-05 shipped code that was complete but never called: 23 route modules
# had no `include_router`, the migration chain was not verified against the
# models, and the v2 job handlers were never registered. Every target here is a
# *guard*: it fails when one of those links is broken again, not when a feature
# is merely absent.
#
# Included by the root Makefile:
#     include Makefile.batch06.mk

PYTHON ?= python3
GO ?= go
CARGO ?= cargo
NPM ?= npm
NPX ?= npx

batch06-check: verify-wiring boot-check openapi-regen

# --- Static wiring: app boots, every route module reachable, no undefined enums,
#     every v2 tick locked, every v2 job type backed by a handler. ------------
verify-wiring:
	$(PYTHON) scripts/verify_wiring.py
	$(PYTHON) scripts/verify_no_dead_routers.py

# --- Live mount: clean-process import + the batch mount contract. -----------
boot-check:
	$(PYTHON) -c "import app.main"
	$(PYTHON) -m pytest tests/test_boot_and_mount.py tests/test_no_undefined_enum_members.py -q

# --- Contracts: regenerate the shipped OpenAPI document from the live app and
#     verify it is the schema the application serves. -------------------------
openapi-regen:
	bash scripts/build_api_docs.sh
	$(PYTHON) -m pytest tests/test_openapi_contract_matches_live_app.py -q

# --- Go services ------------------------------------------------------------
GO_MODULES := services/sip-gateway services/realtime/gateway-go services/signal-go services/ops
go-test-all:
	@set -e; for dir in $(GO_MODULES); do \
		echo "== go test -race ./... ($$dir) =="; \
		( cd $$dir && $(GO) test -race ./... ); \
	done

# --- Rust services ----------------------------------------------------------
CARGO_CRATES := services/realtime/media-engine-rs services/control-plane
cargo-test-all:
	@set -e; for dir in $(CARGO_CRATES); do \
		echo "== cargo test ($$dir) =="; \
		( cd $$dir && $(CARGO) test ); \
	done

# --- Frontend workspaces ----------------------------------------------------
FRONTEND_APPS := dashboard dashboard-next sdk/agent-softphone
frontend-test-all:
	@set -e; for dir in $(FRONTEND_APPS); do \
		echo "== frontend ($$dir) =="; \
		( cd $$dir && $(NPM) ci && $(NPM) run build && $(NPX) tsc --noEmit && $(NPX) vitest run ); \
	done
# Makefile.batch07.mk — gates for the Batch 07 platform surfaces.
#
# Batch 07 is the largest surface in the sequence: three new API areas (WFM,
# white-label, mobile), a new schema revision, nine scheduled cycles, three SDKs, a
# Next.js console section and an AWS reference deployment. Every target here is a
# *guard*: it fails when a link between those pieces is broken, not when a feature is
# merely absent, and none of them claims more than it checks —
#
#   * Terraform is formatted and validated, never applied;
#   * the Android and iOS SDKs are checked structurally, never compiled (no
#     toolchain in this environment) — the targets say so out loud;
#   * the DNS/ACME and FCM/APNs protocol code is tested against local fakes; the
#     live checks are opt-in and skip without credentials.
#
# Included by the root Makefile:
#     include Makefile.batch07.mk

PYTHON ?= python3
BATCH07_JUNIT_DIR ?= reports/check/batch07
BATCH07_TESTS := tests/platform_v2
BATCH07_INFRA := infra/terraform
TF_ENVS := dev staging prod
TF_MODULES := alb assets eks failover network observability platform_v2_assets rds redis

batch07-check: batch07-wiring batch07-mount batch07-schema batch07-scheduler batch07-tests \
               batch07-infra batch07-sdk batch07-console batch07-matrix

batch07-help:
	@echo "batch07 targets:"
	@echo "  batch07-wiring    no dead route modules; every v2 job/tick/permission wired"
	@echo "  batch07-mount     the app boots and the platform routes answer"
	@echo "  batch07-schema    one alembic head; models and 0068 agree"
	@echo "  batch07-scheduler every platform cycle registered, locked and scheduled"
	@echo "  batch07-tests     tests/platform_v2 offline (no live, no provider)"
	@echo "  batch07-live      opt-in: real DNS/TLS and real FCM/APNs (skips without env)"
	@echo "  batch07-infra     terraform fmt+validate (all modules/envs), helm lint, caddy validate"
	@echo "  batch07-sdk       react-native tsc+build+vitest; android/ios structural check"
	@echo "  batch07-console   dashboard-next tsc + vitest + next build"
	@echo "  batch07-matrix    FEATURE_MATRIX_VERIFIED.md is the generator's output, unchanged"

# --- Static wiring -------------------------------------------------------------
# verify_no_dead_routers: every route module is mounted (or allowlisted) and no
# filler markers stand in for code. verify_wiring: enums resolved, job handlers
# registered for every type, scheduler ticks locked, mounts reachable.
batch07-wiring:
	$(PYTHON) scripts/verify_no_dead_routers.py
	$(PYTHON) scripts/verify_wiring.py

# --- Live mount ----------------------------------------------------------------
# The app must import in a fresh interpreter with no optional credentials, and the
# shipped OpenAPI document must still be the live schema (the platform_v2 routes are
# part of it).
batch07-mount:
	$(PYTHON) -c "import app.main"
	$(PYTHON) -m pytest tests/test_boot_and_mount.py tests/test_openapi_contract_matches_live_app.py -q

# --- Schema --------------------------------------------------------------------
# One head, and the models the app imports must be the tables 0068 creates.
batch07-schema:
	@test "$$($(PYTHON) -m alembic heads | wc -l)" -eq 1 || { echo "expected exactly one alembic head" >&2; $(PYTHON) -m alembic heads >&2; exit 1; }
	$(PYTHON) -m pytest tests/test_alembic_models_in_sync.py tests/platform_v2/test_migration_0068.py -q

# --- Scheduler -----------------------------------------------------------------
# The scheduler and registry regressions assert every Batch 07 job type has a
# handler and every platform cycle has a locked tick; Helm CronJobs are checked
# separately in batch07-infra because an unscheduled pass is a production no-op.
batch07-scheduler:
	$(PYTHON) -m pytest tests/test_scheduler_wires_all_v2_jobs.py -q

# --- The batch's own suites ----------------------------------------------------
# The suites run for their results, not for the exit code alone: a marker that
# deselects everything still exits 0, and an empty JUnit file then reads as
# "0 passed" forever. So each JUnit document is required to contain at least one
# <testcase>, and the count is printed so a shrinking suite is visible.
batch07-tests: batch07-mount batch07-schema batch07-scheduler
	@mkdir -p $(BATCH07_JUNIT_DIR)
	$(PYTHON) -m pytest $(BATCH07_TESTS) -q -m "not live" \
		--junitxml=$(BATCH07_JUNIT_DIR)/junit.xml
	$(PYTHON) -m pytest tests/truth -q --junitxml=$(BATCH07_JUNIT_DIR)/truth.xml
	@for f in $(BATCH07_JUNIT_DIR)/junit.xml $(BATCH07_JUNIT_DIR)/truth.xml; do \
		test -s "$$f" || { echo "$$f is missing or empty" >&2; exit 1; }; \
		n=$$(grep -c "<testcase" "$$f"); \
		echo "$$f: $$n testcase(s)"; \
		test "$$n" -gt 0 || { echo "$$f recorded no test cases" >&2; exit 1; }; \
	done

# --- Opt-in live checks (never part of batch07-check) --------------------------
# Both modules skip, naming the missing variable, so this target is safe to run
# anywhere; it only proves something where credentials exist.
batch07-live:
	@mkdir -p $(BATCH07_JUNIT_DIR)
	$(PYTHON) -m pytest $(BATCH07_TESTS)/test_live_custom_domain_tls.py \
		$(BATCH07_TESTS)/test_live_push_delivery.py -q -m live \
		--junitxml=$(BATCH07_JUNIT_DIR)/live.xml

# --- Infrastructure ------------------------------------------------------------
# fmt -check and validate only: nothing here plans against state or applies.
batch07-infra:
	@command -v terraform >/dev/null || { echo "terraform not installed: skipping fmt/validate" >&2; exit 1; }
	terraform fmt -check -recursive $(BATCH07_INFRA)
	@for dir in $(addprefix $(BATCH07_INFRA)/modules/,$(TF_MODULES)) $(addprefix $(BATCH07_INFRA)/envs/,$(TF_ENVS)); do \
		echo "validate $$dir"; \
		( cd $$dir && terraform init -backend=false -input=false >/dev/null && terraform validate -no-color >/dev/null ) || exit 1; \
	done
	@command -v helm >/dev/null || { echo "helm not installed: skipping chart lint" >&2; exit 1; }
	helm lint infra/helm/voxdesk
	@helm template voxdesk infra/helm/voxdesk | grep -q "kind: CronJob" || { echo "chart renders no CronJobs" >&2; exit 1; }
	@command -v caddy >/dev/null || { echo "caddy not installed: skipping config check" >&2; exit 1; }
	@tmp=$$(mktemp infra/caddy/.batch07-validate.XXXXXX.caddy); \
		trap 'rm -f "$$tmp"' EXIT; \
		printf '{\n  admin off\n  import edge-global.caddy\n}\n\nimport tenant-domains.caddy\n\nhttp://localhost {\n  reverse_proxy api:8000\n}\n' > "$$tmp"; \
		caddy validate --config "$$tmp" --adapter caddyfile

# --- SDKs ----------------------------------------------------------------------
# react-native is a real build and a real test run. The native SDKs have no
# toolchain here, so the check is structural and says so: it validates the Gradle
# and SwiftPM manifests, the source shapes and the test sources, and never reports a
# compile that did not happen.
batch07-sdk:
	cd sdk/react-native && (test -d node_modules || npm ci --silent) && npx tsc --noEmit && npm run build --silent && npx vitest run --reporter dot
	$(PYTHON) scripts/verify_mobile_sdks.py
	$(PYTHON) -m pytest tests/platform_v2/test_sdk_contract_shapes.py -q

# --- Console -------------------------------------------------------------------
batch07-console:
	cd dashboard-next && (test -d node_modules || npm ci --silent) && npx tsc --noEmit && npx vitest run --reporter dot
	cd dashboard-next && npx next build >/dev/null

# --- Feature matrix ------------------------------------------------------------
# The matrix is generator output; hand-editing it is the failure this catches.
batch07-matrix:
	$(PYTHON) scripts/generate_feature_matrix.py --output /tmp/feature_matrix.batch07.md
	@diff -u docs/SALES/FEATURE_MATRIX_VERIFIED.md /tmp/feature_matrix.batch07.md \
		|| { echo "FEATURE_MATRIX_VERIFIED.md is not the generator's output" >&2; exit 1; }

# --- The change bundle ---------------------------------------------------------
batch07-patch:
	@test -f dist/voxdesk-batch07-patch.zip || { \
		echo "dist/voxdesk-batch07-patch.zip is missing; rebuild it with the batch tooling" >&2; \
		exit 1; \
	}
	@cd dist && sha256sum -c SHA256SUMS.batch07
