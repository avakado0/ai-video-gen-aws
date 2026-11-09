# TODO (human): AWS account setup before the first deploy

Account `477656043770`, region `us-west-2`. Claude must not do these (console, credentials, Support).

- [ ] Create the $20/month budget in the console (Billing → Budgets → Cost budget, monthly, fixed $20). Do this first.
  - Under "Budget scope" / advanced options, **uncheck "Credits"** so the budget counts gross spend. Otherwise the $100 credit hides the spend and the budget never trips. Verify the checkbox in the console.
  - Alert 1: 80% of actual, email to you.
  - Alert 2: 100% of actual, email to you. The SNS topic is added to this alert after the first `terraform apply` (the topic lives in Terraform; see spec section 5, "AWS Budgets alert and cut-off"). Leave a reminder here: `- [ ] Attach the Terraform SNS topic to the 100% alert`.
- [ ] Create IAM user `terraform-deployer`: no console access, policy AdministratorAccess, one access key. Save it with `aws configure --profile terraform-deployer` (it lands in `~/.aws/credentials`). Never paste the key into the repo or a chat.
- [ ] Keep `the-admin-user` console-only, with MFA and no access key.
- [ ] Add to the pending Support case: "On the Free plan, can I launch g4dn.2xlarge Spot instances in us-west-2, and what is my 'All G and VT Spot Instance Requests' quota?"
- [ ] Make a Kaggle account and verify your phone number (needed for GPU notebooks), for the GPU spike.
- [ ] Free up disk on your machine (at least 25 GB) before the first worker image build.

## Terraform state bucket (one-time, by hand)

Why by hand: Terraform cannot create the bucket that holds its own state, and a `bootstrap/` directory with its own local state is more moving parts than one person needs. The bucket is **never** managed by Terraform (same rule as the models bucket), so a `terraform destroy` cannot delete the state. Locking uses S3 itself (`use_lockfile`), so there is no DynamoDB table. Cost: pennies per month.

Do this in CloudShell (region us-west-2) as `the-admin-user`, before the first `terraform init`. Allowed on the Free plan, creates nothing that costs more than cents.

- [ ] Create the bucket and harden it:

```bash
export REGION=us-west-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export STATE_BUCKET=aivideogen-tfstate-$ACCOUNT_ID    # expect aivideogen-tfstate-477656043770

aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION"

aws s3api put-public-access-block --bucket "$STATE_BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# Versioning is the undo button for a corrupted or overwritten state file.
aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption --bucket "$STATE_BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# Keep old state versions 90 days, then drop them. Abort stuck uploads.
aws s3api put-bucket-lifecycle-configuration --bucket "$STATE_BUCKET" --lifecycle-configuration '{
  "Rules":[{"ID":"expire-old-state-versions","Status":"Enabled","Filter":{},
    "NoncurrentVersionExpiration":{"NoncurrentDays":90},
    "AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}'

aws s3api put-bucket-tagging --bucket "$STATE_BUCKET" \
  --tagging 'TagSet=[{Key=Project,Value=AIVideoGen},{Key=ManagedBy,Value=Manual}]'
```

- [ ] Verify (each should match the comment):

```bash
aws s3api get-bucket-versioning --bucket "$STATE_BUCKET"            # Status: Enabled
aws s3api get-public-access-block --bucket "$STATE_BUCKET"          # all four true
aws s3api get-bucket-encryption --bucket "$STATE_BUCKET"            # AES256
aws s3api get-bucket-location --bucket "$STATE_BUCKET"              # us-west-2
```

- [ ] Tell Claude the bucket name. `terraform/backend.tf` will then be:

```hcl
terraform {
  required_version = ">= 1.10"
  backend "s3" {
    bucket       = "aivideogen-tfstate-477656043770"
    key          = "aivideogen/terraform.tfstate"
    region       = "us-west-2"
    encrypt      = true
    use_lockfile = true   # S3-native lock, no DynamoDB
  }
}
```

  The bucket name is not a secret. Credentials come from the `terraform-deployer` profile, never from this file.

- [ ] Check the local Terraform is `>= 1.10` (`terraform version`). `use_lockfile` does not exist in older versions.

### If something goes wrong

- **"Error acquiring the state lock"** after a crashed run: confirm nobody else is applying, then `terraform force-unlock <LOCK_ID>` (the ID is in the error). The lock is a `.tflock` object next to the state file; it is safe to remove once you are sure no run is active.
- **Bad or lost state:** the bucket is versioned. List versions with `aws s3api list-object-versions --bucket "$STATE_BUCKET" --prefix aivideogen/terraform.tfstate`, then copy the last good version back with `aws s3api copy-object --copy-source "$STATE_BUCKET/aivideogen/terraform.tfstate?versionId=<ID>" --bucket "$STATE_BUCKET" --key aivideogen/terraform.tfstate`.
- **Bucket name taken:** S3 names are global. The account ID in the name makes a clash unlikely; if it happens, pick another name and change `backend.tf`.
- **Never** delete the bucket or disable versioning while a stack exists. Deleting it orphans every resource Terraform created.
