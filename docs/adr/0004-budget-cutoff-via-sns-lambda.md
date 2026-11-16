# Budget cut-off is an SNS-triggered Lambda that zeroes the ASG

When the monthly gross-spend budget reaches 100%, the alert publishes to an SNS topic and a small Lambda sets the ASG `MaxSize` and `DesiredCapacity` to 0. We rejected AWS Budgets actions: an IAM policy does not stop the ASG's service-linked role from launching instances, and SCPs need AWS Organizations, which would upgrade the account and expire the $100 credit for good.

## Consequences

- It is a brake, not a hard cap: Budgets data lags by hours, and running instances finish their current job.
- Terraform must `ignore_changes` on the ASG `max_size`, or the next `apply` undoes the cut-off. Re-enabling means hand-setting `gpu_max` back after reviewing the bill.
- The budget itself is created by hand in the console with credits excluded, so it still trips while the credit absorbs the bill. The topic, Lambda and role live in `budget.tf`.
