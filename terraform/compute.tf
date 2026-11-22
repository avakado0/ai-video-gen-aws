# Compute (spec 3E): launch template, Spot ASG and the ECS capacity provider.
# The whole stack deploys with gpu_max = 0, so nothing here can launch an
# instance until the Paid plan and the Spot quota are in place.

# Latest ECS-optimized GPU AMI (NVIDIA drivers and Docker runtime included).
# A newer recommended image would change the launch template; running instances keep
# theirs. The launch template ignores image_id changes (below): the image of the two measured
# runs (2026-11-21 and 2026-11-22) stays until a deliberate refresh, so an untested image never
# rides along with an unrelated apply (found 2026-11-22 during plan 07). To refresh: remove the
# ignore, plan, apply, boot one test job, then put it back.
data "aws_ssm_parameter" "ecs_gpu_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/gpu/recommended/image_id"
}

# No inbound rules. Outbound only: ECS, ECR, SQS, CloudWatch and S3.
resource "aws_security_group" "instance" {
  name        = "aivideogen-instance"
  description = "GPU worker instances: no inbound, all outbound"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_launch_template" "gpu" {
  name_prefix = "aivideogen-gpu-"
  image_id    = data.aws_ssm_parameter.ecs_gpu_ami.value

  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }

  # Public IP, no inbound rules (ADR 0002).
  network_interfaces {
    associate_public_ip_address = true
    security_groups             = [aws_security_group.instance.id]
  }

  # Hop limit 2 lets the worker container reach IMDS to poll for the Spot notice.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  # Root volume only: it holds the worker image (about 15-25 GB unpacked), not
  # the weights, which live on the NVMe instance store (ADR 0003).
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 60
      volume_type           = "gp3"
      delete_on_termination = true
      encrypted             = true
    }
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    models_bucket = data.aws_s3_bucket.models.id
    region        = var.region
    cluster_name  = local.ecs_cluster_name
  }))

  lifecycle {
    ignore_changes = [image_id]
  }
}

resource "aws_autoscaling_group" "gpu" {
  name                  = local.asg_name
  min_size              = 0
  max_size              = var.gpu_max
  vpc_zone_identifier   = [for s in aws_subnet.public : s.id]
  protect_from_scale_in = true # required for managed termination protection
  enabled_metrics       = ["GroupInServiceInstances"]
  metrics_granularity   = "1Minute"

  mixed_instances_policy {
    instances_distribution {
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = 0
      spot_allocation_strategy                 = "price-capacity-optimized"
    }

    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.gpu.id
        version            = aws_launch_template.gpu.latest_version
      }

      # Wan 2.2 5B needs a 24 GB GPU (plan 06), so the 16 GB T4 of the g4dn.2xlarge is out. The
      # g5.2xlarge has an A10G (24 GB), 8 vCPU and 32 GB RAM. The g6.2xlarge (L4, 24 GB, same
      # vCPU and RAM) was held back after trial 1 (2026-11-22): in the two zones the fleet uses
      # it costs 8 to 10% more than the g5 and is probably slower (unmeasured), and g5 capacity
      # never ran short. Add it as a second override if jobs start waiting for g5 capacity.
      # One type means no Spot diversity: if g5 capacity is short, jobs wait.
      override {
        instance_type = "g5.2xlarge"
      }
    }
  }

  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }

  # The budget cut-off Lambda sets max_size and desired_capacity to 0 (ADR 0004)
  # and the capacity provider manages the desired count. A later apply must not
  # undo either. Re-enable by hand-setting max_size after reviewing the bill.
  lifecycle {
    ignore_changes = [max_size, desired_capacity]
  }
}

resource "aws_ecs_capacity_provider" "gpu" {
  name = "aivideogen-gpu"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.gpu.arn
    managed_termination_protection = "ENABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 2
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = [aws_ecs_capacity_provider.gpu.name]

  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.gpu.name
    weight            = 1
  }
}
