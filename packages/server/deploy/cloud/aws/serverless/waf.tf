# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Two WAFv2 web ACLs: one CLOUDFRONT-scoped (which must live in us-east-1) and
# one REGIONAL for the ALB. Both are needed because the ALB is reachable
# directly as well as through CloudFront, and an ACL on the distribution does
# nothing for a request that goes straight to the load balancer.
#
# This used to be argued away on the grounds that the protocol rate-limits
# per-PID and per-IP in the application layer. It does — but that enforcement
# costs a request reaching Fargate, a signature verification, and a database
# round trip before it can say no. A managed rule group drops the obviously
# hostile traffic before any of that. The two controls are complementary, not
# redundant.
#
# Cost, stated because it was the original objection: roughly USD 5 per web ACL
# per month, USD 1 per managed rule group per month, plus USD 0.60 per million
# requests. Two ACLs with three rule groups each is on the order of USD 16/month
# before request charges. There is no variable to disable it: a control that
# defaults to on and can be switched off is a control that will be off in some
# deployments and reported as on in all of them.
#
# The two ACLs are spelled out rather than generated from a list with
# `dynamic "rule"`. The duplication is deliberate. A dynamic block is opaque to
# graph-based policy analysis — the rules are there at apply time but a scanner
# reading the configuration cannot see that AWSManagedRulesKnownBadInputsRuleSet
# is among them, so the stack was simultaneously protected and unprovable. If
# these two drift apart, that is the cost; being able to read the rule set off
# the page is worth it.
#
# AWSManagedRulesKnownBadInputsRuleSet is not optional decoration: it carries
# the Log4j/JNDI signatures (Log4JRCE). This stack runs no Java, but CloudFront
# and the ALB will forward a JNDI payload to whatever a future deployer puts
# behind them.
#
# AWSManagedRulesCommonRuleSet is applied with two of its rules moved to count
# rather than block, because both fire on this protocol's legitimate traffic
# rather than on attacks against it:
#
#   SizeRestrictions_BODY — caps request bodies at 8 KB. A signed event carrying
#   an attachment manifest or a long moderator summary exceeds that legitimately.
#
#   NoUserAgent_HEADER — agent participants are SDK clients, not browsers, and
#   several HTTP libraries send no User-Agent by default.

# --- CloudFront-scoped ACL ------------------------------------------------

resource "aws_wafv2_web_acl" "cloudfront" {
  provider = aws.us_east_1

  name        = "${local.name}-cloudfront"
  description = "Agent Storming distribution: managed rule groups plus per-IP rate limiting"
  scope       = "CLOUDFRONT"

  default_action {
    allow {
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 10

    override_action {
      none {
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "KnownBadInputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 20

    override_action {
      none {
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "IpReputation"
      sampled_requests_enabled   = true
    }
  }

  # AWSManagedRulesAnonymousIpList, with HostingProviderIPList counted rather
  # than blocked.
  #
  # The group's AnonymousIPList rule covers Tor exit nodes and anonymising
  # proxies, which have no legitimate reason to reach a room. Its companion
  # HostingProviderIPList covers cloud and hosting provider ranges — and this
  # protocol's participants are agents, which run in exactly those ranges.
  # Enabling it as a block would deny the primary client population, so it is
  # set to count: the signal is recorded, nothing is dropped.
  rule {
    name     = "AWSManagedRulesAnonymousIpList"
    priority = 25

    override_action {
      none {
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAnonymousIpList"
        vendor_name = "AWS"

        rule_action_override {
          name = "HostingProviderIPList"
          action_to_use {
            count {
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AnonymousIpList"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 30

    override_action {
      none {
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {
            }
          }
        }
        rule_action_override {
          name = "NoUserAgent_HEADER"
          action_to_use {
            count {
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "PerIpRateLimit"
    priority = 40

    action {
      block {
      }
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_per_5min
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "PerIpRateLimit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name}-cloudfront"
    sampled_requests_enabled   = true
  }
}

# WAF log group names must start with aws-waf-logs-; the service rejects
# anything else. CloudFront-scoped ACLs log in us-east-1.
resource "aws_cloudwatch_log_group" "waf_cloudfront" {
  provider          = aws.us_east_1
  name              = "aws-waf-logs-${local.name}-cloudfront"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.waf_logs_us_east_1.arn
}

resource "aws_wafv2_web_acl_logging_configuration" "cloudfront" {
  provider                = aws.us_east_1
  log_destination_configs = [aws_cloudwatch_log_group.waf_cloudfront.arn]
  resource_arn            = aws_wafv2_web_acl.cloudfront.arn

  # Authorization carries participant bearer tokens; the point of WAF logs is
  # the request shape, not the credential.
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
  redacted_fields {
    single_header {
      name = "cookie"
    }
  }
}

resource "aws_kms_key" "waf_logs_us_east_1" {
  provider                = aws.us_east_1
  description             = "${local.name} CloudFront WAF log encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_key_policy" "waf_logs_us_east_1" {
  provider = aws.us_east_1
  key_id   = aws_kms_key.waf_logs_us_east_1.id
  policy   = data.aws_iam_policy_document.waf_logs_us_east_1_kms.json
}

data "aws_iam_policy_document" "waf_logs_us_east_1_kms" {
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
    sid    = "CloudWatchLogsUsesKey"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logs.us-east-1.amazonaws.com"]
    }
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:us-east-1:${local.account_id}:log-group:aws-waf-logs-${local.name}-*"]
    }
  }
}

# --- regional ACL for the ALB --------------------------------------------

resource "aws_wafv2_web_acl" "alb" {
  name        = "${local.name}-alb"
  description = "Agent Storming ALB: managed rule groups plus per-IP rate limiting"
  scope       = "REGIONAL"

  default_action {
    allow {
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 10

    override_action {
      none {
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "KnownBadInputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 20

    override_action {
      none {
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "IpReputation"
      sampled_requests_enabled   = true
    }
  }

  # AWSManagedRulesAnonymousIpList, with HostingProviderIPList counted rather
  # than blocked.
  #
  # The group's AnonymousIPList rule covers Tor exit nodes and anonymising
  # proxies, which have no legitimate reason to reach a room. Its companion
  # HostingProviderIPList covers cloud and hosting provider ranges — and this
  # protocol's participants are agents, which run in exactly those ranges.
  # Enabling it as a block would deny the primary client population, so it is
  # set to count: the signal is recorded, nothing is dropped.
  rule {
    name     = "AWSManagedRulesAnonymousIpList"
    priority = 25

    override_action {
      none {
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAnonymousIpList"
        vendor_name = "AWS"

        rule_action_override {
          name = "HostingProviderIPList"
          action_to_use {
            count {
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AnonymousIpList"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 30

    override_action {
      none {
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {
            }
          }
        }
        rule_action_override {
          name = "NoUserAgent_HEADER"
          action_to_use {
            count {
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "PerIpRateLimit"
    priority = 40

    action {
      block {
      }
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_per_5min
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "PerIpRateLimit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name}-alb"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.alb.arn
}

resource "aws_cloudwatch_log_group" "waf_alb" {
  name              = "aws-waf-logs-${local.name}-alb"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_wafv2_web_acl_logging_configuration" "alb" {
  log_destination_configs = [aws_cloudwatch_log_group.waf_alb.arn]
  resource_arn            = aws_wafv2_web_acl.alb.arn

  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
  redacted_fields {
    single_header {
      name = "cookie"
    }
  }
}
