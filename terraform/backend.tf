# The state bucket is created by hand once (human_todo/aws_setup.md) and is
# never managed by Terraform. Locking is S3-native, so there is no DynamoDB table.
terraform {
  backend "s3" {
    bucket       = "aivideogen-tfstate-477656043770"
    key          = "aivideogen/terraform.tfstate"
    region       = "us-west-2"
    encrypt      = true
    use_lockfile = true
  }
}
