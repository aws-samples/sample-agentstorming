# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

data "aws_caller_identity" "current" {}

# Filtered on zone name, so the result is exactly these zones: it cannot grow,
# shrink or reorder when AWS adds a zone or marks one unavailable. Unfiltered,
# the stack took the first two available names, and any change to that list
# would have replaced every subnet, and the NAT gateways and Aurora with them.
# Deliberately no `state = "available"`: with it, an impaired zone dropped out
# of the result, the postcondition failed, and no plan could run, the one time
# an operator most needs to change something.
data "aws_availability_zones" "available" {
  filter {
    name   = "zone-name"
    values = local.az_names
  }

  lifecycle {
    postcondition {
      condition     = length(self.names) == length(local.az_names)
      error_message = "Not every zone in ${jsonencode(local.az_names)} exists in ${var.region}. Set availability_zone_names to two zones that do."
    }
  }
}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id

  # Two AZs: the ALB requires at least two subnets in distinct zones, and
  # Aurora needs the same for failover. The region's "a" and "b" zones unless
  # set, which is what the stack has always chosen wherever both exist.
  az_names = var.availability_zone_names != null ? var.availability_zone_names : ["${var.region}a", "${var.region}b"]
  azs      = data.aws_availability_zones.available.names

  # Public subnets hold the ALB and the NAT gateway. Private subnets hold the
  # Fargate tasks and Aurora — nothing with a route to the internet gateway.
  public_subnet_ids  = [for s in aws_subnet.public : s.id]
  private_subnet_ids = [for s in aws_subnet.private : s.id]

  # Key administration, granted to the account root so that IAM policies can
  # delegate it. Deliberately NOT `kms:*`.
  #
  # The AWS default key policy grants `kms:*` to the account root. That is the
  # documented way to avoid locking a key out of its own account, but it also
  # means every data-plane operation on the key is delegable through IAM alone,
  # which makes the key policy stop being a second control. Enumerating the
  # administrative actions keeps the anti-lockout property — `kms:PutKeyPolicy`
  # is in the list, so the policy can always be repaired — while forcing every
  # cryptographic use of the key to be granted explicitly, per principal, in
  # the statements below each key.
  #
  # Consequence to be aware of when editing: adding a new consumer of a CMK
  # here now requires a key-policy statement as well as an IAM policy. An IAM
  # grant alone will fail with AccessDenied. That is the intended trade.
  kms_admin_actions = [
    "kms:CancelKeyDeletion",
    "kms:CreateAlias",
    "kms:CreateGrant",
    "kms:DeleteAlias",
    "kms:DescribeKey",
    "kms:DisableKey",
    "kms:DisableKeyRotation",
    "kms:EnableKey",
    "kms:EnableKeyRotation",
    "kms:GetKeyPolicy",
    "kms:GetKeyRotationStatus",
    "kms:ListGrants",
    "kms:ListKeyPolicies",
    "kms:ListResourceTags",
    "kms:PutKeyPolicy",
    "kms:RetireGrant",
    "kms:RevokeGrant",
    "kms:ScheduleKeyDeletion",
    "kms:TagResource",
    "kms:UntagResource",
    "kms:UpdateAlias",
    "kms:UpdateKeyDescription",
  ]
}
