# Messaging (spec 3D): job queue, dead-letter queue, DLQ alarm, alert topic.

resource "aws_sqs_queue" "dlq" {
  name = "aivideogen-jobs-dlq"

  # Dead-lettered messages keep their original enqueue time, so retention must
  # be at least the source queue's. 14 days is the maximum and is free.
  message_retention_seconds = 14 * 24 * 3600
  sqs_managed_sse_enabled   = true

  # Must be at least the failure Lambda's timeout (10 s), or the event source
  # mapping is rejected. A failed write is retried after this long.
  visibility_timeout_seconds = 60
}

resource "aws_sqs_queue" "jobs" {
  name = "aivideogen-jobs"

  message_retention_seconds  = 24 * 3600 # 1 day, the .pending marker's lifetime (spec 3D)
  visibility_timeout_seconds = 180       # 3 minutes; the worker heartbeat extends it
  sqs_managed_sse_enabled    = true

  # Every receive counts, including retries after a Spot release, so eight in a
  # row are needed to dead-letter a healthy job by mistake.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 8
  })
}

# Only the job queue may use this DLQ. Separate resource: the policy refers to
# the job queue, which already refers to the DLQ.
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.jobs.arn]
  })
}

# Owner alerts. The email subscription must be confirmed once from the email
# after the first apply.
resource "aws_sns_topic" "alerts" {
  name = "aivideogen-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# The failure-marker Lambda consumes the DLQ within seconds, so the visible
# count can fall back to 0 before CloudWatch samples it. Count receives
# instead: the Lambda polling a dead-lettered message registers as a receive,
# and a message the Lambda cannot process keeps being received.
resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "aivideogen-dlq-received"
  alarm_description   = "A job was dead-lettered (failed 8 times)."
  namespace           = "AWS/SQS"
  metric_name         = "NumberOfMessagesReceived"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}
