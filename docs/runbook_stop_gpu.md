# Runbook: stop the GPU now

Use this when a GPU job or instance looks stuck, runs too long, or you want to end a test early. It stops the worker service and the instance, so billing stops. The $20 budget alarm and the cut-off Lambda are the automatic brake; this is the manual one.

## Before you run anything

```
export AWS_PROFILE=terraform-deployer
aws sts get-caller-identity --query Account --output text   # must print 477656043770
```

Other AWS profiles exist on this machine. Check the account every time.

## Stop it now

Run these in order. They set the service's autoscaling max to 0, the service's desired count to 0, and the ASG to 0:

```
aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount --resource-id service/aivideogen/aivideogen-worker --min-capacity 0 --max-capacity 0 --region us-west-2
aws ecs update-service --cluster aivideogen --service aivideogen-worker --desired-count 0 --region us-west-2
aws autoscaling update-auto-scaling-group --auto-scaling-group-name aivideogen-gpu --min-size 0 --max-size 0 --desired-capacity 0 --region us-west-2
```

The cluster name is `aivideogen` (`terraform/variables.tf`). If in doubt: `aws ecs list-clusters --region us-west-2`.

Task scale-in protection and ECS managed termination protection delay the shutdown. Observed 2026-11-21 (idle instance, no job running): the instance stayed `running` for a few minutes after the three commands, then went `Terminating:Proceed` with protection cleared, with no manual step. Wait; check with `aws autoscaling describe-auto-scaling-instances --region us-west-2 --query 'AutoScalingInstances[].[LifecycleState,ProtectedFromScaleIn]' --output text`. Only if it is still `True` after about 10 minutes, remove it by hand: `aws autoscaling set-instance-protection --auto-scaling-group-name aivideogen-gpu --instance-ids <id> --no-protected-from-scale-in --region us-west-2`.

## If a job is running, the three commands do not stop it

Seen on 2026-11-22 (plan 06, trial 1): the worker turns on task scale-in protection while a job runs (up to 120 minutes), and ECS ignores a lowered desired count for a protected task. The three commands above stopped nothing mid-job: the job ran on, failed on its own six minutes later, and the worker took the same message again (attempt 3) before ECS stopped it. They only stop new work. To end a running job at once, stop the task directly (a direct stop is not scale-in; this has not been tried on a running job yet, so check the result):

```
aws ecs list-tasks --cluster aivideogen --service-name aivideogen-worker --region us-west-2 --query 'taskArns[]' --output text
aws ecs stop-task --cluster aivideogen --task <task-arn-from-above> --reason "manual stop" --region us-west-2
```

The worker gets SIGTERM, releases its message and exits. Run the three commands first, so nothing starts a new task, then the two above.

## Check that it is gone

```
aws ec2 describe-instances --region us-west-2 --filters Name=instance-state-name,Values=pending,running --query 'Reservations[].Instances[].[InstanceType,State.Name,LaunchTime]' --output text
```

An empty result means nothing is running.

## Restore afterwards

Terraform ignores the ASG `max_size` and the service autoscaling `max_capacity` (ADR 0004), so `terraform apply` does not undo the stop and does not restore it. Restore by hand, after you have looked at the bill. N is 1 while the owner is the only user, 2 later:

```
aws autoscaling update-auto-scaling-group --auto-scaling-group-name aivideogen-gpu --max-size N --region us-west-2
aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount --resource-id service/aivideogen/aivideogen-worker --min-capacity 0 --max-capacity N --region us-west-2
```

The service's desired count needs no restore: autoscaling sets it.

## Related

- `docs/adr/0004-budget-cutoff-via-sns-lambda.md`: how the automatic cut-off works and why `max_size` is ignored.
- `_plans/01_build_order.md`, phase 7: the go-live steps.
