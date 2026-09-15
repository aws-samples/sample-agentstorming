# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  description = "Public entry point for the Agent Storming room: HTTPS only"
  vpc_id      = aws_vpc.main.id
}

# There is deliberately no port-80 rule and no HTTP listener.
#
# The previous version opened 80 to the internet so the ALB could answer with a
# 301 to HTTPS, and justified it as "redirect only". That is true and it is also
# an unnecessary plaintext listener: a participant who types http:// has already
# sent the request in the clear, and the redirect only helps after the fact. The
# viewer-facing redirect belongs at CloudFront, which does it with
# viewer_protocol_policy = redirect-to-https before anything reaches this load
# balancer.
#
# Consequence, stated plainly: hitting the ALB's own DNS name over http:// now
# fails to connect rather than redirecting. That is the intended behaviour for a
# service whose spec (§8.1) requires TLS and says the server MUST reject
# plaintext HTTP.
resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from the internet — the participant and UI entry point"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# Egress restricted to the task port in the VPC rather than all of 0.0.0.0/0.
# An ALB only ever needs to reach its targets.
resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "ALB to the server tasks"
  referenced_security_group_id = aws_security_group.ecs.id
  ip_protocol                  = "tcp"
  from_port                    = 8440
  to_port                      = 8440
}

resource "aws_lb" "main" {
  name               = "${local.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  # Public subnets only. The tasks it forwards to are private.
  subnets = local.public_subnet_ids

  # Refuse requests with malformed headers rather than passing them to the
  # server, which signature-verifies every event and should not have to also
  # defend against header smuggling.
  drop_invalid_header_fields = true
  enable_deletion_protection = var.enable_deletion_protection

  access_logs {
    bucket  = aws_s3_bucket.logs.id
    prefix  = "alb"
    enabled = true
  }
  # SSE connections are held open up to 30s between events. 300s gives
  # plenty of headroom and is also the default ALB idle timeout.
  idle_timeout = 300
}

resource "aws_lb_target_group" "server" {
  name_prefix = substr("${local.name}-", 0, 6)
  port        = 8440
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # Fast drain is fine — SSE reconnects automatically with Last-Event-ID.
  deregistration_delay = 60
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.server.arn
  }
}
