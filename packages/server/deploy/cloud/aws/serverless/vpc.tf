# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# A dedicated VPC, replacing the default-VPC layout this stack used to assume.
#
# The old shape put the Fargate tasks and the Aurora cluster in the default
# VPC's subnets, which have a route to an internet gateway, and gave each task
# a public IP so it could reach ECR and Bedrock. That worked, and it meant the
# database and the application were one security-group rule away from being
# internet-reachable. The `assign_task_public_ip` variable existed to turn it
# off and could not be, because there was no NAT gateway to egress through.
#
# Now: the ALB is the only thing in a public subnet. Tasks and Aurora sit in
# private subnets and egress through a NAT gateway. Nothing in the data path
# has a public address.
#
# Cost note, stated plainly because it is the reason the old shape existed: a
# NAT gateway is roughly USD 32/month per AZ plus data processing. This module
# provisions one, in the first AZ, shared by both private subnets — cheaper
# than one per AZ, and the failure mode is loss of egress for the second AZ's
# tasks if that AZ goes away. Set `nat_gateway_per_az = true` for production.

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.name}-vpc" }
}

# Flow logs: the room transcript is signed and auditable at the application
# layer, but nothing recorded who connected at the network layer.
resource "aws_flow_log" "main" {
  vpc_id                   = aws_vpc.main.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs.arn
  iam_role_arn             = aws_iam_role.flow_logs.arn
  max_aggregation_interval = 60
}

resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/${local.name}/vpc-flow-logs"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

data "aws_iam_policy_document" "flow_logs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  name_prefix        = "${local.name}-flowlogs-"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume.json
}

data "aws_iam_policy_document" "flow_logs" {
  statement {
    sid = "WriteFlowLogsToOwnGroup"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      aws_cloudwatch_log_group.flow_logs.arn,
      "${aws_cloudwatch_log_group.flow_logs.arn}:log-stream:*",
    ]
  }
}

resource "aws_iam_role_policy" "flow_logs" {
  role   = aws_iam_role.flow_logs.id
  policy = data.aws_iam_policy_document.flow_logs.json
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "public" {
  for_each = { for i, az in local.azs : az => i }

  vpc_id            = aws_vpc.main.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, each.value)

  # Explicitly false. The ALB gets its public addresses from the load balancer
  # itself, and nothing else is launched here; a subnet default of true is how
  # a task ends up internet-facing by accident.
  map_public_ip_on_launch = false

  tags = { Name = "${local.name}-public-${each.key}", Tier = "public" }
}

resource "aws_subnet" "private" {
  for_each = { for i, az in local.azs : az => i }

  vpc_id                  = aws_vpc.main.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, each.value + 8)
  map_public_ip_on_launch = false

  tags = { Name = "${local.name}-private-${each.key}", Tier = "private" }
}

resource "aws_eip" "nat" {
  for_each = var.nat_gateway_per_az ? toset(local.azs) : toset([local.azs[0]])
  domain   = "vpc"
  tags     = { Name = "${local.name}-nat-${each.key}" }
}

resource "aws_nat_gateway" "main" {
  for_each = var.nat_gateway_per_az ? toset(local.azs) : toset([local.azs[0]])

  allocation_id = aws_eip.nat[each.key].id
  subnet_id     = aws_subnet.public[each.key].id
  tags          = { Name = "${local.name}-nat-${each.key}" }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-public" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  for_each = aws_subnet.private
  vpc_id   = aws_vpc.main.id
  tags     = { Name = "${local.name}-private-${each.key}" }
}

resource "aws_route" "private_nat" {
  for_each = aws_subnet.private

  route_table_id         = aws_route_table.private[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  # With a single shared NAT gateway every private subnet egresses through the
  # first AZ's gateway.
  nat_gateway_id = var.nat_gateway_per_az ? aws_nat_gateway.main[each.key].id : aws_nat_gateway.main[local.azs[0]].id
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private[each.key].id
}

# The default security group of a VPC allows all intra-group traffic. Nothing
# is ever attached to it here, but leaving it permissive means anything later
# launched without an explicit group inherits an allow-all posture.
resource "aws_default_security_group" "main" {
  vpc_id = aws_vpc.main.id
  # No ingress, no egress blocks: both empty, which revokes the defaults.
}

# S3 traffic (attachments, and ECR's layer store) leaves through a gateway
# endpoint rather than the NAT gateway. Cheaper, and it keeps object traffic
# off the public path entirely.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for rt in aws_route_table.private : rt.id]
}
