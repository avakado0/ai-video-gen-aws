# Scaling chain (spec 3F):
#   SQS depth -> CloudWatch -> ECS service desired count -> capacity provider
#   -> ASG -> Spot GPU instance.
# 0 -> 1 is a step policy on an alarm; 1 -> N is target tracking on metric math.
# Pure target tracking cannot wake from 0 (division by zero).

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  min_capacity       = 0
  max_capacity       = 2 # the ASG's own max_size (gpu_max) is the real ceiling

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
    scale_in_cooldown  = 300

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
