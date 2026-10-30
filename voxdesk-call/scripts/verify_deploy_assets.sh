#!/usr/bin/env bash
# =============================================================================
# Validate — never apply — every buyer-facing deploy asset.
#
#   helm lint + helm template (prod AND staging values) + kubeconform
#   terraform fmt -check + validate + tflint (+ checkov in CI)
#   caddy validate (both shipped Caddyfiles)
#   docker compose config for prod and staging
#
# The point is that a buyer who clones this repository and runs the commands in
# docs/BUYER_DEPLOY_GUIDE.md hits a chart, a module and a proxy config that have
# already been parsed by the same tools they will use. Nothing here plans,
# applies, pushes or builds an image: `--check`, `-validate` and `config` are
# all read-only.
#
# A missing tool is a hard failure with the install hint, not a skipped step —
# a validation that did not run cannot be reported as a validation that passed.
#
# Usage:
#   bash scripts/verify_deploy_assets.sh             # every check
#   bash scripts/verify_deploy_assets.sh --json out.json
#   bash scripts/verify_deploy_assets.sh --skip-tflint
# =============================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

JSON_OUT=""
SKIP_TFLINT=0
SKIP_KUBECONFORM=0
# Indexed loop rather than `for arg in "$@"`: `--json <path>` consumes the next
# argument, which a value loop cannot do without corrupting the iteration.
args=("$@")
i=0
while [ "$i" -lt "${#args[@]}" ]; do
  arg="${args[$i]}"
  case "$arg" in
    --json)
      i=$((i + 1))
      JSON_OUT="${args[$i]:-}"
      [ -z "$JSON_OUT" ] && { echo "--json needs a path" >&2; exit 2; }
      ;;
    --json=*) JSON_OUT="${arg#*=}" ;;
    --skip-tflint) SKIP_TFLINT=1 ;;
    --skip-kubeconform) SKIP_KUBECONFORM=1 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
  i=$((i + 1))
done

HELM_CHART="infra/helm/voxdesk"
HELM_VALUES_PROD="infra/helm/voxdesk/values-prod.yaml"
HELM_VALUES_STAGING="infra/helm/voxdesk/values-staging.yaml"
TERRAFORM_DIR="infra/terraform"
CADDY_FILES=(Caddyfile infra/caddy/edge-global.caddy infra/caddy/tenant-domains.caddy)
COMPOSE_FILES=(docker-compose.prod.yml docker-compose.staging.yml)

declare -a RESULTS=()
FAILED=0
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

have() { command -v "$1" >/dev/null 2>&1; }

version_of() {
  local tool="$1"
  case "$tool" in
    helm) helm version --short 2>/dev/null | head -1 ;;
    terraform) terraform version 2>/dev/null | head -1 ;;
    tflint) tflint --version 2>/dev/null | head -1 ;;
    kubeconform) kubeconform -v 2>/dev/null | head -1 ;;
    checkov) checkov --version 2>/dev/null | head -1 ;;
    caddy) caddy version 2>/dev/null | head -1 ;;
    docker) docker --version 2>/dev/null | head -1 ;;
    *) echo "" ;;
  esac
}

# record <name> <status> <detail>
record() {
  local name="$1" status="$2" detail="${3:-}"
  RESULTS+=("$(printf '{"check":"%s","status":"%s","detail":"%s"}' \
    "$name" "$status" "$(printf '%s' "$detail" | sed 's/"/\\"/g' | tr '\n' ' ' | cut -c1-300)")")
  printf '  [%s] %s — %s\n' "$status" "$name" "${detail:0:160}"
  [ "$status" = "FAIL" ] && FAILED=$((FAILED + 1))
  return 0
}

require_tool() {
  local tool="$1" hint="$2"
  if have "$tool"; then
    return 0
  fi
  record "tool:$tool" "FAIL" "not installed; $hint"
  return 1
}

echo "== deploy asset validation =="
echo "repository: $REPO_ROOT"
echo "started:    $STARTED_AT"

echo
echo "-- tool versions --"
for tool in helm terraform tflint kubeconform checkov caddy docker; do
  if have "$tool"; then
    printf '   %-12s %s\n' "$tool" "$(version_of "$tool")"
  else
    printf '   %-12s %s\n' "$tool" "(not installed)"
  fi
done

# -----------------------------------------------------------------------------
echo
echo "-- helm --"
if require_tool helm "install https://helm.sh/docs/intro/install/"; then
  for values in "$HELM_VALUES_PROD" "$HELM_VALUES_STAGING"; do
    short="$(basename "$values")"
    if [ ! -f "$values" ]; then
      record "helm:lint:$short" "FAIL" "values file missing: $values"
      continue
    fi
    if out=$(helm lint "$HELM_CHART" -f "$HELM_CHART/values.yaml" -f "$values" 2>&1); then
      record "helm:lint:$short" "PASS" "$(printf '%s' "$out" | tail -1)"
    else
      record "helm:lint:$short" "FAIL" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
    fi

    rendered="$(mktemp)"
    if out=$(helm template voxdesk "$HELM_CHART" -f "$HELM_CHART/values.yaml" -f "$values" 2>&1); then
      printf '%s\n' "$out" > "$rendered"
      kinds="$(grep -c '^kind:' "$rendered" || true)"
      record "helm:template:$short" "PASS" "rendered $kinds object(s)"
    else
      printf '%s\n' "$out" > "$rendered"
      record "helm:template:$short" "FAIL" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
    fi

    if [ "$SKIP_KUBECONFORM" -eq 0 ]; then
      if require_tool kubeconform "install https://github.com/yannh/kubeconform"; then
        if out=$(kubeconform -strict -summary -ignore-missing-schemas "$rendered" 2>&1); then
          record "kubeconform:$short" "PASS" "$(printf '%s' "$out" | tail -1)"
        else
          record "kubeconform:$short" "FAIL" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
        fi
      fi
    fi
    rm -f "$rendered"
  done
