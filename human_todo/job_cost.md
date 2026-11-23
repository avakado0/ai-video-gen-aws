# Per-job cost in the UI (yours)

Code, tests and docs are done (`_plans/08_job_cost_display.md`, spec 3H "Per-job cost estimate"). Nothing runs on AWS until you do these steps. Every `aws` or `terraform` step: first `export AWS_PROFILE=terraform-deployer` and check `aws sts get-caller-identity --query Account --output text` prints `477656043770`.

- [ ] Rebuild and push the worker image (it now writes the attempt records and reads the Spot price): `./scripts/build_push.sh` from the repo root. The script stops if the account is not `477656043770`.
- [ ] Rebuild the API Lambda zip: `./scripts/build_api.sh` (the API now sums the records and deletes them with the job).
- [ ] In `terraform/`: `terraform plan -out=tfplan`, paste the output for review, then `terraform apply tfplan`. Expected changes: the task role policy gains `ec2:DescribeSpotPriceHistory`, the task definition gains the `SPOT_PRICE_FALLBACK` env var, the API Lambda code changes, and the front end files are re-uploaded. `gpu_max` stays as it is. Nothing new that costs money.
- [ ] Run one real job (you start the GPU, so you watch the cost: a `fast` 3 s clip is about 7 minutes of `g5.2xlarge`). When it is ready, check the line under the clip reads like "~$0.07 · 1 try · GPU only". Compare with the `job_attempt` log line (`gpu_seconds`, `cost_usd`) in the worker log group and with the Spot price for the zone at that time (EC2 console, Spot Requests, Pricing history). They should agree to the cent.
- [ ] Optional: if the number is missing ("1 try" only), the price lookup failed and there was no fallback entry for the instance type. Look in the worker log for `spot price history failed`; the likely cause is the new IAM permission not yet applied.
