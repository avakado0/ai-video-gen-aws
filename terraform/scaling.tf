# Scaling chain (spec 3F):
#   SQS depth -> CloudWatch -> ECS service desired count -> capacity provider
#   -> ASG -> Spot GPU instance.
# 0 -> 1 is a step policy on an alarm; 1 -> N is target tracking on metric math.
# Pure target tracking cannot wake from 0 (division by zero).
# Back to 0 (plan 07): an own alarm sets the service to 0 after `idle_minutes` of an empty queue
# (target tracking's scale-in always waits 15 one-minute datapoints, measured about 18 minutes),
# then a Lambda takes the instance down when the last task stops (the capacity provider alone
# waited about 15.5 minutes more, plus about 6 minutes to drain).

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  min_capacity       = 0
  # Follows the ASG limit: 1 while the owner is the only user (decided 2026-11-18), 2 when friends
  # join. The ASG's own max_size (gpu_max) is the real ceiling. Ignored on later applies (below),
  # so the live value is changed by hand, together with the ASG limit.
  max_capacity = 1

  # The budget cut-off sets this to 0 (ADR 0004); an apply must not undo it.
  # Restore by hand after reviewing the bill.
  lifecycle {
    ignore_changes = [max_capacity]
  }
}

# ---- 0 -> 1: wake up when a job is waiting ----------------------------------

resource "aws_appautoscaling_policy" "wake" {
  name               = "aivideogen-wake"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  resource_id        = aws_appautoscaling_target.worker.resource_id
  policy_type        = "StepScaling"

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_lower_bound = 0
      scaling_adjustment          = 1
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "jobs_waiting" {
  alarm_name          = "aivideogen-jobs-waiting"
  alarm_description   = "A job is waiting in the queue: wake the worker service."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.jobs.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_appautoscaling_policy.wake.arn]
}

# ---- 1 -> N and back down: jobs per GPU instance ----------------------------

# (visible + not visible) / instances in service. In-flight messages are not
# visible, so counting visible alone reads 0 mid-job and would scale the service
# to zero under a running worker. One task per instance, so the target is one
# job each. ECS RunningTaskCount is not used: it needs paid Container Insights.
resource "aws_appautoscaling_policy" "track_jobs" {
  name               = "aivideogen-jobs-per-instance"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  resource_id        = aws_appautoscaling_target.worker.resource_id
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    target_value       = 1
    scale_out_cooldown = 60
    # Scale-in is the sleep alarm's job (below): this policy's own scale-in cannot be shortened.
    disable_scale_in = true

    customized_metric_specification {
      metrics {
        id          = "visible"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesVisible"
            dimensions {
              name  = "QueueName"
              value = aws_sqs_queue.jobs.name
            }
          }
        }
      }

      metrics {
        id          = "inflight"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesNotVisible"
            dimensions {
              name  = "QueueName"
              value = aws_sqs_queue.jobs.name
            }
          }
        }
      }

      metrics {
        id          = "instances"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/AutoScaling"
            metric_name = "GroupInServiceInstances"
            dimensions {
              name  = "AutoScalingGroupName"
              value = local.asg_name
            }
          }
        }
      }

      metrics {
        id          = "jobs_per_instance"
        expression  = "(visible + inflight) / instances"
        label       = "Jobs per GPU instance"
        return_data = true
      }
    }
  }
}

# ---- N -> 0: sleep after `idle_minutes` of nothing to do --------------------------------

resource "aws_appautoscaling_policy" "sleep" {
  name               = "aivideogen-sleep"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  resource_id        = aws_appautoscaling_target.worker.resource_id
  policy_type        = "StepScaling"

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = 0
    }
  }
}

