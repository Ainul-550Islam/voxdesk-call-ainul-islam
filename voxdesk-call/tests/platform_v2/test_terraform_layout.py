"""Hermetic Terraform syntax and platform-v2 workload-identity wiring checks.

The HCL parser validates configuration locally; these tests do not initialise a
provider, read state, contact AWS, or claim that a Terraform plan was applied.
"""

from __future__ import annotations

from pathlib import Path

import hcl2

ROOT = Path(__file__).resolve().parents[2]
TERRAFORM = ROOT / "infra" / "terraform"


def _parse(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        parsed = hcl2.load(handle)
    assert isinstance(parsed, dict), path
    return parsed


def _modules(path: Path) -> dict[str, dict]:
    return {
        name: block
        for module_block in _parse(path).get("module", [])
        for name, block in module_block.items()
    }


def _outputs(path: Path) -> dict[str, dict]:
    return {
        name: block
        for output_block in _parse(path).get("output", [])
        for name, block in output_block.items()
    }


def test_batch07_terraform_files_parse_without_provider_or_state_access():
    touched_roots = (
        TERRAFORM / "modules" / "assets",
        TERRAFORM / "modules" / "platform_v2_assets",
        TERRAFORM / "envs" / "dev",
        TERRAFORM / "envs" / "staging",
        TERRAFORM / "envs" / "prod",
    )
    files = sorted(path for root in touched_roots for path in root.glob("*.tf"))
    assert files
    for path in files:
        _parse(path)


def test_every_environment_wires_eks_oidc_assets_and_helm_values():
    for environment in ("dev", "staging"):
        env_root = TERRAFORM / "envs" / environment
        modules = _modules(env_root / "main.tf")
        assert modules["platform_v2_assets"]["source"] == "../../modules/platform_v2_assets"
        assert modules["platform_v2_assets"]["oidc_provider_arn"] == "${module.eks.oidc_provider_arn}"
        assert modules["platform_v2_assets"]["oidc_issuer_url"] == "${module.eks.oidc_issuer_url}"
        assert modules["platform_v2_assets"]["bucket_arn"] == "${module.assets.bucket_arn}"
        assert "platform_assets_helm_values" in _outputs(env_root / "outputs.tf")

    production_modules = _modules(TERRAFORM / "envs" / "prod" / "main.tf")
    assert production_modules["platform_v2_assets_primary"]["source"] == "../../modules/platform_v2_assets"
    assert production_modules["platform_v2_assets_secondary"]["source"] == "../../modules/platform_v2_assets"
    assert production_modules["platform_v2_assets_primary"]["oidc_provider_arn"] == (
        "${module.primary_eks.oidc_provider_arn}"
    )
    assert production_modules["platform_v2_assets_secondary"]["oidc_provider_arn"] == (
        "${module.secondary_eks.oidc_provider_arn}"
    )
    assert production_modules["platform_v2_assets_secondary"]["bucket_region"] == "${var.primary_region}"
    production_outputs = _outputs(TERRAFORM / "envs" / "prod" / "outputs.tf")
    assert "primary_platform_assets_helm_values" in production_outputs
    assert "secondary_platform_assets_helm_values" in production_outputs


def test_platform_asset_role_is_restricted_to_tenant_objects_and_kms_via_s3():
    module_text = (TERRAFORM / "modules" / "platform_v2_assets" / "main.tf").read_text(
        encoding="utf-8"
    )
    assert '"s3:GetObject"' in module_text
    assert '"s3:PutObject"' in module_text
    assert '"s3:DeleteObject"' in module_text
    assert '"${var.bucket_arn}/tenants/*"' in module_text
    assert '"kms:ViaService"' in module_text
    assert '"s3.${var.bucket_region}.amazonaws.com"' in module_text
    assert '"s3:ListBucket"' not in module_text
    assert '"s3:*"' not in module_text


def test_asset_bucket_prefix_deny_uses_an_object_resource_boundary():
    bucket_policy = (TERRAFORM / "modules" / "assets" / "main.tf").read_text(
        encoding="utf-8"
    )
    assert 'not_resources = ["${aws_s3_bucket.this.arn}/tenants/*"]' in bucket_policy
    assert 'variable = "s3:prefix"' not in bucket_policy
