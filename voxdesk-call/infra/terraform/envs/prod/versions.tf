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

# Two providers, one per region. Every regional module is instantiated twice with
# an explicit `providers = { aws = aws.<region> }`, so a resource can never land in
# the wrong region because of a module default.
provider "aws" {
  alias  = "primary"
  region = var.primary_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias  = "secondary"
  region = var.secondary_region

  default_tags {
    tags = local.common_tags
  }
}
