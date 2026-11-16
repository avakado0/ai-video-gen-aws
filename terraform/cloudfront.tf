# Web app delivery (spec 3B): private S3 bucket behind CloudFront (origin access
# control), default *.cloudfront.net address, no custom domain.
#
# Dependency order, no cycle: web bucket -> distribution (needs only the bucket)
# -> everything that reads its domain_name (Cognito callback, API CORS, clips
# CORS below) -> bucket policy as a separate resource (needs the distribution
# ARN). config.json is uploaded last, from outputs.tf.

locals {
  # Every file in frontend/ except the committed mock config.json, so the mock
  # is never overwritten in S3. Terraform writes the real config.json itself.
  frontend_files = setsubtract(fileset("${path.module}/../frontend", "**"), ["config.json"])

  content_types = {
    html = "text/html; charset=utf-8"
    css  = "text/css; charset=utf-8"
    js   = "text/javascript; charset=utf-8"
    json = "application/json"
    mp4  = "video/mp4"
  }
}

resource "aws_s3_bucket" "web" {
  bucket        = "aivideogen-web-${local.account_id}"
  force_destroy = true # static files are rebuilt from frontend/ on every apply
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket = aws_s3_bucket.web.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "aivideogen-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# About 5 minutes, so an update shows without an invalidation.
resource "aws_cloudfront_cache_policy" "web" {
  name        = "aivideogen-web-5min"
  min_ttl     = 0
  default_ttl = 300
  max_ttl     = 300

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  comment             = "AIVideoGen web app"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id       = "web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = aws_cloudfront_cache_policy.web.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# Separate resource: it needs the distribution ARN, and the distribution needs
# the bucket. Only this distribution may read the bucket.
data "aws_iam_policy_document" "web_bucket" {
  statement {
    sid       = "AllowCloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.web]
}

resource "aws_s3_object" "frontend" {
  for_each = local.frontend_files

  bucket       = aws_s3_bucket.web.id
  key          = each.value
  source       = "${path.module}/../frontend/${each.value}"
  etag         = filemd5("${path.module}/../frontend/${each.value}")
  content_type = lookup(local.content_types, reverse(split(".", each.value))[0], "application/octet-stream")
}

# Clips bucket CORS (deferred from storage.tf): its allowed origin is this
# distribution. Browser POST uploads to inputs/, GET/HEAD for fetch downloads.
resource "aws_s3_bucket_cors_configuration" "clips" {
  bucket = aws_s3_bucket.clips.id

  cors_rule {
    allowed_origins = ["https://${aws_cloudfront_distribution.web.domain_name}"]
    allowed_methods = ["POST", "GET", "HEAD"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}
