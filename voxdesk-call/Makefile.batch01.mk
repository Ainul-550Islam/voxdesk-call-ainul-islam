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

.PHONY: verify-hygiene clean-checkout-proof live-proof loadtest-gateway batch01-help

batch01-help:
	@echo "make verify-hygiene       - zero-byte scan + dead-router scan + docs/code number match"
	@echo "make clean-checkout-proof - fresh clone -> venv -> migrate -> offline pytest, evidence JSON"
	@echo "make live-proof           - real PSTN probe against the configured test number (needs provider keys)"
	@echo "make loadtest-gateway     - ramp real WebSocket sessions against the Go realtime gateway"

verify-hygiene:
	$(PYTHON) scripts/find_zero_byte_files.py --check
	$(PYTHON) scripts/verify_no_dead_routers.py
	$(PYTHON) scripts/verify_docs_match_code.py

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
