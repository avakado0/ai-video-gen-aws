# Step-by-Step Guide: Migrating Model Weights from Hugging Face to Amazon S3

This document outlines the operational instructions to download your model weights from Hugging Face directly into your Amazon S3 model bucket with **zero inbound data transfer fees**.

---

## Prerequisites

* An active AWS account with your S3 model bucket already provisioned.
* A Hugging Face account (and an Access Token if downloading private or gated models like Llama or Stable Diffusion variants).
* A temporary compute instance (e.g., a `t3.medium` or `t3.large` running for under an hour, costing mere fractions of a dollar).

---

## Step 1: Spin Up a Temporary Instance

1. Launch a temporary EC2 instance (e.g., `t3.medium` with 4 GB RAM) in your target region.
2. Attach an IAM role to this temporary instance that grants `s3:PutObject`, `s3:GetObject`, and `s3:ListBucket` permissions to your S3 model bucket.
3. Attach an extra EBS volume (e.g., 50 GB to 100 GB `gp3`, at least the size of the model) if your model weights exceed the root storage limit. This volume is only for this temporary instance; production workers keep weights on the instance store, not EBS.
4. SSH into your instance.
5. Format and mount the extra volume at `/data` (check the device name with `lsblk`; it is often `/dev/nvme1n1`):

```bash
sudo mkfs -t xfs /dev/nvme1n1
sudo mkdir -p /data
sudo mount /dev/nvme1n1 /data
sudo chown ec2-user:ec2-user /data
```

---

## Step 2: Install Dependencies & Authenticate

Run the following commands in your instance terminal to install the Hugging Face CLI/Hub library and boto3 (the AWS CLI is already preinstalled on Amazon Linux):

```bash
# Update system packages and install python pip
sudo yum update -y
sudo yum install python3-pip -y

# Install huggingface_hub with CLI support
pip install -U "huggingface_hub[cli]" boto3

```

If your target model is gated or private, log in to Hugging Face using your token:

```bash
hf auth login

```

---

## Step 3: Download and Upload to S3

Use a short Python script to download the model files and then upload them to your S3 bucket. The download goes to local disk first, so the target directory needs free space of at least the full model size (use the extra EBS volume from Step 1, mounted at e.g. `/data`). Create a script named `hf_to_s3.py`:

```python
import os
import boto3
from huggingface_hub import snapshot_download

# Configuration
REPO_ID = "your-org/your-model-name"  # Replace with your Hugging Face model ID
BUCKET_NAME = "your-s3-model-bucket-name"  # Replace with your S3 bucket name
S3_PREFIX = "models/my-model/"  # must be models/<model-name>/, the path the workers' user_data copies from

print(f"Downloading {REPO_ID} from Hugging Face...")
# Download model to the large volume (the default cache is on the root disk)
local_dir = snapshot_download(repo_id=REPO_ID, local_dir="/data/model")

print(f"Uploading files to s3://{BUCKET_NAME}/{S3_PREFIX}...")
s3 = boto3.client('s3')

for root, dirs, files in os.walk(local_dir):
    # snapshot_download(local_dir=...) keeps metadata in .cache/; do not upload it
    dirs[:] = [d for d in dirs if d != ".cache"]
    for file in files:
        local_path = os.path.join(root, file)
        relative_path = os.path.relpath(local_path, local_dir)
        s3_key = os.path.join(S3_PREFIX, relative_path).replace("\\", "/")
        
        print(f"Uploading {relative_path} -> S3")
        s3.upload_file(local_path, BUCKET_NAME, s3_key)

print("Upload complete! Weights are safely in S3.")

```

Run the script:

```bash
python hf_to_s3.py

```

---

## Step 4: Cleanup & Cost Control

1. Verify via the AWS S3 Console that all model weight files are present in your bucket.
2. **Terminate the temporary EC2 instance immediately** so you do not accrue ongoing hourly charges or EBS storage fees.
3. Your production ECS Spot instances will now pull these weights directly from S3 via the free VPC Gateway Endpoint on boot.