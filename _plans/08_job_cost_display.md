# Plan 08: per-job cost shown in the front end

Status: code written and tested offline (2026-11-23); not deployed. Decided in a grill session with the owner. Left to the owner: rebuild and push the worker image, `terraform apply`, one real job to check the number (see `human_todo/job_cost.md`).

## Context

The owner wants the UI to show the best-effort cost of each generated video. Spec 3H only attributes cost
per user per month, by hand (Logs Insights plus Cost Explorer). This plan adds a per-job number to the
job list without a database (ADR 0001) and without always-on resources.

It is an estimate, labelled as such. It is not the AWS bill.

## Decisions

1. **Scope of the number: GPU time only.** `gpu_seconds` x Spot price for the instance type. No share of
   cold boot, weight copy or idle (those are unknown when the clip is ready), and no egress (the clip is
   deleted after download). UI label says "GPU only".
2. **Price source: `ec2:DescribeSpotPriceHistory`** for the worker's instance type and availability
   zone (AZ from IMDS `placement/availability-zone`), time-weighted over the attempt. If the call fails,
   fall back to a per-type table from a Terraform variable (`spot_price_fallback`, env var on the task).
   A pricing failure never fails a job. Spot moved $0.69 to $0.90 an hour on 2026-11-22, so a fixed price
   alone could be about 30% off.
3. **Which attempts count: all of them.** Released and errored attempts burned GPU time too (spec 3H
   already charges them to the user). The UI also shows how many tries the job took.
4. **Storage: one write-only record per attempt**, `clips/{sub}/{job_id}.attempt-{n}.json` with
   `{gpu_seconds, cost_usd, outcome, instance_type}`; `n` is `ApproximateReceiveCount`. Written in the
   `finally` block of `Worker.handle` (`worker/runner.py`), next to the usage log, for the outcomes `ok`,
   `failed`, `released`, `error` and `gave_up`. Not for `stale` (no GPU used), `cancelled` (the job is gone)
   or `bad_message`.
   No read-modify-write, no new worker S3 permission (`WriteClips` already allows `PutObject` on
   `clips/*`). Rewriting `.pending` was rejected: a cancel during a release would be undone by the write.
5. **Which jobs show a cost: ready and failed.** Pending shows none (not final).
6. **Known gap, accepted:** an instance killed with no warning writes no record, so that attempt is not
   counted. No heartbeat write. Same limit as the usage log in 3H; document it in the spec.

## Changes (tests first, per CLAUDE.md)

### Worker
- New `worker/pricing.py`: `SpotPrices` class. `average_price(instance_type, az, start, end)` from
  `describe_spot_price_history` (time-weighted); fallback table on any error. Tests with a fake EC2
  client: normal case, a price change mid-job, API error uses fallback, unknown type with no fallback
  gives `cost_usd = None`.
- `worker/imds.py`: add `availability_zone()` next to `instance_type()` (line 52).
- `worker/store.py`: `put_attempt(sub, job_id, attempt, record)` next to `put_failed`.
- `worker/runner.py`: in the `finally` of `handle`, build the record and call `put_attempt`. A failure to
  write the record is logged and swallowed (it must not change the outcome or block the release).
  Inject the pricing object like `log_usage`. Tests in `tests/worker/test_runner.py` and
  `test_store.py`: a record is written for ok, failed, released, error, gave_up; none for stale, cancelled, `bad_message`; a write
  failure does not change the outcome.
- `worker/` entry/config: wire `SpotPrices`, the env var for the fallback table.

### API (`lambdas/api/`)
- `store.py`: `list_jobs` already ignores unknown extensions (`attempt-2.json` is not in `EXTENSIONS`),
  so grouping is untouched. Add `read_attempts(sub, job_id)`: list prefix `clips/{sub}/{job_id}.attempt-`
  and read each JSON (reuse `read_json`).
- `service.py` `_view` (line 67): for `ready` and `failed`, add `cost_usd` (sum, rounded to 4 places;
  `null` if any record has no price), `attempts` (count of records, or the highest `n` when higher) and
  `cost_partial` is not needed. `list_jobs` (line 80) keeps returning the light view; `GET /jobs/{id}`
  (line 88) carries the cost, as the front end already fetches details for non-pending jobs
  (`frontend/js/jobs.js:42`).
- `delete_job` (line 101): also delete the job's `attempt-*` keys (list the prefix, delete all).
  Cancelling a pending job goes through the same path.
- Tests in `tests/api/test_read_jobs.py`: cost summed over 3 attempts, attempts count, failed job has
  cost, pending has none, a record with `cost_usd: null` gives `null`, delete removes the records,
  another user's records are never read.

### Terraform (`terraform/`; checks are `terraform fmt -check` and `terraform validate` only)
- `iam.tf` task role: new statement `SpotPriceHistory`, `ec2:DescribeSpotPriceHistory`, resource `*`
  (the action does not support resource-level permissions). Read-only, free.
- `variables.tf`: `spot_price_fallback` (map of instance type to USD per hour, default
  `{ "g5.2xlarge" = 0.90 }`, the top of the observed range so the fallback leans high).
- `ecs.tf`: pass the map as a task env var.
- The 1-day lifecycle on `clips/` already covers orphaned attempt records. Check no lifecycle prefix
  excludes them.
- The user runs `terraform plan` / `apply`. Add a step to `human_todo/` with the profile reminder
  (`export AWS_PROFILE=terraform-deployer`, check account `477656043770`).

### Front end (vanilla JS, must run with no backend)
- `frontend/js/jobs.js`: under a ready or failed job show "~$0.07 · 2 tries · GPU only"; hide the cost
  when `cost_usd` is `null` (show only the tries). One try reads "1 try".
- Pure formatting function in a new small module (like `elapsed.js`), tested with `node --test`.
- `frontend/js/api/MockApiClient.js`: return `cost_usd` and `attempts` for mock ready and failed jobs
  (include a retried one and a `null` one). `RealApiClient.js` needs no change if the fields pass through.

### Docs (same commits as the code; one logical change per commit)
- `CONTEXT.md`: define "Job cost" (estimate, GPU time only, all attempts) and "Attempt record".
- Spec `docs/architecture/DRAFT_architecture.md`: 3H (new per-job path, the gap), the key layout
  (section 3C), `GET /jobs/{job_id}` response, worker `finally` block (section 4), task IAM.
- ADR: not needed. The choices are reversible and not surprising once the spec says why.
- `human_todo/update_diagram.md`: not needed (no new component).
- CLAUDE.md "Repo state": test count and plan list. Control-room artifact: update per its hourly rule.

## Out of scope
- Overhead share, egress, running figure on pending jobs, a heartbeat write, per-user totals in the UI.

## Verification
- `.venv/bin/pytest` (offline, moto): all new tests plus the existing 354 pass.
- `node --test frontend/js` for the formatter.
- `terraform fmt -check && terraform validate` in `terraform/`.
- Front end: `python3 -m http.server -d frontend 8000` with `"mock": true`; check a ready, a retried, a
  failed and a no-price job.
- After `apply` (user runs it): one real job on AWS; compare the shown cost with
  `gpu_seconds` in the `job_attempt` log line and the Spot price at that time.
