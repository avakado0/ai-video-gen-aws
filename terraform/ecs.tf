# ECS (spec 3E, 3H): cluster, worker log group, task definition, service and
# the saved per-user usage query.

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

      # One job at a time, so the task takes the whole instance: a g5.2xlarge (and the
      # g6.2xlarge to come) has 8 vCPU and 32 GB RAM. Trial 1 (plan 06, 2026-11-22): with the transformer,
      # VAE and prompt encoder all kept in RAM (about 24 GB of weights, load peak 27.5 to 28.5 GiB) the
      # container was OOM-killed (exit 137) at this 29 GiB limit while denoising a second job. The
      # worker now loads the prompt encoder per job and drops it (worker/wan22.py), which takes about
      # 11 GB out of the long-lived footprint (my estimate; the trial shows the real figure).
      # Leave a little for the OS and the ECS agent.
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
        # boto3 needs a region and a bridge-mode container has none by default.
        { name = "AWS_DEFAULT_REGION", value = var.region },
        # One source of truth for the model's inputs and params (spec 3B); the worker re-checks
        # every job against it. Re-encoded to one line, same as the API Lambda gets it.
        { name = "MODEL_MANIFEST", value = jsonencode(jsondecode(file("${path.module}/../model_manifest.json"))) },
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
    | stats sum(gpu_seconds) as gpu_seconds, sum(bytes) as bytes, count(*) as events by sub, instance_type, quality
    | sort gpu_seconds desc
  EOT
}

# One task per GPU instance. desired_count starts at 0 and is driven entirely by
# the scaling policies in scaling.tf, so Terraform must not reset it.
resource "aws_ecs_service" "worker" {
  name            = local.ecs_service_name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 0

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.gpu.name
    weight            = 1
  }

  # A new task definition replaces the task in place: one task fills the whole
  # instance, so the new one cannot start until the old one stops.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_ecs_cluster_capacity_providers.main]
}
