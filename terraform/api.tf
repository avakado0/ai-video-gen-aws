# API (spec 3B): HTTP API with a Cognito JWT authorizer in front of one Lambda
# that will run the whole FastAPI app through Mangum. The Lambda has no function
# URL; API Gateway is the only way in. Source: lambdas/api/ (placeholder until
# the app is written).

data "archive_file" "api" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api"
  output_path = "${path.module}/.build/api.zip"
  excludes    = ["__init__.py", "__pycache__"]
}

resource "aws_cloudwatch_log_group" "api" {
  name              = local.api_log_group
  retention_in_days = 90
}

resource "aws_lambda_function" "api" {
  function_name = "aivideogen-api"
  role          = aws_iam_role.api.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 29 # API Gateway gives up at 30 s
  memory_size   = 512

  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256

  environment {
    variables = {
      CLIPS_BUCKET    = aws_s3_bucket.clips.bucket
      QUEUE_URL       = aws_sqs_queue.jobs.url
      MAX_ACTIVE_JOBS = "3"
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

resource "aws_apigatewayv2_api" "main" {
  name          = "aivideogen"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["https://${aws_cloudfront_distribution.web.domain_name}"]
    allow_methods = ["GET", "POST", "DELETE", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.main.id
  name             = "cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = "https://${aws_cognito_user_pool.main.endpoint}"
    audience = [aws_cognito_user_pool_client.web.id]
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

# Every path goes to the one Lambda, and every path needs a valid JWT.
resource "aws_apigatewayv2_route" "all" {
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = "ANY /{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  # A small brake on a runaway client; about 6 users need far less.
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}

# The Lambda's resource policy: API Gateway, and only this API, may invoke it.
resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}
