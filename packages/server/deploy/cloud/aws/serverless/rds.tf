# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_db_subnet_group" "db" {
  name = "${local.name}-db"
  # Private subnets only. The cluster has no route to the internet gateway.
  subnet_ids = local.private_subnet_ids
}

resource "aws_security_group" "rds" {
  name_prefix = "${local.name}-rds-"
  description = "Aurora cluster: reachable only from the server tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "ecs" {
  name_prefix = "${local.name}-ecs-"
  description = "Server tasks: reachable only from the ALB"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_ecs" {
  security_group_id            = aws_security_group.rds.id
  description                  = "Postgres from the server tasks only"
  referenced_security_group_id = aws_security_group.ecs.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# Aurora needs no egress at all: it is reached, it does not reach out. The
# previous rule allowed all protocols to 0.0.0.0/0, which for a database in a
# subnet that now has a NAT route would be an exfiltration path.
resource "aws_vpc_security_group_egress_rule" "rds_none" {
  security_group_id = aws_security_group.rds.id
  description       = "No egress required; pinned to the VPC CIDR for AWS-internal responses"
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "-1"
}

# --- database key ---------------------------------------------------------

resource "aws_kms_key" "db" {
  description             = "${local.name} Aurora cluster and DB credential secret"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "db" {
  name          = "alias/${local.name}-db"
  target_key_id = aws_kms_key.db.key_id
}

resource "aws_kms_key_policy" "db" {
  key_id = aws_kms_key.db.id
  policy = data.aws_iam_policy_document.db_kms.json
}

data "aws_iam_policy_document" "db_kms" {
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
    sid    = "AwsServicesUseKey"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["rds.amazonaws.com", "secretsmanager.amazonaws.com"]
    }
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:CreateGrant", "kms:DescribeKey"]
    resources = ["*"]
    # kms:CallerAccount rather than kms:ViaService. ViaService looks tighter,
    # but RDS reaches this key through more than one endpoint — storage
    # encryption, Performance Insights, and the managed master-user secret —
    # and an incomplete endpoint list fails at apply time or, worse, when
    # Performance Insights first tries to write. CallerAccount is the
    # confused-deputy guard that actually matters for a service principal.
    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [local.account_id]
    }
  }
  # Explicit grants for the two task roles.
  #
  # These used to be unnecessary because the account-root statement granted
  # kms:*, which let any IAM policy in the account delegate use of this key.
  # That statement now covers administration only, so every consumer needs
  # naming here. This is the trade described in main.tf: more verbose, but the
  # key policy is a real second control again rather than a rubber stamp.
  statement {
    sid    = "TaskRolesDecryptCredentialViaSecretsManager"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.task.arn, aws_iam_role.task_exec.arn]
    }
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
  }
}

# --- cluster parameter group ---------------------------------------------
#
# Query logging, with the privacy objection actually addressed rather than used
# as a reason not to log.
#
# The previous version left this off, arguing that log_statement=all records
# every statement including payload contents, and that room messages are
# participant data. The premise was right and the conclusion was avoidable:
# log_parameter_max_length=0 suppresses bound parameter values, and the server
# uses asyncpg, which sends every value as a bound parameter rather than
# inlining it. So the log shows which statements ran and against what shape,
# and not the contents of anyone's message.

