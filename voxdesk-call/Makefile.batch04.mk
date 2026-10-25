# Batch 04 validation entry points. Included by the root Makefile marker.
BATCH04_API := app/api/ws app/api/dialer_v2_routes.py app/api/softphone_routes.py app/api/agent_assist_routes.py app/api/acd_routes.py app/api/agent_states_v2_routes.py app/api/supervisor_v2_routes.py
.PHONY: batch04-check batch04-backend-check batch04-sdk-check batch04-dashboard-check batch04-helm-check cc2-test cc2-dialer-sim agent-desktop-build agent-desktop-test

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
