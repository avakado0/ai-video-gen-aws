# Worker image repository (spec 3E). The image is built and pushed by hand
# with scripts/build_push.sh; there is no CI deploy for v1.

resource "aws_ecr_repository" "worker" {
  name                 = "aivideogen-worker"
  image_tag_mutability = "MUTABLE"

  # A destroy fails while images exist. That is deliberate: re-pushing a
  # 6-10 GB image is slow, so deleting it should be a conscious step.
  force_delete = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Storage is billed per GB-month and one image is several GB, so keep only the
# latest three images (enough to roll back) and drop untagged leftovers fast.
resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the latest 3 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 3
        }
        action = { type = "expire" }
      }
    ]
  })
}
