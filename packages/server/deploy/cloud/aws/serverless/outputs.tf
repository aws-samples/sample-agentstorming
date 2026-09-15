# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "storm_api_url" {
  description = "Same origin as the SPA. All /v1/* calls are proxied through CloudFront."
  value       = "https://${aws_cloudfront_distribution.spa.domain_name}"
}

output "storm_alb_url" {
  description = "Direct ALB URL (bypasses CloudFront). Always HTTPS."
  value       = "https://${aws_lb.main.dns_name}"
}

output "spa_bucket" {
  value = aws_s3_bucket.spa.bucket
}

output "spa_cloudfront_domain" {
  value = aws_cloudfront_distribution.spa.domain_name
}

output "spa_url" {
  value = "https://${aws_cloudfront_distribution.spa.domain_name}"
}

output "attachments_bucket" {
  value = aws_s3_bucket.attachments.bucket
}

output "ecr_repository" {
  value = aws_ecr_repository.server.repository_url
}

output "aurora_endpoint" {
  value = aws_rds_cluster.db.endpoint
}

output "rds_secret_arn" {
  description = "RDS-managed master user secret. Created and rotated by RDS, not by Terraform."
  value       = aws_rds_cluster.db.master_user_secret[0].secret_arn
}

output "spa_replica_bucket" {
  description = "CloudFront failover origin, in replica_region."
  value       = aws_s3_bucket.spa_replica.bucket
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "owner_pubkey" {
  description = "Operator's registered owner pubkey (base64url) if set via terraform var."
  value       = var.owner_pubkey
  sensitive   = false
}
