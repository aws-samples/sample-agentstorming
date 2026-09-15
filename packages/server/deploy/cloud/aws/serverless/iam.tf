# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_exec" {
  name               = "${local.name}-task-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "task_exec" {
  role       = aws_iam_role.task_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_exec_extra" {
  role   = aws_iam_role.task_exec.id
  policy = data.aws_iam_policy_document.task_exec_extra.json
}

data "aws_iam_policy_document" "task_exec_extra" {
  statement {
    sid       = "ReadRdsManagedCredential"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_rds_cluster.db.master_user_secret[0].secret_arn]
  }
  statement {
    sid       = "DecryptRdsManagedCredential"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.db.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid = "InvokeAllowedModels"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    # Not "*": the server invokes models on behalf of participants, so its
    # blast radius should be the models this deployment sanctions.
    resources = var.allowed_bedrock_model_arns
  }
  statement {
    sid       = "DiscoverModels"
    actions   = ["bedrock:ListFoundationModels"]
    resources = ["*"] # no resource-level permissions for list APIs
  }
  statement {
    sid       = "AttachmentObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.attachments.arn}/*"]
  }
  statement {
    sid = "UseAttachmentsKey"
    # The attachments bucket is CMK-encrypted; without these, every upload
    # and download fails with AccessDenied.
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [aws_kms_key.attachments.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "ListAttachmentsBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.attachments.arn]
  }
  statement {
    sid     = "OwnLogsOnly"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "${aws_cloudwatch_log_group.server.arn}",
      "${aws_cloudwatch_log_group.server.arn}:log-stream:*",
    ]
  }
  statement {
    sid       = "ReadDbCredential"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_rds_cluster.db.master_user_secret[0].secret_arn]
  }
  statement {
    sid       = "DecryptDbCredential"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.db.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}
