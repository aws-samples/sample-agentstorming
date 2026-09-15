# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.70" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = var.project
      Component   = "agentstorming"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# CloudFront-scoped WAFv2 web ACLs exist only in us-east-1, whatever region the
# rest of the stack is in — the same constraint as the CloudFront certificate.
provider "aws" {
  alias   = "us_east_1"
  region  = "us-east-1"
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = var.project
      Component   = "agentstorming"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# Cross-region replication destination. A replica bucket must be created by a
# provider in its own region.
provider "aws" {
  alias   = "replica"
  region  = var.replica_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = var.project
      Component   = "agentstorming"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
