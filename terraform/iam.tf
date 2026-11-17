# IAM (spec 3G). One role per principal, scoped to this stack's resources.
# The budget cut-off Lambda's role lives in budget.tf, next to its function.

data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  log_arn    = "arn:${data.aws_partition.current.partition}:logs:${var.region}:${local.account_id}:log-group"
}

# ---- Trust policies ----------------------------------------------------------

data "aws_iam_policy_document" "trust_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "trust_ecs_tasks" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

data "aws_iam_policy_document" "trust_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ---- EC2 instance profile (ECS agent + weights copy) ------------------------

resource "aws_iam_role" "instance" {
  name               = "aivideogen-instance"
  assume_role_policy = data.aws_iam_policy_document.trust_ec2.json
}

# Registers the instance with ECS, pulls images from ECR, ships agent logs.
resource "aws_iam_role_policy_attachment" "instance_ecs" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

# user_data copies models/<name>/ from the models bucket on every boot.
data "aws_iam_policy_document" "instance_models" {
  statement {
    sid       = "ReadModelWeights"
    actions   = ["s3:GetObject"]
    resources = ["${data.aws_s3_bucket.models.arn}/models/*"]
  }
  statement {
    sid       = "ListModelWeights"
    actions   = ["s3:ListBucket"]
    resources = [data.aws_s3_bucket.models.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["models/*"]
    }
  }
}

resource "aws_iam_role_policy" "instance_models" {
  name   = "read-models"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance_models.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "aivideogen-instance"
  role = aws_iam_role.instance.name
}

# ---- ECS task execution role (pull image, write logs) -----------------------

resource "aws_iam_role" "task_execution" {
  name               = "aivideogen-task-execution"
  assume_role_policy = data.aws_iam_policy_document.trust_ecs_tasks.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ---- ECS task role (the worker) ---------------------------------------------

resource "aws_iam_role" "task" {
  name               = "aivideogen-task"
  assume_role_policy = data.aws_iam_policy_document.trust_ecs_tasks.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid = "ConsumeJobs"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.jobs.arn]
  }
  statement {
    sid       = "WriteClips"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.clips.arn}/clips/*"]
  }
  # Stale-job check (spec 4): HEAD the .pending marker before GPU work.
  # ListBucket (below) makes a missing key a 404, not a 403. The worker treats
  # only 404 as "job gone"; a 403 is an error and the job is retried.
  statement {
    sid       = "HeadPendingMarkers"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.clips.arn}/clips/*.pending"]
  }
  # Unconditional, like the API role: a HEAD carries no s3:prefix, so a
  # prefix condition would never match and the 404 would stay a 403.
  statement {
    sid       = "ListClipsBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.clips.arn]
  }
  statement {
    sid       = "ReadAndDeleteInputs"
    actions   = ["s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.clips.arn}/inputs/*"]
  }
  statement {
    sid       = "ProtectOwnTaskFromScaleIn"
    actions   = ["ecs:UpdateTaskProtection"]
    resources = ["arn:${data.aws_partition.current.partition}:ecs:${var.region}:${local.account_id}:task/${local.ecs_cluster_name}/*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "worker"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ---- API Lambda role --------------------------------------------------------

resource "aws_iam_role" "api" {
  name               = "aivideogen-api"
  assume_role_policy = data.aws_iam_policy_document.trust_lambda.json
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "EnqueueJobs"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.jobs.arn]
  }
  statement {
    sid       = "ClipObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.clips.arn}/*"]
  }
  # Without ListBucket, a HEAD on a missing key returns 403 instead of 404, and
  # a pending job would look like an error.
  statement {
    sid       = "ListClipsBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.clips.arn]
  }
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.log_arn}:${local.api_log_group}:*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# ---- Failure-marker Lambda role ---------------------------------------------

resource "aws_iam_role" "failure" {
  name               = "aivideogen-failure-marker"
  assume_role_policy = data.aws_iam_policy_document.trust_lambda.json
}

data "aws_iam_policy_document" "failure" {
  # GetQueueAttributes is required by the SQS event source mapping.
  statement {
    sid = "ConsumeDeadLetters"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.dlq.arn]
  }
  statement {
    sid       = "WriteFailedMarkers"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.clips.arn}/clips/*"]
  }
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.log_arn}:${local.failure_log_group}:*"]
  }
}

resource "aws_iam_role_policy" "failure" {
  name   = "failure-marker"
  role   = aws_iam_role.failure.id
  policy = data.aws_iam_policy_document.failure.json
}