resource "aws_rds_cluster_parameter_group" "db" {
  name_prefix = "${local.name}-aurora-pg-"
  family      = "aurora-postgresql16"
  description = "${local.name} Aurora Postgres: statement logging without parameter values"

  parameter {
    name  = "log_statement"
    value = "all"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "0"
  }

  # 0 = log no bound parameter values. This is the setting that makes statement
  # logging safe here; do not raise it without re-reading the note above.
  parameter {
    name  = "log_parameter_max_length"
    value = "0"
  }

  parameter {
    name  = "log_parameter_max_length_on_error"
    value = "0"
  }

  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Aurora Serverless v2 Postgres — auto-scaling compute, RDS-compatible
# Postgres wire protocol, full LISTEN/NOTIFY support. min_capacity must
# be >= 0.5 (not 0) because our long-lived SSE connections hold LISTEN
# subscriptions; scale-to-zero would break the fan-out.
resource "aws_rds_cluster" "db" {
  cluster_identifier              = "${local.name}-aurora"
  engine                          = "aurora-postgresql"
  engine_mode                     = "provisioned"
  engine_version                  = var.aurora_engine_version
  database_name                   = "agentstorming"
  master_username                 = "agentstorming"
  db_subnet_group_name            = aws_db_subnet_group.db.name
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.db.name
  vpc_security_group_ids          = [aws_security_group.rds.id]
  storage_encrypted               = true
  kms_key_id                      = aws_kms_key.db.arn
  backup_retention_period         = var.rds_backup_retention_days
  skip_final_snapshot             = true
  apply_immediately               = true

  # RDS owns the master password: it generates it, stores it in Secrets
  # Manager, and rotates it on a schedule AWS manages.
  #
  # This replaces a `random_password` resource whose value was written to
  # Terraform state in plaintext and never rotated. Two problems disappear at
  # once — the credential leaves state entirely, and rotation stops being
  # something a human has to remember to do by re-applying.
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.db.arn

  deletion_protection                 = var.enable_deletion_protection
  copy_tags_to_snapshot               = true
  iam_database_authentication_enabled = true
  # Postgres logs go to CloudWatch so a room's database activity is
  # auditable alongside its event log.
  enabled_cloudwatch_logs_exports = ["postgresql"]

  serverlessv2_scaling_configuration {
    min_capacity = var.aurora_min_capacity
    max_capacity = var.aurora_max_capacity
  }
}

resource "aws_rds_cluster_instance" "db" {
  cluster_identifier = aws_rds_cluster.db.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.db.engine
  engine_version     = aws_rds_cluster.db.engine_version
  apply_immediately  = true

  auto_minor_version_upgrade      = true
  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.db.arn
  monitoring_interval             = 60
  monitoring_role_arn             = aws_iam_role.rds_monitoring.arn
}

# Enhanced monitoring needs a service role to publish OS metrics.
resource "aws_iam_role" "rds_monitoring" {
  name_prefix        = "${local.name}-rds-mon-"
  assume_role_policy = data.aws_iam_policy_document.rds_monitoring_assume.json
}

data "aws_iam_policy_document" "rds_monitoring_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# --- task security group rules -------------------------------------------

resource "aws_vpc_security_group_egress_rule" "ecs_all" {
  security_group_id = aws_security_group.ecs.id
  description       = "Server tasks to Bedrock, ECR, Secrets Manager, S3 via NAT and the S3 endpoint"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "ecs_from_alb" {
  security_group_id            = aws_security_group.ecs.id
  description                  = "Server port from the ALB only"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8440
  to_port                      = 8440
}

# --- backup plan ----------------------------------------------------------
#
# `backup_retention_period` above already gives point-in-time recovery inside
# RDS. The previous version treated that as sufficient and called an AWS Backup
# plan "an account-level concern, not this module's". The difference that
# argument missed: RDS automated backups are deleted with the cluster, so they
# protect against data loss but not against deletion of the cluster itself —
# accidental or otherwise. A Backup vault is a separate resource with its own
# access policy and its own lifecycle.

resource "aws_backup_vault" "db" {
  name        = "${local.name}-db"
  kms_key_arn = aws_kms_key.db.arn
}

resource "aws_backup_plan" "db" {
  name = "${local.name}-db"

  rule {
    rule_name         = "daily-retain-35-days"
    target_vault_name = aws_backup_vault.db.name
    # 05:00 UTC, outside the Aurora maintenance window default.
    schedule          = "cron(0 5 ? * * *)"
    start_window      = 60
    completion_window = 300

    lifecycle {
      delete_after = 35
    }
  }
}

data "aws_iam_policy_document" "backup_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["backup.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup" {
  name_prefix        = "${local.name}-backup-"
  assume_role_policy = data.aws_iam_policy_document.backup_assume.json
}

resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_backup_selection" "db" {
  name         = "${local.name}-db"
  iam_role_arn = aws_iam_role.backup.arn
  plan_id      = aws_backup_plan.db.id
  resources    = [aws_rds_cluster.db.arn]
}

# --- admin token ----------------------------------------------------------
#
# There is no `aws_secretsmanager_secret` for the admin token any more, and it
# was not removed to satisfy a scanner.
#
# The deployment used to generate a 40-character token, store it in Secrets
# Manager under a CMK, and inject it into the task as
# AGENTSTORMING_ADMIN_TOKEN. Nothing read it. `Settings.admin_token` is
# declared in config.py and consumed nowhere; AuthGate validates admin bearer
# tokens against the `admin_tokens` table, whose rows are created out of band
# with `agentstorming-admin create-admin-token`. So the stack was provisioning,
# encrypting, and mounting a credential that authenticated nothing — pure
# attack surface with no function.
#
# To issue an admin token for this deployment, run the admin CLI against the
# cluster. That path stores only a SHA-256 hash server-side and supports having
# several valid tokens at once, which is what makes rotating one safe.
