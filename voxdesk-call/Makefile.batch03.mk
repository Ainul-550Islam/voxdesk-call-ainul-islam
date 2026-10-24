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

.PHONY: siem-test kms-live-test evidence-run authz-matrix pentest-regress residency-check \
	compliance-v2-test compliance-v2-lint compliance-v2-migrate compliance-v2-migration-check \
	compliance-v2-harness compliance-v2-verify

# ---------------------------------------------------------------- block A ---
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
