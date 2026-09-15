# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

variable "project" {
  type    = string
  default = "agentstorming"
}

variable "environment" {
  type    = string
  default = "production"
}

variable "region" {
  type    = string
  default = "us-east-2"
}

variable "aws_profile" {
  type    = string
  default = "default"
}

# Aurora Serverless v2. min_capacity MUST be >= 0.5 to keep LISTEN/NOTIFY
# active across idle periods; see deploy/cloud/aws/serverless/rds.tf.
variable "aurora_min_capacity" {
  type    = number
  default = 0.5
}

variable "aurora_max_capacity" {
  type    = number
  default = 4.0
}

variable "aurora_engine_version" {
  type    = string
  default = "16.4"
}

# Retained for backwards-compat with older terraform var files; unused.
variable "rds_instance_class" {
  type    = string
  default = "db.serverless"
}

variable "rds_allocated_gb" {
  type    = number
  default = 20
}

variable "rds_backup_retention_days" {
  type    = number
  default = 7
}

variable "fargate_cpu" {
  type    = number
  default = 2048
}

variable "fargate_memory" {
  type    = number
  default = 4096
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "vpc_cidr" {
  description = "CIDR for the VPC this stack creates. /16 gives room for the /20 subnets carved out of it."
  type        = string
  default     = "10.60.0.0/16"

  validation {
    condition     = can(cidrsubnet(var.vpc_cidr, 4, 15))
    error_message = "vpc_cidr must be a CIDR block with room for at least 16 /4-offset subnets, e.g. 10.60.0.0/16."
  }
}

variable "nat_gateway_per_az" {
  description = <<-DESC
    One NAT gateway per availability zone instead of a single shared one.
    A shared gateway is cheaper (one EIP, one hourly charge) but makes the
    second AZ's egress depend on the first AZ staying up. True for production.
  DESC
  type        = bool
  default     = false
}

# `admin_token` used to be declared here and injected into the task from
# Secrets Manager. The server never read it — see the note at the foot of
# rds.tf. Issue admin tokens with `agentstorming-admin create-admin-token`,
# which stores only a hash server-side.

variable "default_room" {
  type    = string
  default = "research"
}

# The base64url Ed25519 public key of the human owner / deployer.
# Seeded into owner_keys at first boot (idempotent) so the deployer
# can mint fresh invites via the signed-request flow from their laptop.
variable "owner_pubkey" {
  type        = string
  default     = ""
  description = "base64url Ed25519 public key of the human owner/deployer"
}

variable "owner_pubkey_label" {
  type    = string
  default = "terraform-bootstrap"
}

# ACM certificate for the ALB HTTPS listener. Leave empty to keep the
# TLS certificate for the HTTPS listener. REQUIRED.
#
# There is deliberately no plaintext fallback. Spec §8.1 requires TLS and
# says the server MUST reject plaintext HTTP; the previous `default = ""`
# quietly served the entire API — bearer tokens, signed envelopes, whispers —
# over port 80 whenever someone forgot to set this.
variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the ALB HTTPS listener, in this region."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:acm:", var.acm_certificate_arn))
    error_message = "acm_certificate_arn must be an ACM certificate ARN. This deployment does not support plaintext HTTP; request a certificate with `aws acm request-certificate`."
  }
}

variable "enable_deletion_protection" {
  description = "Deletion protection on the ALB and Aurora cluster. Default true; set false only for throwaway test stacks you intend to `terraform destroy`."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch retention for server logs. Room events are an audit transcript, so the default is a year."
  type        = number
  default     = 365
}

# `assign_task_public_ip` used to live here, defaulting to true, because the
# stack ran in the default VPC and tasks needed a public address to reach ECR
# and Bedrock. vpc.tf now provisions private subnets behind a NAT gateway, so
# tasks never need one and the variable would only be a way to reintroduce the
# problem. Removed rather than defaulted to false.

variable "geo_restriction_type" {
  description = <<-DESC
    CloudFront geo restriction mode: "blacklist", "whitelist" or "none".

    Defaults to "blacklist" over `geo_restriction_locations`. Rooms are
    deliberately multi-institution and international, so an allowlist would
    contradict the protocol's purpose; a denylist of comprehensively sanctioned
    jurisdictions is what AWS customers are expected to operate anyway.
  DESC
  type        = string
  default     = "blacklist"

  validation {
    condition     = contains(["blacklist", "whitelist", "none"], var.geo_restriction_type)
    error_message = "geo_restriction_type must be blacklist, whitelist or none."
  }
}

variable "geo_restriction_locations" {
  description = <<-DESC
    ISO 3166-1 alpha-2 country codes for `geo_restriction_type`. The default
    denies the four comprehensively sanctioned jurisdictions. This is a
    starting point and not legal advice — check your own compliance regime,
    which may require more, fewer, or different entries.
  DESC
  type        = list(string)
  default     = ["CU", "IR", "KP", "SY"]
}

variable "waf_rate_limit_per_5min" {
  description = <<-DESC
    Requests per five minutes from a single IP before the WAF blocks it. The
    protocol already rate-limits per-PID and per-IP in the application, but
    that costs a request to the server to enforce; the WAF drops the excess
    before it reaches Fargate. Generous by default because SSE reconnects and
    event posts from a busy room are legitimately chatty.
  DESC
  type        = number
  default     = 10000
}

# `enable_cross_region_replication` and `enable_waf` were declared here and
# have been removed. Both defaulted to on and both were implemented with
# `count`, which meant a graph-based policy scan could not see the resources
# they gated — a stack could be correct and unprovable at the same time. More
# to the point, the only thing either flag bought was a cheaper deployment that
# was quietly missing a control the documentation said it had. See the headers
# of replication.tf and waf.tf.

variable "replica_region" {
  description = "Destination region for cross-region replication. Must differ from `region`."
  type        = string
  default     = "us-west-2"
}

# Required, for the same reason acm_certificate_arn is. On the shared
# *.cloudfront.net certificate AWS pins the protocol and ignores
# minimum_protocol_version, so a TLS 1.2 floor cannot be enforced — the SPA and
# the API it talks to would be reachable over older TLS with no way to say no.
# An optional field here meant the insecure branch was the default one.
#
# This adds no new prerequisite in kind: the ALB listener already requires a
# certificate, so a deployment already owns a domain. Note the region though —
# CloudFront reads certificates only from us-east-1, whatever region the rest
# of the stack is in.
variable "cloudfront_certificate_arn" {
  description = "ACM certificate ARN for the CloudFront distribution. MUST be in us-east-1."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:acm:us-east-1:", var.cloudfront_certificate_arn))
    error_message = "cloudfront_certificate_arn must be an ACM certificate ARN in us-east-1 — CloudFront reads certificates only from that region. Request one with: aws acm request-certificate --region us-east-1"
  }
}

variable "cloudfront_aliases" {
  description = "Custom domain names for the distribution. Requires cloudfront_certificate_arn."
  type        = list(string)
  default     = []
}

# Declared here as well as in single-vm/. iam.tf has always referenced this
# variable and this stack never declared it, so `terraform validate` failed
# outright — the serverless sample could not be applied by anyone who tried.
# It went unnoticed because validate was only ever run against single-vm.
variable "allowed_bedrock_model_arns" {
  description = <<-DESC
    Bedrock model / inference-profile ARNs the server may invoke. Deliberately
    not "*": the server invokes models on behalf of participants, so its blast
    radius should be the models the deployment actually sanctions. Wildcards
    within a family are fine.
  DESC
  type        = list(string)
  default = [
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
    "arn:aws:bedrock:*:*:inference-profile/*anthropic.claude-*",
  ]
}
