locals {
  tags = merge(var.tags, {
    "voxdesk:component" = "platform-v2-assets"
    "voxdesk:name"      = var.name
  })

  oidc_issuer = replace(var.oidc_issuer_url, "https://", "")
}

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer}:sub"
      values   = ["system:serviceaccount:${var.kubernetes_namespace}:${var.service_account_name}"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = local.tags
}

data "aws_iam_policy_document" "assets" {
  statement {
    sid    = "TenantBrandAssetObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${var.bucket_arn}/tenants/*"]
  }

  statement {
    sid    = "BrandAssetEncryptionKey"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:GenerateDataKey",
    ]
    resources = [var.kms_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.bucket_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "assets" {
  name   = "${var.name}-brand-assets"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.assets.json
}
