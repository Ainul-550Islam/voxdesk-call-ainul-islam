# AWS reference deployment (Batch 07, block A)

This directory is a **reference**, not a production story we have run for a
customer. Nothing here has been applied to a real account from this repository:
the tests in `tests/platform_v2/test_terraform_layout.py` run `terraform fmt -check`
and `terraform validate` (no credentials, no plan), and `terraform plan` is left to
the pipeline that has credentials. No output in this batch claims an `apply`.

## Layout

```
infra/terraform/
├── modules/
│   ├── network/       VPC, 3 AZs, NAT per AZ, S3/DynamoDB gateway endpoints, flow logs
│   ├── eks/           EKS 1.30, managed node group, IRSA OIDC provider, control-plane logs
│   ├── rds/           Aurora PostgreSQL 16, KMS-encrypted, RDS-managed master password
│   ├── redis/         ElastiCache Redis 7.1, AUTH token, at-rest + in-transit encryption
│   ├── alb/           ALB + ACM + Route53 + WAFv2 (managed rules + per-IP rate limit)
│   ├── assets/        S3 brand-asset bucket, SSE-KMS, versioning, tenant-prefixed policy
│   ├── observability/ SNS alarms + CloudWatch dashboard
│   └── failover/      Route53 health checks and PRIMARY/SECONDARY records
└── envs/
    ├── dev/           thin, single region, one NAT gateway
    ├── staging/       3 AZs, two Aurora instances, deletion protection
    └── prod/          two regions, failover DNS, latency records for api.<domain>
```

## Why the shape is what it is

* **NAT per AZ in staging and prod.** A single NAT gateway turns an AZ failure into
  an egress outage. `single_nat_gateway = true` exists for dev only, where the
  saving is worth more than the availability.
* **`manage_master_user_password = true`.** No database password is ever written
  to a plan file, a CI variable or a `.tfvars`. RDS generates it and keeps it in
  Secrets Manager; the application reads it through IRSA.
* **Brand assets never leave AWS.** The S3 gateway endpoint plus the
  `s3:prefix` deny in `modules/assets` means a presigned URL cannot be used to
  read another tenant's prefix.
* **Failover DNS is failover-routed, not latency-routed.** The API hostname
  (`app.<domain>`) has one live record at a time, gated by a per-region health
  check on `/health`. A latency record would split writes across two Aurora
  clusters; `api.<domain>` (read-only surfaces) does use latency routing, which is
  harmless.
* **TTL is 60s and we say so.** That is the DNS TTL; resolvers and clients cache
  longer, so the real recovery-time objective is documented in
  `docs/PLATFORM_V2_RUNBOOK.md` rather than implied by this number.

## Using it

```bash
cd infra/terraform/envs/prod
cp terraform.tfvars.example terraform.tfvars      # fill in, never commit
cp backend.hcl.example backend.hcl                # fill in, never commit
export TF_VAR_redis_auth_token=...               # from the secret store
terraform init -backend-config=backend.hcl
terraform plan -out plan.tfplan
terraform apply plan.tfplan                      # requires an operator with creds
```

`terraform.tfvars` and `backend.hcl` are git-ignored: the `.example` files are the
contract, the real ones carry account ids and are per-environment secrets.

## What the pipeline does

`.github/workflows/terraform.yml` runs, on every pull request that touches this
directory:

1. `terraform fmt -check -recursive`
2. `terraform init -backend=false` and `terraform validate` for every module and env
3. `terraform plan` **only** on `main`, with credentials from OIDC, and it posts the
   plan as a workflow step summary. It never applies.

The apply is a human step with a saved plan. A reference deployment that applies
itself is a reference deployment that deletes a database at 3am.
