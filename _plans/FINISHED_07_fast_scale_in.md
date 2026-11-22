# Plan 07: shorten the idle wait before the GPU scales in to zero

Canonical plan. Decided with the owner 2026-11-22: "shorten the idle time as much as possible". Status: FINISHED 2026-11-22. Applied 2026-11-22 (9 added, 1 changed, 0 destroyed) and measured the same day.

## Context

G7 measured on 2026-11-22 (g4dn, one `fast` job, no hand stop): the instance was gone about 40 minutes after the last upload.

| Stage | What | Measured |
|---|---|---|
| 1 | ECS service target tracking sets desired count 0 (needs 15 one-minute datapoints below target) | about 18 min |
| 2 | Capacity provider managed scaling sets the ASG desired to 0 (another 15 datapoints) | about 15.5 min |
| 3 | Instance leaves the ASG after desired 0 (protection clearing, drain) | about 6 min |

Spot price checked the same day (us-west-2): g4dn.2xlarge $0.29 to $0.32 an hour, g5.2xlarge $0.69 to $0.90. One idle window of 40 minutes costs about $0.20 on a g4dn and $0.46 to $0.60 on a g5 (my arithmetic), more than the clip. Plan 06 (not approved yet) would move the fleet to g5 and g6, which makes the wait dearer still.

## Decisions

1. **Stage 1 replaced by an own alarm.** The target-tracking policy `track_jobs` keeps scaling out but gets `disable_scale_in = true` (its scale-in alarm is fixed at 15 datapoints and cannot be tuned). A new CloudWatch alarm on metric math `visible + in-flight` (the same count the old policy used, so a running job never reads as idle) goes to ALARM when the sum is below 1 for `idle_minutes` one-minute periods. Its action is a step policy that sets the service desired count to exactly 0. New Terraform variable `idle_minutes`, default 3 (owner wants it as short as possible; raise it if jobs often come a few minutes apart).
2. **Missing data counts as idle** for that alarm. SQS stops publishing metrics for a long-idle queue, and "no data" then means "no work".
3. **Stage 2 and 3 replaced by a small Lambda** (`lambdas/scale_in/`), triggered by an EventBridge rule on ECS "Task State Change" with `lastStatus` STOPPED in this cluster. It acts only if all three hold: the queue has 0 visible and 0 in-flight messages; the service has desired, running and pending counts of 0. Then it clears scale-in protection on the ASG's instances and sets the ASG `DesiredCapacity` to 0. Otherwise it logs why and does nothing. It never changes `MinSize` or `MaxSize` (those belong to the budget cut-off, ADR 0004), and never raises anything.
4. **The capacity provider's own 15-minute scale-in stays** as the fallback, for example if an instance boots and never gets a task.
5. **Safety net unchanged:** ECS task scale-in protection keeps a task that is running a job alive against any service scale-in, so a wrong "idle" reading cannot kill a running job.
6. **Known race:** a job arriving in the seconds between the Lambda's checks and its ASG update. The wake alarm then raises the service to 1 and the capacity provider raises the ASG again (about 1 to 2 minutes of extra delay). Self-healing, not a lost job.
7. **Trade-off accepted:** a shorter warm window means a user who submits a second job more than a few minutes after the first waits the full cold start again (about 12 minutes). `idle_minutes` is the knob.
8. **No ADR:** easy to reverse (a Terraform change). The spec, CLAUDE.md and the artifact carry the result.

## Expected result (my estimate, to be measured)

Idle wait about `idle_minutes` (3) plus the Lambda and termination, so 5 to 8 minutes instead of 40. Per wake-up saving about $0.17 on a g4dn and $0.40 to $0.50 on a g5.

## Steps

1. [x] Plan written.
2. [x] Tests first for the Lambda (`tests/scale_in/`, moto): idle scales the ASG to 0 and clears protection; a visible message, an in-flight message, a service desired count above 0, or running tasks each leave everything alone; `MinSize` and `MaxSize` untouched; ASG already at 0 is a no-op; AWS errors are raised so the invocation is retried; the log line carries no ids.
3. [x] Lambda `lambdas/scale_in/handler.py` until green. Added during review: desired 0 with tasks still counted raises `TasksStillStopping`, so Lambda retries (the stopped event can beat the service counts by seconds). 13 tests, 307 in the suite.
4. [x] Terraform in `scaling.tf` and `variables.tf`: `idle_minutes` variable, `disable_scale_in` on `track_jobs`, the idle alarm and its step policy, the Lambda with its role, log group and EventBridge rule and permission. `terraform fmt` and `validate` offline only.
5. [x] Owner: `terraform plan`, review, `apply`. Claude never runs these.
6. [x] Owner: one measuring run (raise limits to 1, one `fast` job, `scripts/watch_scale_in.sh`, restore limits to 0). Expected cost about $0.12 on a g4dn (my estimate). If idle is still long, read the Lambda's log and the alarm history.
7. [x] Update the spec (idle bullet and scaling section), CLAUDE.md scaling chain, plan 01, effort log and the artifact with the measured number.

## Open (measure, do not guess)

- Whether ECS clears the instance protection by itself soon after the last task stops, or only on its own slow cycle (the 6-minute drain). The Lambda clears it explicitly, so the first measuring run shows whether that is enough.
- Whether `SetInstanceProtection` on an instance of an ECS-managed ASG is accepted. If AWS refuses, fall back to `TerminateInstanceInAutoScalingGroup` with `ShouldDecrementDesiredCapacity`.

## Found at the first plan (2026-11-22)

The owner's `terraform plan` showed 9 to add and 3 to change, not the 1 change expected. The extra changes were the launch template `image_id` (AWS published a newer recommended ECS GPU AMI in the SSM parameter) and the version bump rolling into the ASG. Not part of this plan, and an untested image would confound the idle measurement, since both measured runs used the old one. Fix: the launch template now has `ignore_changes = [image_id]` (comment in `compute.tf` says how to refresh deliberately). Re-plan expected: 9 to add, 1 to change (`track_jobs`), 0 to destroy.

## Result (measured 2026-11-22, g4dn, one `fast` job, no hand stop)

| Event | UTC | After the last upload |
|---|---|---|
| `uploaded clip` | 10:13:55 | 0 |
| `worker stopped` (idle alarm, sleep policy, service to 0) | 10:18:55 | 5.0 min (old: about 18) |
| Lambda decides `scaled_in` (first try, no retry), ASG desired 0 | 10:19:04 | 5.1 min |
| EC2 termination begins (billing stops) | 10:19:10 | 5.25 min |
| Instance gone from the ASG | 10:26:16 | 12.4 min (unbilled `shutting-down` tail) |

Billed idle about 5 min instead of about 33.5. The run billed about 22.7 minutes (launch 09:56:30, termination 10:19:10), about $0.11 on a g4dn (my arithmetic).

Two things the run settled. The 6 to 7 minute tail that I counted as "drain" in the first G7 measurement was the unbilled `shutting-down` phase: clearing the protection explicitly did not shorten it, and it does not need to. The first G7 figure of "about 40 minutes" was therefore about 33.5 billed minutes plus that tail.

Remaining time is mostly the alarm (3 datapoints plus the SQS metric lag). `idle_minutes` 2 would save about a minute (about $0.005 on a g4dn) at the price of a higher chance of a false idle reading, so it stays at 3.

Not exercised: a job arriving in the seconds between the Lambda's checks and its ASG update.
