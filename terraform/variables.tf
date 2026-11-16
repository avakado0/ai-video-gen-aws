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

variable "gpu_max" {
  description = "ASG Max Size, the GPU cost ceiling. 0 until the Paid plan and the G/VT Spot quota are in place; 2 in production."
  type        = number
  default     = 0

  validation {
    condition     = var.gpu_max >= 0 && var.gpu_max <= 2 && floor(var.gpu_max) == var.gpu_max
    error_message = "gpu_max must be a whole number from 0 to 2."
  }
}

# Fixed names shared across files. Policies in iam.tf and budget.tf are scoped
# by these names rather than by resource references, because the ASG and the
# cluster depend on those roles and a reference would create a cycle.
locals {
  ecs_cluster_name      = "aivideogen"
  asg_name              = "aivideogen-gpu"
  api_log_group         = "/aws/lambda/aivideogen-api"
  failure_log_group     = "/aws/lambda/aivideogen-failure-marker"
  budget_log_group      = "/aws/lambda/aivideogen-budget-cutoff"
  worker_log_group_name = "/aivideogen/worker"
}
