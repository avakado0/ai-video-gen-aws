# Failure-marker Lambda (spec 3D): consumes the DLQ and writes
# clips/{sub}/{job_id}.failed. Source and tests: lambdas/failure_marker/,
# tests/failure_marker/.

data "archive_file" "failure_marker" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/failure_marker"
  output_path = "${path.module}/.build/failure_marker.zip"
  excludes    = ["__init__.py", "__pycache__"]
}

# Declared here so it expires; Lambda's auto-created groups never do.
resource "aws_cloudwatch_log_group" "failure_marker" {
  name              = local.failure_log_group
  retention_in_days = 90
}

resource "aws_lambda_function" "failure_marker" {
  function_name = "aivideogen-failure-marker"
  role          = aws_iam_role.failure.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 10
  memory_size   = 128

  filename         = data.archive_file.failure_marker.output_path
  source_code_hash = data.archive_file.failure_marker.output_base64sha256

  environment {
    variables = {
      CLIPS_BUCKET = aws_s3_bucket.clips.bucket
    }
  }

  # The group must exist first, or Lambda would create an immortal one.
  depends_on = [aws_cloudwatch_log_group.failure_marker]
}

resource "aws_lambda_event_source_mapping" "failure_marker" {
  event_source_arn = aws_sqs_queue.dlq.arn
  function_name    = aws_lambda_function.failure_marker.arn
  batch_size       = 10

  # The handler reports only the records whose S3 write failed.
  function_response_types = ["ReportBatchItemFailures"]

  # Dead letters are rare; keep a bug from fanning out.
  scaling_config {
    maximum_concurrency = 2
  }

  # The role's inline policy must exist before the mapping validates access.
  depends_on = [aws_iam_role_policy.failure]
}
