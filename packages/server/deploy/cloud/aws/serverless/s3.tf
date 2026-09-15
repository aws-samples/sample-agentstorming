# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# --- attachments key ------------------------------------------------------

resource "aws_kms_key" "attachments" {
  description             = "${local.name} room attachments at rest"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "attachments" {
  name          = "alias/${local.name}-attachments"
  target_key_id = aws_kms_key.attachments.key_id
}

resource "aws_kms_key_policy" "attachments" {
  key_id = aws_kms_key.attachments.id
  policy = data.aws_iam_policy_document.attachments_kms.json
}

data "aws_iam_policy_document" "attachments_kms" {
  statement {
    sid    = "AccountKeyAdministration"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
    actions   = local.kms_admin_actions
    resources = ["*"] # required form in a key policy: "this key"
    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [local.account_id]
    }
  }
  statement {
    sid    = "ServerTaskUsesKey"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.task.arn]
    }
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }
  # Replication reads the source object, which means decrypting it with this
  # key. Without this statement the replication rule silently fails and objects
  # sit in FAILED status.
  statement {
    sid    = "ReplicationRoleDecryptsSource"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.replication.arn]
    }
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }
}

# --- SPA key --------------------------------------------------------------
#
# The SPA bundle is public content, so a CMK buys no confidentiality against a
# reader. It is here for a different reason: the SPA is the code that holds each
# participant's Ed25519 private key in the browser, so integrity of what is
# served matters more than secrecy, and a CMK gives per-object key-usage records
# in CloudTrail — a tamper signal on the artefact.

resource "aws_kms_key" "spa" {
  description             = "${local.name} SPA bundle at rest"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "spa" {
  name          = "alias/${local.name}-spa"
  target_key_id = aws_kms_key.spa.key_id
}

resource "aws_kms_key_policy" "spa" {
  key_id = aws_kms_key.spa.id
  policy = data.aws_iam_policy_document.spa_kms.json
}

data "aws_iam_policy_document" "spa_kms" {
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
  # The deployer and CI upload the bundle. Scoped to S3 so the grant cannot be
  # used for anything but object encryption.
  statement {
    sid    = "AccountUploadsBundleViaS3"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }
  # CloudFront reads through OAC and must be able to decrypt. The action list
  # and the aws:SourceArn condition are the shape AWS documents for OAC with
  # SSE-KMS; narrowing further breaks object delivery.
  statement {
    sid    = "CloudFrontOacReadsBundle"
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
  statement {
    sid    = "ReplicationRoleDecryptsSource"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.replication.arn]
    }
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }
}

# --- attachments bucket ---------------------------------------------------

resource "aws_s3_bucket" "attachments" {
  bucket        = "${local.name}-attach-aws-${local.account_id}-${var.region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "attachments" {
  bucket                  = aws_s3_bucket.attachments.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.attachments.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id
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

resource "aws_s3_bucket_logging" "attachments" {
  bucket        = aws_s3_bucket.attachments.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "s3-access/attachments/"
}

# TLS-only, on every bucket. Required by the internal Secure Build Path for S3,
# which mandates a Deny on aws:SecureTransport=false for both the access-logs
# bucket and the data bucket. No scanner in this repository's set checks for it —
# it was missing until the standard was read rather than inferred. Every AWS
# log-delivery principal and CloudFront OAC use HTTPS, so this denies nothing the
# stack relies on.
resource "aws_s3_bucket_policy" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  policy = data.aws_iam_policy_document.attachments_bucket.json
}

