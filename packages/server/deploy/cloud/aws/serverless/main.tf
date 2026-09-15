# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id

  # Two AZs: the ALB requires at least two subnets in distinct zones, and
  # Aurora needs the same for failover.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)

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
