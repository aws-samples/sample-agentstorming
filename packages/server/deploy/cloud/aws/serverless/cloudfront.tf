# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_cloudfront_origin_access_control" "spa" {
  name                              = "${local.name}-spa-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "spa_router" {
  name    = "${local.name}-spa-router"
  runtime = "cloudfront-js-2.0"
  comment = "SPA client-side routing"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var req = event.request;
      var uri = req.uri;
      if (uri.indexOf('/v1/') === 0 || uri === '/healthz' || uri === '/readyz') {
        return req;  // API paths go to the ALB origin.
      }
      if (!uri.includes('.') && uri !== '/') {
        req.uri = '/index.html';
      }
      return req;
    }
  EOT
}

# Managed origin-request policy that forwards the key headers needed
# for SSE + signed POST: Authorization, Accept (text/event-stream),
# Last-Event-ID, and Idempotency-Key.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

# The security-headers policy the v3 audit deferred. Managed policy
# "SecurityHeadersPolicy" sets HSTS, X-Content-Type-Options, X-Frame-Options,
# Referrer-Policy and a CSP baseline on every response — the SPA holds the
# participant's Ed25519 private key in IndexedDB, so clickjacking and
# MIME-sniffing protections are not cosmetic here.
data "aws_cloudfront_response_headers_policy" "security_headers" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_distribution" "spa" {
  enabled             = true
  default_root_object = "index.html"
  is_ipv6_enabled     = true

  web_acl_id = aws_wafv2_web_acl.cloudfront.arn

  # SPA origin (static files in S3, private + OAC).
  origin {
    domain_name              = aws_s3_bucket.spa.bucket_regional_domain_name
    origin_id                = "spa"
    origin_access_control_id = aws_cloudfront_origin_access_control.spa.id
  }

  # Failover origin: the replicated SPA bucket in `replica_region`. This is a
  # real second copy of the bundle, kept current by S3 replication, not a
  # nominal second origin added to satisfy a rule.
  origin {
    domain_name              = aws_s3_bucket.spa_replica.bucket_regional_domain_name
    origin_id                = "spa-failover"
    origin_access_control_id = aws_cloudfront_origin_access_control.spa.id
  }

  origin_group {
    origin_id = "spa-group"

    failover_criteria {
      # 404 is deliberately absent: a missing SPA route is a legitimate 404
      # from the primary, and failing over on it would mask real deploy gaps
      # by silently serving the replica's older bundle.
      status_codes = [403, 500, 502, 503, 504]
    }

    member {
      origin_id = "spa"
    }
    member {
      origin_id = "spa-failover"
    }
  }

  # API origin (ALB). Viewer connects to CloudFront over HTTPS; CloudFront
  # connects to the ALB over HTTPS only — the ALB has no plaintext listener.
  origin {
    domain_name = aws_lb.main.dns_name
    origin_id   = "api"

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "https-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }

  logging_config {
    bucket          = aws_s3_bucket.logs.bucket_domain_name
    prefix          = "cloudfront/"
    include_cookies = false
  }

  default_cache_behavior {
    target_origin_id           = "spa-group"
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_router.arn
    }

    min_ttl     = 0
    default_ttl = 300
    max_ttl     = 86400
  }

  # SSE stream + POST endpoints — NO caching, forward auth headers.
  ordered_cache_behavior {
    path_pattern           = "/v1/*"
    target_origin_id       = "api"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = false # NEVER gzip SSE

    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  # Health endpoints.
  ordered_cache_behavior {
    path_pattern             = "/healthz"
    target_origin_id         = "api"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  ordered_cache_behavior {
    path_pattern             = "/readyz"
    target_origin_id         = "api"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  # Geo restriction defaults to denying the four comprehensively sanctioned
  # jurisdictions rather than to "none".
  #
  # The previous version disabled it outright, arguing that rooms are
  # multi-institution and international so restricting geography contradicts
  # the protocol's purpose. The first half is true; the conclusion was not. A
  # denylist of jurisdictions an AWS customer may not serve at all is not a
  # limit on legitimate international participation — it is the same list the
  # deployer is already obliged to honour. An allowlist would contradict the
  # purpose, and is available via `geo_restriction_type = "whitelist"` for
  # deployments whose compliance regime requires one.
  restrictions {
    geo_restriction {
      restriction_type = var.geo_restriction_type
      locations        = var.geo_restriction_type == "none" ? [] : var.geo_restriction_locations
    }
  }

  aliases = var.cloudfront_aliases

  # Unconditional, because the certificate is required. The previous version
  # made every field conditional on a certificate being supplied, which meant
  # the branch AWS ignores minimum_protocol_version on was also the default.
  viewer_certificate {
    acm_certificate_arn      = var.cloudfront_certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

resource "aws_s3_bucket_policy" "spa" {
  bucket = aws_s3_bucket.spa.id
  policy = data.aws_iam_policy_document.spa_bucket.json
}

data "aws_iam_policy_document" "spa_bucket" {
  statement {
    sid    = "DenyNonTlsRequests"
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.spa.arn, "${aws_s3_bucket.spa.arn}/*"]
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
    resources = ["${aws_s3_bucket.spa.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.spa.arn]
    }
  }
}
