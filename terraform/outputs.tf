# Outputs, and the real config.json for the web app (spec 5).

locals {
  # Generated in memory and uploaded straight to S3. Never written to disk
  # (no local_file), so the committed mock frontend/config.json stays untouched.
  web_config = {
    mock       = false
    apiBaseUrl = trimsuffix(aws_apigatewayv2_api.main.api_endpoint, "/")
    cognito = {
      domain      = "${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
      clientId    = aws_cognito_user_pool_client.web.id
      redirectUri = local.web_url
    }
    pollSeconds = 3
  }
}

# Uploaded last: it needs every other front-end piece, and the site is not
# usable without it.
resource "aws_s3_object" "web_config" {
  bucket        = aws_s3_bucket.web.id
  key           = "config.json"
  content       = jsonencode(local.web_config)
  content_type  = "application/json"
  cache_control = "max-age=60"

  depends_on = [aws_s3_object.frontend]
}

output "web_url" {
  description = "Where the app is served."
  value       = local.web_url
}

output "api_url" {
  description = "API Gateway endpoint."
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "cognito_user_pool_id" {
  description = "User pool to create users in (console, admin-create-only)."
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "Public web app client ID (not a secret)."
  value       = aws_cognito_user_pool_client.web.id
}

output "clips_bucket" {
  value = aws_s3_bucket.clips.bucket
}

output "web_bucket" {
  value = aws_s3_bucket.web.bucket
}

output "queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "ecr_repository_url" {
  description = "Push the worker image here."
  value       = aws_ecr_repository.worker.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "asg_name" {
  description = "Raise its max size by hand in phase 7 (ADR 0004)."
  value       = aws_autoscaling_group.gpu.name
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "budget_alert_topic_arn" {
  description = "Attach this to the budget's 100% alert in the console."
  value       = aws_sns_topic.budget.arn
}
