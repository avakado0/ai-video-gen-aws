# Budget cut-off is an SNS-triggered Lambda that zeroes the ASG

When the monthly gross-spend budget reaches 100%, the alert publishes to an SNS topic and a small Lambda sets the ASG `MaxSize` and `DesiredCapacity` to 0. We rejected AWS Budgets actions: an IAM policy does not stop the ASG's service-linked role from launching instances, and SCPs need AWS Organizations, which would upgrade the account and expire the $100 credit for good.

## Consequences

- It is a brake, not a hard cap: Budgets data lags by hours, and running instances finish their current job.
- Terraform must `ignore_changes` on the ASG `max_size`, or the next `apply` undoes the cut-off. `gpu_max` therefore only sets the ASG's *initial* `max_size` (decided 2026-11-16): raising or restoring the limit later is done by hand (`aws autoscaling update-auto-scaling-group --auto-scaling-group-name aivideogen-gpu --max-size N`, or the console), never by `terraform apply`. Re-enabling after a cut-off means doing that after reviewing the bill. We rejected dropping the ignore, because an unrelated `apply` would then silently re-enable GPUs after a cut-off.
- The budget itself is created by hand in the console with credits excluded, so it still trips while the credit absorbs the bill. The topic, Lambda and role live in `budget.tf`.
