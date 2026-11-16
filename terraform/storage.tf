# Storage (spec 3C). Two buckets: models (hand-made, read-only here) and clips.
# CORS on the clips bucket is added with cloudfront.tf, because its allowed
# origin is the CloudFront domain, which does not exist yet at this point.

data "aws_caller_identity" "current" {}

# Models bucket: created by hand from the runbook and never managed here.
# The lookup makes `terraform plan` fail early if the bucket is missing.
data "aws_s3_bucket" "models" {
  bucket = var.models_bucket_name
}

resource "aws_s3_bucket" "clips" {
  bucket = "aivideogen-clips-${data.aws_caller_identity.current.account_id}"

  # Everything in here expires within a day, so a destroy must not be blocked
  # by leftover clips.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "clips" {
  bucket = aws_s3_bucket.clips.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "clips" {
  bucket = aws_s3_bucket.clips.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Backstop only: the API deletes a clip once the download is confirmed and the
# worker deletes inputs after a successful job. 1 day is the S3 minimum and the
# rule runs asynchronously, so removal can lag. No transition rule: cheaper
# storage classes need 30+ days.
resource "aws_s3_bucket_lifecycle_configuration" "clips" {
  bucket = aws_s3_bucket.clips.id

  rule {
    id     = "expire-clips"
    status = "Enabled"

    filter {
      prefix = "clips/"
    }

    expiration {
      days = 1
    }
  }

  rule {
    id     = "expire-inputs"
    status = "Enabled"

    filter {
      prefix = "inputs/"
    }

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}
