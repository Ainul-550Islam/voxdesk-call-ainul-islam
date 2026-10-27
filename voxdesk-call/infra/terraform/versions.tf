# Batch 07 — AWS reference deployment.
#
# Deliberately pinned: an unpinned reference deployment is not a reference, it is
# a moving target that nobody can reproduce a year later.
terraform {
  required_version = ">= 1.6.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}
