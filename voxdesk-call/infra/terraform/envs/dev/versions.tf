terraform {
  required_version = ">= 1.6.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # `terraform init -backend-config=backend.hcl` for a real state bucket.
  # Nothing in this repository holds a state file.
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.common_tags
  }
}
