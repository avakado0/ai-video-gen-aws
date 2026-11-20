#!/usr/bin/env bash
# Build the worker image and push it to ECR as :latest. Run it yourself; it spends no credit
# beyond ECR storage (a few GB, about $0.10 per GB-month).
#
#   export AWS_PROFILE=terraform-deployer
#   ./scripts/build_push.sh
#
# Needs Docker and about 25 GB of free disk. The new instance pulls :latest at its next boot.
set -euo pipefail

cd "$(dirname "$0")/.."

EXPECTED_ACCOUNT=477656043770
REGION=us-west-2

: "${AWS_PROFILE:?run: export AWS_PROFILE=terraform-deployer}"
account=$(aws sts get-caller-identity --query Account --output text)
if [ "$account" != "$EXPECTED_ACCOUNT" ]; then
  echo "AWS_PROFILE=$AWS_PROFILE is account $account, expected $EXPECTED_ACCOUNT. Stopping." >&2
  exit 1
fi

registry="$account.dkr.ecr.$REGION.amazonaws.com"
image="$registry/aivideogen-worker:latest"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry"
# The login token sits unencrypted in ~/.docker/config.json; remove it when the script ends, pass or fail.
trap 'docker logout "$registry" >/dev/null' EXIT
docker build --platform linux/amd64 -f worker/Dockerfile -t "$image" .
docker push "$image"

echo "pushed $image"
