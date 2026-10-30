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
include Makefile.batch01.mk
# Batch 06: wiring, mount and cross-language regression gates.
include Makefile.batch06.mk
# Batch 07: platform-v2, infrastructure, SDK and admin-console gates.
include Makefile.batch07.mk
