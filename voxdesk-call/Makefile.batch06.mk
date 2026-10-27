# Makefile.batch06.mk — wiring, mount and regression gates for the Batch 06 repair.
#
# Batch 02–05 shipped code that was complete but never called: 23 route modules
# had no `include_router`, the migration chain was not verified against the
# models, and the v2 job handlers were never registered. Every target here is a
# *guard*: it fails when one of those links is broken again, not when a feature
# is merely absent.
#
# Included by the root Makefile:
#     include Makefile.batch06.mk

PYTHON ?= python3
BATCH06_JUNIT_DIR ?= reports/check/batch06
BATCH06_API := app/main.py app/core/errors.py app/core/tick_lock.py

.PHONY: batch06-check batch06-wiring batch06-mount batch06-schema batch06-scheduler \
        batch06-errors batch06-tests batch06-contracts batch06-patch

batch06-check: batch06-wiring batch06-mount batch06-schema batch06-scheduler batch06-errors batch06-tests

# --- Static wiring: every route module reachable, every v2 job called --------
batch06-wiring:
	$(PYTHON) scripts/verify_no_dead_routers.py
	$(PYTHON) scripts/verify_wiring.py

# --- Live mount: the app imports in a fresh interpreter and the surfaces are
#     actually on the route table, in an order where no literal path is
#     swallowed by an earlier wildcard.
batch06-mount:
	$(PYTHON) -m pytest tests/test_boot_and_mount.py -q

# --- Schema: one Alembic head, models and migrations agree on v2 tables -----
batch06-schema:
	$(PYTHON) -m pytest tests/test_alembic_models_in_sync.py tests/db -q

# --- Scheduler: every v2 tick is registered, locked and wired to a handler --
batch06-scheduler:
	$(PYTHON) -m pytest tests/test_scheduler_wires_all_v2_jobs.py -q

# --- Errors: a bad path value is a 4xx with a request id, never a bare 500 --
batch06-errors:
	$(PYTHON) -m pytest tests/test_no_500_on_bad_path_params.py tests/test_error_handling.py -q

# --- The batch's own regression suites -------------------------------------
batch06-tests: batch06-mount batch06-schema batch06-scheduler batch06-errors
	@mkdir -p $(BATCH06_JUNIT_DIR)
	$(PYTHON) -m pytest tests/contact_center_v2 tests/compliance_v2 tests/integrations_v2 \
		tests/security_v2 tests/api tests/db -m "not live" -q \
		--junitxml=$(BATCH06_JUNIT_DIR)/junit.xml
	$(PYTHON) -m pytest tests/test_no_undefined_enum_members.py -q
	$(PYTHON) -m pytest tests/truth -q

# --- Contracts: the shipped OpenAPI document is the live schema -------------
batch06-contracts:
	$(PYTHON) scripts/record_route_snapshot.py --check
	bash scripts/build_api_docs.sh
	$(PYTHON) -m pytest tests/test_openapi_contract_matches_live_app.py -q

# --- The change bundle: base patch + full copies of every changed file ------
batch06-patch:
	@test -f dist/voxdesk-batch06-patch.zip || { \
		echo "dist/voxdesk-batch06-patch.zip is missing; rebuild it with the batch tooling" >&2; \
		exit 1; \
	}
	@cd dist && sha256sum -c SHA256SUMS.batch06
