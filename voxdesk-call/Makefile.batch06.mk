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

.PHONY: batch06-check verify-wiring boot-check openapi-regen \
        go-test-all cargo-test-all frontend-test-all

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