data "aws_iam_policy_document" "attachments_bucket" {
  statement {
    sid    = "DenyNonTlsRequests"
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.attachments.arn, "${aws_s3_bucket.attachments.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

# EventBridge delivery, on every bucket. There is no consumer in this module —
# the server writes and presigns directly — but an object-level event stream is
# how a deployer notices an attachment appearing that no room event references,
# and enabling it costs nothing until something subscribes.
resource "aws_s3_bucket_notification" "attachments" {
  bucket      = aws_s3_bucket.attachments.id
  eventbridge = true
}

# --- SPA bucket -----------------------------------------------------------

resource "aws_s3_bucket" "spa" {
  bucket        = "${local.name}-spa-${local.account_id}-${var.region}"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "spa" {
  bucket = aws_s3_bucket.spa.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.spa.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "spa" {
  bucket = aws_s3_bucket.spa.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "spa" {
  bucket = aws_s3_bucket.spa.id
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

resource "aws_s3_bucket_logging" "spa" {
  bucket        = aws_s3_bucket.spa.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "s3-access/spa/"
}

resource "aws_s3_bucket_notification" "spa" {
  bucket      = aws_s3_bucket.spa.id
  eventbridge = true
}

resource "aws_s3_bucket_public_access_block" "spa" {
  bucket                  = aws_s3_bucket.spa.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- shared log target ----------------------------------------------------
#
# This bucket is the one place in the stack that cannot be CMK-encrypted, and
# the reason is a documented AWS constraint rather than a preference:
#
#   * ALB access logs — "The only server-side encryption option that's
#     supported is Amazon S3-managed keys (SSE-S3)."
#     https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html
#
#   * S3 server access logs — "The destination bucket must use Amazon S3
#     managed keys (SSE-S3). If the destination bucket uses SSE-KMS, Amazon S3
#     might deliver log objects that are encrypted with a key that you can't
#     access."
#     https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-server-access-logging.html
#
# So CKV_AWS_145 ("buckets are encrypted with KMS by default") and CKV_AWS_18
# ("buckets have access logging") cannot both hold for the same bucket. Setting
# sse_algorithm = "aws:kms" here would satisfy the scanner and break log
# delivery, which is worse than the finding. It stays SSE-S3, deliberately.
#
# The bucket does log its own access, to itself under a distinct prefix. AWS
# discourages that because logs about writing logs generate more logs; the
# lifecycle rule below bounds the growth, and the alternative is a second
# log-target bucket with exactly the same problem one level down.

resource "aws_s3_bucket" "logs" {
  bucket        = "${local.name}-logs-${local.account_id}-${var.region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
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
  # Self-logging writes under s3-access/logs/. Expire it faster than the real
  # log data so the recursive tail cannot accumulate.
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

resource "aws_s3_bucket_logging" "logs" {
  bucket        = aws_s3_bucket.logs.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "s3-access/logs/"
}

resource "aws_s3_bucket_notification" "logs" {
  bucket      = aws_s3_bucket.logs.id
  eventbridge = true
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id
  # Enforced, not Preferred: ALB and S3 log delivery both write via the
  # bucket policy below, so no ACL path is needed and object ownership stays
  # unambiguous.
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id
  policy = data.aws_iam_policy_document.logs_delivery.json
}

data "aws_iam_policy_document" "logs_delivery" {
  statement {
    sid    = "DenyNonTlsRequests"
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.logs.arn, "${aws_s3_bucket.logs.arn}/*"]
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
    resources = ["${aws_s3_bucket.logs.arn}/s3-access/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
  statement {
    sid    = "AlbAccessLogs"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logdelivery.elasticloadbalancing.amazonaws.com"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.logs.arn}/alb/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# --- SPA config object ----------------------------------------------------

locals {
  # CloudFront proxies /v1/* to the ALB (see cloudfront.tf), so the SPA's
  # api_base is the CloudFront origin itself — no mixed content, single
  # origin, HTTPS end-to-end.
  spa_api_base = "https://${aws_cloudfront_distribution.spa.domain_name}"
  spa_config_payload = {
    api_base     = local.spa_api_base
    default_room = var.default_room
  }
}

resource "aws_s3_object" "spa_config" {
  bucket        = aws_s3_bucket.spa.id
  key           = "config.json"
  content_type  = "application/json"
  content       = jsonencode(local.spa_config_payload)
  cache_control = "no-store"
  etag          = md5(jsonencode(local.spa_config_payload))

  kms_key_id = aws_kms_key.spa.arn

  depends_on = [aws_kms_key_policy.spa]
}
