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
TF_MODULES := alb assets eks failover network observability rds redis

.PHONY: batch07-check batch07-wiring batch07-mount batch07-schema batch07-scheduler \
        batch07-tests batch07-live batch07-infra batch07-sdk batch07-console \
        batch07-matrix batch07-patch batch07-help

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
	@test "$$(alembic heads | wc -l)" -eq 1 || { echo "expected exactly one alembic head" >&2; alembic heads >&2; exit 1; }
	$(PYTHON) -m pytest tests/test_alembic_models_in_sync.py tests/platform_v2/test_migration_0068.py -q

# --- Scheduler -----------------------------------------------------------------
# tests/test_scheduler_wires_all_v2_jobs.py asserts every JobType has a handler and
# every LOOP_NAMES entry has a locked tick; the helm CronJobs are checked separately
# in batch07-infra because a cycle with no CronJob is a silent no-op in production.
batch07-scheduler:
	$(PYTHON) -m pytest tests/test_scheduler_wires_all_v2_jobs.py -q

# --- The batch's own suites ----------------------------------------------------
batch07-tests: batch07-mount batch07-schema batch07-scheduler
	@mkdir -p $(BATCH07_JUNIT_DIR)
	$(PYTHON) -m pytest $(BATCH07_TESTS) -q -m "not live" \
		--junitxml=$(BATCH07_JUNIT_DIR)/junit.xml
	$(PYTHON) -m pytest tests/truth -q

# --- Opt-in live checks (never part of batch07-check) --------------------------
# Both modules skip, naming the missing variable, so this target is safe to run
# anywhere; it only proves something where credentials exist.
batch07-live:
	$(PYTHON) -m pytest $(BATCH07_TESTS)/test_live_custom_domain_tls.py \
		$(BATCH07_TESTS)/test_live_push_delivery.py -q -m live

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
	caddy validate --config infra/caddy/edge-global.caddy --adapter caddyfile
	caddy validate --config infra/caddy/tenant-domains.caddy --adapter caddyfile

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
