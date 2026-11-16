# Budget cut-off (spec 5, ADR 0004). The budget itself is made by hand in the
# console (gross spend, credits excluded). Its 100% alert publishes to the topic
# below, and the Lambda zeroes the GPU ASG. Source: lambdas/budget_cutoff/.

resource "aws_sns_topic" "budget" {
  name = "aivideogen-budget-alert"
}

data "aws_iam_policy_document" "budget_topic" {
  statement {
    sid       = "AllowBudgetsToPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.budget.arn]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:budgets::${local.account_id}:*"]
    }
  }
}

resource "aws_sns_topic_policy" "budget" {
  arn    = aws_sns_topic.budget.arn
  policy = data.aws_iam_policy_document.budget_topic.json
}

# ---- Role: update this stack's ASG only, plus logs ---------------------------

resource "aws_iam_role" "budget" {
  name               = "aivideogen-budget-cutoff"
  assume_role_policy = data.aws_iam_policy_document.trust_lambda.json
}

data "aws_iam_policy_document" "budget" {
  # Scoped by name, not by reference: the ASG is written later and must not
  # depend on this role.
  statement {
    sid       = "ZeroTheGpuGroup"
    actions   = ["autoscaling:UpdateAutoScalingGroup"]
    resources = ["arn:${data.aws_partition.current.partition}:autoscaling:${var.region}:${local.account_id}:autoScalingGroup:*:autoScalingGroupName/${local.asg_name}"]
  }
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.log_arn}:${local.budget_log_group}:*"]
  }
}

resource "aws_iam_role_policy" "budget" {
  name   = "budget-cutoff"
  role   = aws_iam_role.budget.id
  policy = data.aws_iam_policy_document.budget.json
}

# ---- Function ---------------------------------------------------------------

data "archive_file" "budget_cutoff" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/budget_cutoff"
  output_path = "${path.module}/.build/budget_cutoff.zip"
  excludes    = ["__init__.py", "__pycache__"]
}

resource "aws_cloudwatch_log_group" "budget_cutoff" {
  name              = local.budget_log_group
  retention_in_days = 90
}

resource "aws_lambda_function" "budget_cutoff" {
  function_name = "aivideogen-budget-cutoff"
  role          = aws_iam_role.budget.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.budget_cutoff.output_path
  source_code_hash = data.archive_file.budget_cutoff.output_base64sha256

  environment {
    variables = {
      ASG_NAME = local.asg_name
    }
  }

  depends_on = [aws_cloudwatch_log_group.budget_cutoff, aws_iam_role_policy.budget]
}

resource "aws_lambda_permission" "budget_from_sns" {
  statement_id  = "AllowSnsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.budget_cutoff.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.budget.arn
}

resource "aws_sns_topic_subscription" "budget_cutoff" {
  topic_arn = aws_sns_topic.budget.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.budget_cutoff.arn

  depends_on = [aws_lambda_permission.budget_from_sns]
}
