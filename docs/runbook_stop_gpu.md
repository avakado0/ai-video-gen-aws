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

Task scale-in protection and ECS managed termination protection can delay the shutdown by a few minutes.

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
