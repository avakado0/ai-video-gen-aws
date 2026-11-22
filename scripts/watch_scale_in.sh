#!/usr/bin/env bash
# Print one line a minute: queue, ECS service, ASG. Used to time the idle wait before scale-in (G7).
# Read-only: it only calls describe/get APIs. Stop with Ctrl-C.
#
#   export AWS_PROFILE=terraform-deployer
#   scripts/watch_scale_in.sh | tee /tmp/g7_watch.txt
set -euo pipefail

REGION=us-west-2
EXPECTED_ACCOUNT=477656043770

account=$(aws sts get-caller-identity --query Account --output text)
if [ "$account" != "$EXPECTED_ACCOUNT" ]; then
  echo "wrong account ($account). Run: export AWS_PROFILE=terraform-deployer" >&2
  exit 1
fi

queue_url=$(aws sqs get-queue-url --queue-name aivideogen-jobs --region "$REGION" --query QueueUrl --output text)

echo "time_utc | queue visible/in-flight | ecs desired/running | asg desired/instances"
while true; do
  q=$(aws sqs get-queue-attributes --queue-url "$queue_url" --region "$REGION" \
        --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
        --query 'Attributes.[ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible]' --output text | tr '\t' '/')
  e=$(aws ecs describe-services --cluster aivideogen --services aivideogen-worker --region "$REGION" \
        --query 'services[0].[desiredCount,runningCount]' --output text | tr '\t' '/')
  a=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names aivideogen-gpu --region "$REGION" \
        --query 'AutoScalingGroups[0].[DesiredCapacity,length(Instances)]' --output text | tr '\t' '/')
  echo "$(date -u +%H:%M:%S) | $q | $e | $a"
  sleep 60
done
