# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "server" {
  # Room events are an audit transcript; the logs about them should outlive a
  # fortnight. Encrypted with a CMK because request logs carry PIDs.
  name              = "/${local.name}/server"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_kms_key" "logs" {
  description             = "${local.name} server log encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_key_policy" "logs" {
  key_id = aws_kms_key.logs.id
  policy = data.aws_iam_policy_document.logs_kms.json
}

data "aws_iam_policy_document" "logs_kms" {
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
    sid    = "CloudWatchLogsUsesKey"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      # Two patterns: the stack's own groups under /<name>/, and the regional
      # WAF log group, whose name WAFv2 requires to begin with aws-waf-logs-
      # and which therefore cannot live under the first prefix.
      values = [
        "arn:aws:logs:${var.region}:${local.account_id}:log-group:/${local.name}/*",
        "arn:aws:logs:${var.region}:${local.account_id}:log-group:aws-waf-logs-${local.name}-*",
      ]
    }
  }
}

resource "aws_ecr_repository" "server" {
  name = "${local.name}-server"
  # Immutable tags: a deployed digest cannot be swapped out from under a
  # running task, which matters when the image is what verifies signatures.
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  force_delete = true

  encryption_configuration {
    encryption_type = "KMS"
  }
}

resource "aws_ecs_task_definition" "server" {
  family                   = "${local.name}-server"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.fargate_cpu
  memory                   = var.fargate_memory
  execution_role_arn       = aws_iam_role.task_exec.arn
  task_role_arn            = aws_iam_role.task.arn

  # Python needs a writable /tmp even when the root filesystem is read-only.
  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name         = "server"
      image        = "${aws_ecr_repository.server.repository_url}:latest"
      essential    = true
      portMappings = [{ containerPort = 8440, protocol = "tcp" }]
      environment = [
        { name = "AGENTSTORMING_BIND_HOST", value = "0.0.0.0" },
        { name = "AGENTSTORMING_BIND_PORT", value = "8440" },
        { name = "AGENTSTORMING_ATTACHMENTS_BACKEND", value = "s3" },
        { name = "AGENTSTORMING_ATTACHMENTS_S3_BUCKET", value = aws_s3_bucket.attachments.bucket },
        { name = "AGENTSTORMING_ATTACHMENTS_S3_REGION", value = var.region },
        # The RDS-managed secret contains only username and password, so the
        # non-secret half of the DSN is supplied here and the entrypoint
        # combines the two. A hostname is not a credential.
        { name = "AGENTSTORMING_DB_HOST", value = aws_rds_cluster.db.endpoint },
        { name = "AGENTSTORMING_DB_PORT", value = "5432" },
        { name = "AGENTSTORMING_DB_NAME", value = "agentstorming" },
        # Values in `environment` are returned by DescribeTaskDefinition to
        # anyone with ecs:Describe*, so nothing secret belongs here. The owner
        # PUBLIC key is fine in the clear — it is a public key.
        { name = "AGENTSTORMING_OWNER_PUBKEY", value = var.owner_pubkey },
        { name = "AGENTSTORMING_OWNER_PUBKEY_LABEL", value = var.owner_pubkey_label },
        # The origin invite and attachment links are built from. Without it the
        # server falls back to Starlette's request.base_url, which comes from the
        # Host header — behind CloudFront that is the ALB's name, so invite links
        # would point at the origin instead of the distribution, and the Host a
        # caller sends would decide where a link carrying an invite token points.
        # A custom alias wins if one is configured, since that is what users type.
        {
          name = "AGENTSTORMING_PUBLIC_BASE_URL"
          value = "https://${
            length(var.cloudfront_aliases) > 0
            ? var.cloudfront_aliases[0]
            : aws_cloudfront_distribution.spa.domain_name
          }"
        },
      ]
      secrets = [
        # RDS owns this secret and rotates it. See rds.tf for why there is no
        # longer an AGENTSTORMING_ADMIN_TOKEN entry here.
        { name = "AGENTSTORMING_DSN_JSON", valueFrom = aws_rds_cluster.db.master_user_secret[0].secret_arn },
      ]

      # Read-only root filesystem: the server writes nothing to disk in this
      # profile — attachments go to S3 and logs to stdout — so a writable root
      # is only useful to someone who has already achieved execution.
      readonlyRootFilesystem = true
      mountPoints = [
        { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.server.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "server"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "server" {
  name            = "${local.name}-server"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.server.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    # Private subnets, no public address. Egress to ECR, Bedrock and Secrets
    # Manager goes through the NAT gateway in vpc.tf; S3 goes through the
    # gateway endpoint. Nothing reaches these tasks except the ALB.
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.server.arn
    container_name   = "server"
    container_port   = 8440
  }

  # The HTTPS listener, not the old HTTP one: a target group cannot register
  # until a listener is forwarding to it, and HTTPS is now the only listener.
  depends_on = [aws_lb_listener.https]
}
