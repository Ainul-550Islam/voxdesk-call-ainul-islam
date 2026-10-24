# Batch 02 — live carrier media and speech-to-speech.
#
# Included from the root Makefile by apply_batch02.sh. Every target here is
# additive: nothing in the existing Makefile is replaced, and a target that
# needs a credential says so instead of pretending to work without one.

.PHONY: sip-gateway-build sip-gateway-test sip-gateway-run s2s-smoke-live carriers-live-test carriers-capabilities

SIP_GATEWAY_DIR ?= services/sip-gateway
GO ?= go
GO_ENV ?= GOTOOLCHAIN=auto

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
