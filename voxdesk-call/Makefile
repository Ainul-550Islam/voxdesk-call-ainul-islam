.PHONY: help up down logs migrate seed test lint deps-check contracts-check api-docs sbom due-diligence-pack verify-truth verify-sale check-backend check-frontend check-docker check-other check-all test-backend-fast test-backend-full test-migrations dev worker clean

FEATURE_EVIDENCE_TESTS := $(shell python3 -c "import yaml; d=yaml.safe_load(open('tests/truth/feature_manifest.yaml')); print(' '.join(sorted({t.split('::')[0] for f in d['features'] for t in f.get('evidence_tests', [])})))")

help:
	@echo "make up                 - start postgres + api + scheduler"
	@echo "make migrate            - apply database migrations"
	@echo "make seed               - create a demo tenant"
	@echo "make test               - run the test suite"
	@echo "make lint               - ruff check"
	@echo "make deps-check         - verify every pinned dependency imports + version-matches"
	@echo "make contracts-check    - compile protobuf contracts + cross-check enums/tenancy"
	@echo "make api-docs           - build static OpenAPI reference in docs/api/"
	@echo "make due-diligence-pack - generate SBOMs, third-party licenses, and dist/due-diligence-<date>.zip"
	@echo "make verify-truth       - run repository truth guards and tests/truth suite"
	@echo "make verify-sale        - full Gate G9 release gate (lint + typecheck + backend/frontend/go/cargo/c++ tests + truth + contracts + SBOM + matrix + due-diligence pack)"
	@echo "make check-backend      - run full backend check & test pipeline (scripts/check_backend.sh)"
	@echo "make check-frontend     - run full frontend check & test pipeline (scripts/check_frontend.sh)"
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
	@echo "VoxDesk is running on http://localhost:8000/docs"

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

verify-truth:
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
