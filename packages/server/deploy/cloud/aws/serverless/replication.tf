# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Cross-region replication for the three buckets, into `replica_region`.
#
# This is not only a durability feature. The SPA replica is the failover origin
# in the CloudFront origin group (see cloudfront.tf), so the room UI survives
# the loss of the primary region's S3 endpoint — which is the difference between
# "participants see a broken page" and "participants keep talking".
#
# What it costs: a second copy of every object, plus inter-region transfer on
# write. Attachments are room content and the SPA is a build artefact, so the
# steady-state volume is small; a room that exchanges large files will notice.
#
# There is deliberately no variable to switch this off. An earlier draft had
# one, defaulting to on, and it was the wrong shape for two reasons. The small
# one: `count` makes the replica set invisible to graph-based policy analysis,
# so a toggled stack cannot be shown to be configured correctly. The real one: the
# only thing the toggle bought was a cheaper deployment that silently had no
# failover origin and one copy of every room's attachments. If a deployer wants
# that, deleting this file is a clearer way to ask for it than setting a flag.

# --- replication role -----------------------------------------------------
#
# Unconditional: the source key policies grant it decrypt access, and a role
# whose policy is absent grants nothing. Making the role itself conditional
# would mean those key policies could not reference it.

data "aws_iam_policy_document" "replication_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "replication" {
  name_prefix        = "${local.name}-s3-repl-"
  assume_role_policy = data.aws_iam_policy_document.replication_assume.json
}

data "aws_iam_policy_document" "replication" {

  statement {
    sid = "ReadSourceBucketConfiguration"
    actions = [
      "s3:GetReplicationConfiguration",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.attachments.arn,
      aws_s3_bucket.spa.arn,
      aws_s3_bucket.logs.arn,
    ]
  }
  statement {
    sid = "ReadSourceObjectVersions"
    actions = [
      "s3:GetObjectVersionForReplication",
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionTagging",
    ]
    resources = [
      "${aws_s3_bucket.attachments.arn}/*",
      "${aws_s3_bucket.spa.arn}/*",
      "${aws_s3_bucket.logs.arn}/*",
    ]
  }
  statement {
    sid = "WriteReplicas"
    actions = [
      "s3:ReplicateObject",
      "s3:ReplicateDelete",
      "s3:ReplicateTags",
    ]
    resources = [
      "${aws_s3_bucket.attachments_replica.arn}/*",
      "${aws_s3_bucket.spa_replica.arn}/*",
      "${aws_s3_bucket.logs_replica.arn}/*",
    ]
  }
  statement {
    sid       = "DecryptSourceObjects"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [aws_kms_key.attachments.arn, aws_kms_key.spa.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "EncryptReplicaObjects"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [aws_kms_key.attachments_replica.arn, aws_kms_key.spa_replica.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.replica_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "replication" {
  role   = aws_iam_role.replication.id
  policy = data.aws_iam_policy_document.replication.json
}

# --- replica-region keys --------------------------------------------------

resource "aws_kms_key" "attachments_replica" {
  provider                = aws.replica
  description             = "${local.name} replicated room attachments at rest"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_key_policy" "attachments_replica" {
  provider = aws.replica
  key_id   = aws_kms_key.attachments_replica.id
  policy   = data.aws_iam_policy_document.attachments_replica_kms.json
}

data "aws_iam_policy_document" "attachments_replica_kms" {

  statement {
    sid    = "AccountKeyAdministration"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
    actions   = local.kms_admin_actions
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [local.account_id]
    }
  }
  statement {
    sid    = "ReplicationRoleEncryptsReplica"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.replication.arn]
    }
    actions   = ["kms:Encrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.replica_region}.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "spa_replica" {
  provider                = aws.replica
  description             = "${local.name} replicated SPA bundle at rest"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_key_policy" "spa_replica" {
  provider = aws.replica
  key_id   = aws_kms_key.spa_replica.id
  policy   = data.aws_iam_policy_document.spa_replica_kms.json
}

data "aws_iam_policy_document" "spa_replica_kms" {

  statement {
    sid    = "AccountKeyAdministration"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
    actions   = local.kms_admin_actions
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [local.account_id]
    }
  }
  statement {
    sid    = "ReplicationRoleEncryptsReplica"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.replication.arn]
    }
    actions   = ["kms:Encrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.replica_region}.amazonaws.com"]
    }
  }
  # The failover origin is read by the same distribution, so CloudFront needs
  # the replica key too. Without this, failover serves 403s — which is worse
  # than no failover, because it only shows up during an outage.
  statement {
    sid    = "CloudFrontOacReadsReplicaBundle"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.spa.arn]
    }
  }
}