fi

# -----------------------------------------------------------------------------
echo
echo "-- terraform --"
if require_tool terraform "install https://developer.hashicorp.com/terraform/downloads"; then
  if out=$(terraform fmt -check -recursive -no-color "$TERRAFORM_DIR" 2>&1); then
    record "terraform:fmt" "PASS" "all files formatted"
  else
    record "terraform:fmt" "FAIL" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
  fi

  # Every module and every environment is initialised without a backend and
  # validated. `init -backend=false` touches no remote state.
  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    label="${dir#"$TERRAFORM_DIR"/}"
    if ! ( cd "$dir" && terraform init -backend=false -input=false -no-color >/dev/null 2>&1 ); then
      record "terraform:init:$label" "FAIL" "terraform init failed"
      continue
    fi
    if out=$(cd "$dir" && terraform validate -no-color 2>&1); then
      record "terraform:validate:$label" "PASS" "$(printf '%s' "$out" | tail -1)"
    else
      record "terraform:validate:$label" "FAIL" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
    fi
  done < <(find "$TERRAFORM_DIR" -name '*.tf' -printf '%h\n' 2>/dev/null | sort -u)

  if [ "$SKIP_TFLINT" -eq 0 ] && require_tool tflint "install https://github.com/terraform-linters/tflint"; then
    if out=$(tflint --recursive --chdir "$TERRAFORM_DIR" 2>&1); then
      record "tflint" "PASS" "no lint findings"
    else
      record "tflint" "FAIL" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
    fi
  fi
fi

# -----------------------------------------------------------------------------
echo
echo "-- caddy --"
if require_tool caddy "install https://caddyserver.com/docs/install"; then
  for caddyfile in "${CADDY_FILES[@]}"; do
    if [ ! -f "$caddyfile" ]; then
      record "caddy:validate:$caddyfile" "FAIL" "file missing"
      continue
    fi
    # edge-global.caddy and tenant-domains.caddy are *imports*, not standalone
    # configs, so each is validated through a wrapper that imports it.
    wrapper="$(mktemp /tmp/voxdesk-caddy-validate.XXXXXX.caddy)"
    if [[ "$caddyfile" == "infra/caddy/edge-global.caddy" ]]; then
      {
        echo "{"
        echo "  admin off"
        echo "  import $(basename "$caddyfile")"
        echo "}"
        echo
        echo "http://localhost {"
        echo "  reverse_proxy api:8000"
        echo "}"
      } > "$wrapper"
      cp "$wrapper" "$(dirname "$caddyfile")/.validate-$(basename "$wrapper")"
      wrapper_path="$(dirname "$caddyfile")/.validate-$(basename "$wrapper")"
    elif [[ "$caddyfile" == "infra/caddy/tenant-domains.caddy" ]]; then
      {
        echo "{"
        echo "  admin off"
        echo "  import edge-global.caddy"
        echo "}"
        echo
        echo "import $(basename "$caddyfile")"
      } > "$wrapper"
      cp "$wrapper" "$(dirname "$caddyfile")/.validate-$(basename "$wrapper")"
      wrapper_path="$(dirname "$caddyfile")/.validate-$(basename "$wrapper")"
    else
      cp "$caddyfile" "$wrapper"
      wrapper_path="$wrapper"
    fi
    if out=$(caddy validate --config "$wrapper_path" --adapter caddyfile 2>&1); then
      record "caddy:validate:$caddyfile" "PASS" "config is valid"
    else
      record "caddy:validate:$caddyfile" "FAIL" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
    fi
    rm -f "$wrapper"
    rm -f "$(dirname "$caddyfile")/.validate-"* 2>/dev/null || true
  done
fi

# -----------------------------------------------------------------------------
echo
echo "-- docker compose --"
if require_tool docker "install Docker Engine or Docker Desktop"; then
  for compose in "${COMPOSE_FILES[@]}"; do
    if [ ! -f "$compose" ]; then
      record "compose:config:$compose" "FAIL" "file missing"
      continue
    fi
    if out=$(docker compose -f "$compose" config -q 2>&1); then
      record "compose:config:$compose" "PASS" "config is valid"
    else
      record "compose:config:$compose" "FAIL" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
    fi
  done
fi

# -----------------------------------------------------------------------------
FINISHED_AT="$(date -u +%y-%m-%dT%H:%M:%SZ)"
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OVERALL="PASS"
[ "$FAILED" -gt 0 ] && OVERALL="FAIL"

echo
echo "== result: $OVERALL ($FAILED failing check(s)) =="

if [ -n "$JSON_OUT" ]; then
  {
    printf '{\n'
    printf '  "schema": "voxdesk.deploy_asset_validation/v1",\n'
    printf '  "generated_at": "%s",\n' "$FINISHED_AT"
    printf '  "started_at": "%s",\n' "$STARTED_AT"
    printf '  "checks_run": %d,\n' "${#RESULTS[@]}"
    printf '  "failed": %d,\n' "$FAILED"
    printf '  "overall": "%s",\n' "$OVERALL"
    printf '  "results": [\n'
    for i in "${!RESULTS[@]}"; do
      printf '    %s' "${RESULTS[$i]}"
      [ "$i" -lt $(( ${#RESULTS[@]} - 1 )) ] && printf ','
      printf '\n'
    done
    printf '  ]\n}\n'
  } > "$JSON_OUT"
  echo "wrote $JSON_OUT"
fi

exit $(( FAILED > 0 ? 1 : 0 ))
