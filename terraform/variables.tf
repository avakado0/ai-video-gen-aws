variable "region" {
  description = "AWS region for the whole stack."
  type        = string
  default     = "us-west-2"
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
