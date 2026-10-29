# Step-by-Step Guide: Migrating Model Weights from Hugging Face to Amazon S3

This document outlines the operational instructions to download your model weights from Hugging Face directly into your Amazon S3 model bucket with **zero inbound data transfer fees**.

---

## Prerequisites

* An AWS account, and AWS CLI credentials on your own machine for the bucket and role setup in Step 1 (or do those steps in the AWS console).
* A Hugging Face account (and an Access Token if downloading private or gated models like Llama or Stable Diffusion variants). For gated models, also accept the model's license on its Hugging Face page in the browser first. A token alone returns 403 until you do.
* A temporary compute instance (an `m5.xlarge` running for under an hour costs well under a dollar).

### Variables used below

Set these once in your local shell. Keep them the same across all steps.

```bash
export REGION=us-west-2
export BUCKET=aivideogen-models-610588468668   # S3 names are global; pick a unique one and use it everywhere
export MODEL=wan2-1-t2v-1-3b                        # must match models/<model-name>/ in user_data and model_manifest.json
export HF_REPO=Wan-AI/Wan2.1-T2V-1.3B-Diffusers
export HF_SHA=0fad780a534b6463e45facd96134c9f345acfa5b
export MY_IP=$(curl -s https://checkip.amazonaws.com)
```

---

## Step 1: Bucket, Role, Key, and Instance

### 1a. Create the models bucket

This is the bucket the production workers read from. Run from your own machine (or use the S3 console with the same settings). It is private, with public access blocked. Versioning stays off: it would only add storage cost for this bucket.

```bash
aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION"

aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

When `terraform/storage.tf` is written later, import this bucket into Terraform instead of creating a second one.

### 1b. Create the temporary instance's IAM role

The instance uses this role for S3 access, so no AWS keys are stored on it. The policy is limited to the `models/` prefix.

```bash
cat > /tmp/hf-trust.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF

cat > /tmp/hf-policy.json <<EOF
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::$BUCKET"},
  {"Effect":"Allow","Action":["s3:PutObject","s3:GetObject","s3:AbortMultipartUpload"],"Resource":"arn:aws:s3:::$BUCKET/models/*"}
]}
EOF

aws iam create-role --role-name hf-migration-role --assume-role-policy-document file:///tmp/hf-trust.json
aws iam put-role-policy --role-name hf-migration-role --policy-name hf-to-s3 --policy-document file:///tmp/hf-policy.json
aws iam create-instance-profile --instance-profile-name hf-migration-role
aws iam add-role-to-instance-profile --instance-profile-name hf-migration-role --role-name hf-migration-role
```

Delete the role and instance profile after the migration is complete (see Step 4).

### 1c. Create an SSH key and a security group

Inbound SSH only from your IP. No other inbound rules are needed. The default VPC's subnets are public.

```bash
aws ec2 create-key-pair --region "$REGION" --key-name hf-migration \
  --query KeyMaterial --output text > ~/.ssh/hf-migration.pem
chmod 400 ~/.ssh/hf-migration.pem

VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SG_ID=$(aws ec2 create-security-group --region "$REGION" --group-name hf-migration-ssh \
  --description "SSH from my IP for HF migration" --vpc-id "$VPC_ID" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
  --protocol tcp --port 22 --cidr "$MY_IP/32"
```

### 1d. Launch the instance

* **Region:** `us-west-2`, the same region as the bucket. The region must match exactly: a cross-region copy is billed as data transfer.
* **Instance type:** `m5.xlarge` (or `c5.xlarge`). It has up to 10 Gbps network, so a 29 GB model copies in minutes. A `t3.medium` has only about 0.25 Gbps baseline (about 30 MB/s).
* **AMI:** **Amazon Linux 2023**. Do not use Amazon Linux 2: its `python3-pip` is Python 3.7, which installs an ancient `huggingface_hub` that may have no `hf` command.
* **Extra EBS volume:** 40 GB `gp3`, which is the model (about 29 GB) plus headroom. The production cap is 225 GB, and this volume is only for this temporary instance. Production workers keep weights on the instance store, not EBS. **Delete on termination must be on**, or the volume survives Step 4 as a billed orphan.
* **Shutdown behavior:** terminate. This makes `sudo shutdown -h now` at the end of Step 3 also end billing.

```bash
AMI_ID=$(aws ssm get-parameter --region "$REGION" \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)

INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI_ID" --instance-type m5.xlarge --count 1 \
  --key-name hf-migration --security-group-ids "$SG_ID" \
  --iam-instance-profile Name=hf-migration-role \
  --instance-initiated-shutdown-behavior terminate \
  --block-device-mappings '[{"DeviceName":"/dev/sdf","Ebs":{"VolumeSize":40,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --query 'Instances[0].InstanceId' --output text)

aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
```

Then SSH in as `ec2-user`, using the public IP printed above:

```bash
ssh -i ~/.ssh/hf-migration.pem ec2-user@<public-ip>
```

Remaining steps run on the instance, unless marked otherwise.

---

## Step 2: Format and Mount the Extra Volume

Check the device name with `lsblk`. It is often `/dev/nvme1n1`. The 40 GB volume will show no mount point and no filesystem. Confirm both before formatting: formatting the wrong device destroys data.

```bash
lsblk
sudo file -s /dev/nvme1n1        # must print "data", not a filesystem name
sudo mkfs -t xfs /dev/nvme1n1
sudo mkdir -p /data
sudo mount /dev/nvme1n1 /data
sudo chown ec2-user:ec2-user /data
```

---

## Step 3: Install Dependencies, Download, and Upload

### 3a. Install tools

Install `tmux` and the Hugging Face CLI. Skip `yum update`: it is slow and pointless for a one-hour instance.

```bash
sudo yum install python3-pip tmux -y

# Install huggingface_hub with CLI support
pip install -U "huggingface_hub[cli]"
```

If `hf: command not found`, the user-level pip bin directory is not on your `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

For gated models only, log in to Hugging Face with your token (the license must already be accepted on the model page; see Prerequisites). Wan 2.1 is ungated, so skip this for v1:

```bash
hf auth login
```

### 3b. Start a tmux session

Run everything below inside `tmux`, so an SSH disconnect does not kill the download or upload. If you get disconnected, SSH back in and run `tmux attach -t migrate`.

```bash
tmux new -s migrate
```

Set the variables again in the new shell (they are not inherited from your laptop):

```bash
export REGION=us-west-2
export BUCKET=aivideogen-models-610588468668   # same value as Step 1
export MODEL=wan2-1-t2v-1-3b
export HF_REPO=Wan-AI/Wan2.1-T2V-1.3B-Diffusers
export HF_SHA=0fad780a534b6463e45facd96134c9f345acfa5b
```

### 3c. Download

The download goes to `/data`, which is the 40 GB volume from Step 2.

Download only the files the worker needs. Many repos ship the same weights in several formats (`.safetensors` plus `.bin`, `.onnx`, or fp32 and fp16 variants). Every unneeded GB costs S3 storage and adds copy time to every Spot cold boot, and it eats into the 225 GB instance-store cap. Check the repo's file list on Hugging Face first, then filter with `--include` (or `--exclude`).

Pin the exact commit with `--revision`, so the weights in S3 are reproducible even if the repo is updated later. The commit SHA is in the repo's "History" tab on Hugging Face.

The v1 model is **Wan 2.1 T2V 1.3B in Diffusers format**, repo `Wan-AI/Wan2.1-T2V-1.3B-Diffusers`, pinned to commit `0fad780a534b6463e45facd96134c9f345acfa5b` (ungated, Apache-2.0, about 29 GB total). The worker loads it with `diffusers.WanPipeline`, which needs this layout (`model_index.json` plus `text_encoder/`, `tokenizer/`, `transformer/`, `vae/`, `scheduler/` folders). The original `Wan-AI/Wan2.1-T2V-1.3B` repo uses a different layout and does not load in `diffusers`. Everything is `.safetensors` or small config files. The UMT5 text encoder is stored in fp32 (about 22.7 GB), which is why this repo is larger than the original. The filter below excludes only the docs and sample assets, about 7 MB of extras.

```bash
hf download "$HF_REPO" \
  --revision "$HF_SHA" \
  --exclude "assets/*" "examples/*" "*.md" "*.txt" \
  --local-dir /data/model
```

For a different model, change the repo ID, SHA and exclude patterns, and re-check the file list.

### 3d. Upload

Tune the AWS CLI for large files. The defaults (10 concurrent requests, 8 MB chunks) are conservative. The CLI uses the instance role from Step 1, so no keys are needed:

```bash
aws configure set default.s3.max_concurrent_requests 32
aws configure set default.s3.multipart_chunksize 64MB
```

Upload with `aws s3 sync`, which uploads in parallel with multipart, retries failures, and can be re-run to resume. The prefix must be `models/<model-name>/`, the path the workers' `user_data` copies from. `hf download` with `--local-dir` keeps metadata in `.cache/`, so exclude it:

```bash
aws s3 sync /data/model "s3://$BUCKET/models/$MODEL/" --exclude ".cache/*"
```

If it is interrupted, run the same command again; files already uploaded are skipped.

### 3e. Verify

Compare byte counts between the local copy and the bucket. They must match exactly:

```bash
# Local total, excluding .cache (files only, so no directory overhead)
find /data/model -path /data/model/.cache -prune -o -type f -printf '%s\n' | awk '{s+=$1} END {print s}'

# Bucket total ("Total Size" line)
aws s3 ls --recursive --summarize "s3://$BUCKET/models/$MODEL/" | tail -2
```

If the numbers differ, re-run the `aws s3 sync` command and compare again.

Detach from tmux with `Ctrl-b d`. Then continue to Step 4 from your own machine or the instance.

---

## Step 4: Cleanup & Cost Control

1. Confirm the byte counts matched in Step 3e.
2. **Terminate the temporary EC2 instance** so you do not accrue hourly charges or EBS storage fees. If you set shutdown behavior to terminate in Step 1d, `sudo shutdown -h now` does this for you. Otherwise run `aws ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE_ID"`.
3. Confirm the extra EBS volume is gone: `aws ec2 describe-volumes --region "$REGION" --filters Name=status,Values=available` should list no 40 GB volume. Delete any leftover volume.
4. Delete the temporary access, which is no longer needed:

   ```bash
   aws ec2 delete-key-pair --region "$REGION" --key-name hf-migration
   aws ec2 delete-security-group --region "$REGION" --group-id "$SG_ID"
   aws iam remove-role-from-instance-profile --instance-profile-name hf-migration-role --role-name hf-migration-role
   aws iam delete-instance-profile --instance-profile-name hf-migration-role
   aws iam delete-role-policy --role-name hf-migration-role --policy-name hf-to-s3
   aws iam delete-role --role-name hf-migration-role
   ```

   Keep the bucket. Production needs it.
5. Your production ECS Spot instances will now pull these weights directly from S3 via the free VPC Gateway Endpoint on boot.
