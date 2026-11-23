variable "region" {
  description = "AWS region for the whole stack."
  type        = string
  default     = "us-west-2"
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC. Two /24 public subnets are carved from it."
  type        = string
  default     = "10.0.0.0/16"
}

variable "alert_email" {
  description = "Owner email for DLQ alerts. Set it in a git-ignored terraform.tfvars or TF_VAR_alert_email, never in a committed file."
  type        = string
  sensitive   = true
}

variable "models_bucket_name" {
  description = "Name of the models bucket created by the runbook. Read-only here; Terraform never creates, changes or deletes it."
  type        = string
}

variable "spot_price_fallback" {
  description = "USD per hour by instance type, used for the per-job cost estimate only when the Spot price history cannot be read (plan 08). The default leans high: the top of the g5.2xlarge range seen in us-west-2 on 2026-11-22 ($0.69 to $0.90)."
  type        = map(number)
  default     = { "g5.2xlarge" = 0.90 }

  validation {
    condition     = alltrue([for price in values(var.spot_price_fallback) : price > 0])
    error_message = "Every fallback price must be above 0."
  }
}

variable "gpu_max" {
  description = "Initial ASG Max Size, the GPU cost ceiling. Leave at 0. The ASG ignores later changes to max_size (ADR 0004), so raise the live limit by CLI or console, not by editing this."
  type        = number
  default     = 0

  validation {
    condition     = var.gpu_max >= 0 && var.gpu_max <= 2 && floor(var.gpu_max) == var.gpu_max
    error_message = "gpu_max must be a whole number from 0 to 2."
  }
}

variable "idle_minutes" {
  description = "Minutes of an empty queue (nothing waiting, nothing running) before the worker service scales in to 0, which then takes the GPU instance down (plan 07). Measured 2026-11-22 with the old policies: about 40 minutes. A job that arrives later than this after the last one waits the full cold start (about 12 minutes) again."
  type        = number
  default     = 3

  validation {
    condition     = var.idle_minutes >= 1 && var.idle_minutes <= 15 && floor(var.idle_minutes) == var.idle_minutes
    error_message = "idle_minutes must be a whole number from 1 to 15."
  }
}

# Fixed names shared across files. Policies in iam.tf and budget.tf are scoped
# by these names rather than by resource references, because the ASG and the
# cluster depend on those roles and a reference would create a cycle.
locals {
  ecs_cluster_name      = "aivideogen"
  asg_name              = "aivideogen-gpu"
  ecs_service_name      = "aivideogen-worker"
  api_log_group         = "/aws/lambda/aivideogen-api"
  failure_log_group     = "/aws/lambda/aivideogen-failure-marker"
  budget_log_group      = "/aws/lambda/aivideogen-budget-cutoff"
  scale_in_log_group    = "/aws/lambda/aivideogen-scale-in"
  worker_log_group_name = "/aivideogen/worker"
}