# --- replica-region log target -------------------------------------------
#
# Same documented constraint as the primary log bucket: a log-delivery target
# must be SSE-S3, so this bucket cannot carry a CMK either. It exists because
# S3 server access logging requires the target to be in the same region as the
# source, so the replica buckets cannot log to the primary region's bucket.

resource "aws_s3_bucket" "logs_replica" {
  provider      = aws.replica
  bucket        = "${local.name}-logs-${local.account_id}-${var.replica_region}"
  force_destroy = true

  lifecycle {
    precondition {
      condition     = var.replica_region != var.region
      error_message = "replica_region must differ from region: cross-region replication cannot target the source region."
    }
  }
}

resource "aws_s3_bucket_public_access_block" "logs_replica" {
  provider                = aws.replica
  bucket                  = aws_s3_bucket.logs_replica.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.logs_replica.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.logs_replica.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.logs_replica.id
  rule {
    id     = "expire-logs"
    status = "Enabled"
    filter {
    }
    expiration {
      days = 90
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
  rule {
    id     = "expire-self-access-logs"
    status = "Enabled"
    filter {
      prefix = "s3-access/logs/"
    }
    expiration {
      days = 7
    }
  }
}

resource "aws_s3_bucket_logging" "logs_replica" {
  provider      = aws.replica
  bucket        = aws_s3_bucket.logs_replica.id
  target_bucket = aws_s3_bucket.logs_replica.id
  target_prefix = "s3-access/logs/"
}

resource "aws_s3_bucket_notification" "logs_replica" {
  provider    = aws.replica
  bucket      = aws_s3_bucket.logs_replica.id
  eventbridge = true
}

resource "aws_s3_bucket_ownership_controls" "logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.logs_replica.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_policy" "logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.logs_replica.id
  policy   = data.aws_iam_policy_document.logs_replica_delivery.json
}

data "aws_iam_policy_document" "logs_replica_delivery" {
  statement {
    sid    = "DenyNonTlsRequests"
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.logs_replica.arn, "${aws_s3_bucket.logs_replica.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
  statement {
    sid    = "S3ServerAccessLogsPolicy"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.logs_replica.arn}/s3-access/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# --- replica buckets ------------------------------------------------------

resource "aws_s3_bucket" "attachments_replica" {
  provider      = aws.replica
  bucket        = "${local.name}-attach-aws-${local.account_id}-${var.replica_region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "attachments_replica" {
  provider                = aws.replica
  bucket                  = aws_s3_bucket.attachments_replica.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "attachments_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.attachments_replica.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.attachments_replica.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "attachments_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.attachments_replica.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "attachments_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.attachments_replica.id
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_logging" "attachments_replica" {
  provider      = aws.replica
  bucket        = aws_s3_bucket.attachments_replica.id
  target_bucket = aws_s3_bucket.logs_replica.id
  target_prefix = "s3-access/attachments/"
}

resource "aws_s3_bucket_policy" "attachments_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.attachments_replica.id
  policy   = data.aws_iam_policy_document.attachments_replica_bucket.json
}

data "aws_iam_policy_document" "attachments_replica_bucket" {
  statement {
    sid    = "DenyNonTlsRequests"
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.attachments_replica.arn, "${aws_s3_bucket.attachments_replica.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_notification" "attachments_replica" {
  provider    = aws.replica
  bucket      = aws_s3_bucket.attachments_replica.id
  eventbridge = true
}

resource "aws_s3_bucket" "spa_replica" {
  provider      = aws.replica
  bucket        = "${local.name}-spa-${local.account_id}-${var.replica_region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "spa_replica" {
  provider                = aws.replica
  bucket                  = aws_s3_bucket.spa_replica.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "spa_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.spa_replica.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.spa_replica.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "spa_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.spa_replica.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "spa_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.spa_replica.id
  rule {
    id     = "expire-old-builds"
    status = "Enabled"
    filter {
    }
    noncurrent_version_expiration {
      noncurrent_days = 14
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_logging" "spa_replica" {
  provider      = aws.replica
  bucket        = aws_s3_bucket.spa_replica.id
  target_bucket = aws_s3_bucket.logs_replica.id
  target_prefix = "s3-access/spa/"
}

resource "aws_s3_bucket_notification" "spa_replica" {
  provider    = aws.replica
  bucket      = aws_s3_bucket.spa_replica.id
  eventbridge = true
}

# The failover origin must be readable by the same distribution as the primary.
resource "aws_s3_bucket_policy" "spa_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.spa_replica.id
  policy   = data.aws_iam_policy_document.spa_replica_bucket.json
}

data "aws_iam_policy_document" "spa_replica_bucket" {
  statement {
    sid    = "DenyNonTlsRequests"
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.spa_replica.arn, "${aws_s3_bucket.spa_replica.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
  statement {
    sid    = "AllowCloudFrontOAC"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.spa_replica.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.spa.arn]
    }
  }
}

# --- replication rules ----------------------------------------------------

resource "aws_s3_bucket_replication_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  role   = aws_iam_role.replication.arn

  rule {
    id       = "attachments-to-replica"
    status   = "Enabled"
    priority = 0

    filter {
    }
    delete_marker_replication {
      status = "Enabled"
    }

    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }

    destination {
      bucket        = aws_s3_bucket.attachments_replica.arn
      storage_class = "STANDARD_IA"
      encryption_configuration {
        replica_kms_key_id = aws_kms_key.attachments_replica.arn
      }
    }
  }

  depends_on = [
    aws_s3_bucket_versioning.attachments,
    aws_s3_bucket_versioning.attachments_replica,
    aws_iam_role_policy.replication,
  ]
}

resource "aws_s3_bucket_replication_configuration" "spa" {
  bucket = aws_s3_bucket.spa.id
  role   = aws_iam_role.replication.arn

  rule {
    id       = "spa-to-replica"
    status   = "Enabled"
    priority = 0

    filter {
    }
    delete_marker_replication {
      status = "Enabled"
    }

    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }

    destination {
      bucket = aws_s3_bucket.spa_replica.arn
      # Not STANDARD_IA: this is the CloudFront failover origin, so it is read
      # under exactly the conditions where retrieval latency matters.
      storage_class = "STANDARD"
      encryption_configuration {
        replica_kms_key_id = aws_kms_key.spa_replica.arn
      }
    }
  }

  depends_on = [
    aws_s3_bucket_versioning.spa,
    aws_s3_bucket_versioning.spa_replica,
    aws_iam_role_policy.replication,
  ]
}

resource "aws_s3_bucket_replication_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  role   = aws_iam_role.replication.arn

  rule {
    id       = "logs-to-replica"
    status   = "Enabled"
    priority = 0

    filter {
    }
    delete_marker_replication {
      status = "Enabled"
    }

    destination {
      bucket        = aws_s3_bucket.logs_replica.arn
      storage_class = "STANDARD_IA"
    }
  }

  depends_on = [
    aws_s3_bucket_versioning.logs,
    aws_s3_bucket_versioning.logs_replica,
    aws_iam_role_policy.replication,
  ]
}
