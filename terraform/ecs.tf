# ECS (spec 3E, 3H): cluster, worker log group, task definition and the saved
# per-user usage query. The service is added with scaling.tf, because it is
# the thing that scaling drives and it needs the capacity provider (compute.tf).

resource "aws_ecs_cluster" "main" {
  name = local.ecs_cluster_name
  # Container Insights stays off: it is billed per metric, and the scaling
  # chain does not use it (spec 3F).
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = local.worker_log_group_name
  retention_in_days = 90
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "aivideogen-worker"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  task_role_arn            = aws_iam_role.task.arn
  execution_role_arn       = aws_iam_role.task_execution.arn

  # The weights live on the instance's NVMe, which user_data mounts at /data.
  volume {
    name      = "data"
    host_path = "/data"
  }

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = "${aws_ecr_repository.worker.repository_url}:latest"
      essential = true

      # One job at a time, so the task takes the whole instance: a g4dn.2xlarge
      # or g5.2xlarge has 8 vCPU and 32 GB RAM, and the model needs about 32 GB
      # (spec 5). Leave a little for the OS and the ECS agent.
      cpu    = 7168
      memory = 29696

      resourceRequirements = [{ type = "GPU", value = "1" }]

      # 120 s is not enough to finish a job, so the worker aborts and releases
      # its message on SIGTERM instead (spec 4).
      stopTimeout = 120

      mountPoints = [{ sourceVolume = "data", containerPath = "/data", readOnly = false }]

      # PyTorch data loading needs more shared memory than Docker's 64 MB.
      linuxParameters = { sharedMemorySize = 4096 }

      environment = [
        { name = "QUEUE_URL", value = aws_sqs_queue.jobs.url },
        { name = "CLIPS_BUCKET", value = aws_s3_bucket.clips.bucket },
        { name = "MODELS_DIR", value = "/data/models" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "worker"
        }
      }
    }
  ])
}

# Per-user cost attribution (spec 3H): sums the worker's GPU seconds and the
# API's download bytes per user. Run it over one calendar month, then allocate
# the bill by hand. Free.
resource "aws_cloudwatch_query_definition" "usage_per_user" {
  name = "aivideogen/usage-per-user"

  log_group_names = [
    aws_cloudwatch_log_group.worker.name,
    aws_cloudwatch_log_group.api.name,
  ]

  query_string = <<-EOT
    fields @timestamp
    | filter event in ["job_attempt", "download", "inputs"]
    | stats sum(gpu_seconds) as gpu_seconds, sum(bytes) as bytes, count(*) as events by sub, instance_type
    | sort gpu_seconds desc
  EOT
}