# visible + in-flight, as the target tracking policy counted. A running job is in flight, so it
# never reads as idle. Missing data counts as idle: SQS stops publishing metrics for a queue that
# has been quiet for a long time. A task running a job is also protected from scale-in (ECS task
# scale-in protection), so a wrong reading cannot stop a job.
resource "aws_cloudwatch_metric_alarm" "queue_idle" {
  alarm_name          = "aivideogen-queue-idle"
  alarm_description   = "Nothing waiting and nothing running for idle_minutes: put the worker service to sleep."
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  evaluation_periods  = var.idle_minutes
  datapoints_to_alarm = var.idle_minutes
  treat_missing_data  = "breaching"

  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      dimensions  = { QueueName = aws_sqs_queue.jobs.name }
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id = "inflight"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      dimensions  = { QueueName = aws_sqs_queue.jobs.name }
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id          = "busy"
    expression  = "visible + inflight"
    label       = "Jobs waiting or running"
    return_data = true
  }

  alarm_actions = [aws_appautoscaling_policy.sleep.arn]
}

# ---- Instance down as soon as the last task stops (plan 07) ------------------------------
# The capacity provider would wait 15 more minutes, then the instance drains for about 6. This
# Lambda checks that the queue and the service are idle, clears scale-in protection and sets the
# ASG's desired capacity to 0. It never changes min or max size (the budget cut-off owns those).
# The capacity provider's own scale-in stays as the fallback.

resource "aws_iam_role" "scale_in" {
  name               = "aivideogen-scale-in"
  assume_role_policy = data.aws_iam_policy_document.trust_lambda.json
}

data "aws_iam_policy_document" "scale_in" {
  statement {
    sid       = "ReadTheQueueDepth"
    actions   = ["sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.jobs.arn]
  }
  statement {
    sid       = "ReadTheWorkerService"
    actions   = ["ecs:DescribeServices"]
    resources = ["arn:${data.aws_partition.current.partition}:ecs:${var.region}:${local.account_id}:service/${local.ecs_cluster_name}/${local.ecs_service_name}"]
  }
  # DescribeAutoScalingGroups does not support resource-level permissions.
  statement {
    sid       = "ReadTheGpuGroup"
    actions   = ["autoscaling:DescribeAutoScalingGroups"]
    resources = ["*"]
  }
  statement {
    sid       = "TakeTheGpuGroupDown"
    actions   = ["autoscaling:UpdateAutoScalingGroup", "autoscaling:SetInstanceProtection"]
    resources = ["arn:${data.aws_partition.current.partition}:autoscaling:${var.region}:${local.account_id}:autoScalingGroup:*:autoScalingGroupName/${local.asg_name}"]
  }
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.log_arn}:${local.scale_in_log_group}:*"]
  }
}

resource "aws_iam_role_policy" "scale_in" {
  name   = "scale-in"
  role   = aws_iam_role.scale_in.id
  policy = data.aws_iam_policy_document.scale_in.json
}

data "archive_file" "scale_in" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/scale_in"
  output_path = "${path.module}/.build/scale_in.zip"
  excludes    = ["__init__.py", "__pycache__"]
}

resource "aws_cloudwatch_log_group" "scale_in" {
  name              = local.scale_in_log_group
  retention_in_days = 90
}

resource "aws_lambda_function" "scale_in" {
  function_name = "aivideogen-scale-in"
  role          = aws_iam_role.scale_in.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.scale_in.output_path
  source_code_hash = data.archive_file.scale_in.output_base64sha256

  environment {
    variables = {
      ASG_NAME    = local.asg_name
      ECS_CLUSTER = local.ecs_cluster_name
      ECS_SERVICE = local.ecs_service_name
      QUEUE_NAME  = aws_sqs_queue.jobs.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.scale_in, aws_iam_role_policy.scale_in]
}

resource "aws_cloudwatch_event_rule" "worker_task_stopped" {
  name        = "aivideogen-worker-task-stopped"
  description = "A task in the GPU cluster stopped: check whether the instance can go."
  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.main.arn]
      lastStatus = ["STOPPED"]
    }
  })
}

resource "aws_cloudwatch_event_target" "scale_in" {
  rule = aws_cloudwatch_event_rule.worker_task_stopped.name
  arn  = aws_lambda_function.scale_in.arn
}

resource "aws_lambda_permission" "scale_in_from_events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scale_in.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.worker_task_stopped.arn
}
