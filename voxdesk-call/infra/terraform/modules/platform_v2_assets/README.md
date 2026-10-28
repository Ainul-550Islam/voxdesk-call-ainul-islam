# Platform-v2 brand-asset workload identity

Creates one EKS IRSA role bound to the exact API Kubernetes service account. The
role can read, write, and delete objects only below `tenants/*` in the existing
encrypted brand-assets bucket; it can use the bucket's KMS key only through S3 in
the bucket's region. It receives no bucket-list, public-access, or unrelated AWS
permissions.

The environment root passes `role_arn`, `service_account_name`, `bucket_name`,
`bucket_region`, and `kms_key_arn` to the Helm values output. The chart creates
that service account with the `eks.amazonaws.com/role-arn` annotation and sets
`BRANDING_ASSET_S3_BUCKET`, `BRANDING_ASSET_S3_REGION`, and
`BRANDING_ASSET_S3_KMS_KEY_ID` for the API deployment.

In the production environment, instantiate this module once per EKS OIDC issuer;
both roles may use the primary region's encrypted assets bucket so regional API
pods retain the same tenant asset paths during failover.
