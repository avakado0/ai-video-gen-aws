# TODO (human): AWS account setup before the first deploy

Account `477656043770`, region `us-west-2`. Claude must not do these (console, credentials, Support).

- [ ] Create the $20/month budget alarm in the console (Billing → Budgets: cost budget, 80% of actual, email to you). Do this first.
- [ ] Create IAM user `terraform-deployer`: no console access, policy AdministratorAccess, one access key. Save it with `aws configure --profile terraform-deployer` (it lands in `~/.aws/credentials`). Never paste the key into the repo or a chat.
- [ ] Keep `the-admin-user` console-only, with MFA and no access key.
- [ ] Add to the pending Support case: "On the Free plan, can I launch g4dn.2xlarge Spot instances in us-west-2, and what is my 'All G and VT Spot Instance Requests' quota?"
- [ ] Make a Kaggle account and verify your phone number (needed for GPU notebooks), for the GPU spike.
- [ ] Free up disk on your machine (at least 25 GB) before the first worker image build.
