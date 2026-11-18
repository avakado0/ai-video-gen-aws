# Worker image and first GPU steps (yours)

The worker code, tests and Dockerfile are written (`_plans/02_gpu_worker.md`). Nothing has run on a GPU yet.

- [ ] Free about 25 GB of disk (the image build needs it; the image is about 8 GB).
- [ ] Create a Kaggle account (phone verification) for the GPU spike.
- [ ] Run the spike: kaggle.com, New Notebook, File, Import Notebook, upload `spike/wan_spike.ipynb`. In Settings pick Accelerator **GPU T4 x2** and Internet **On**. Run all (about 40 to 60 minutes, about 30 GB of weights download to `/tmp`). Copy the REPORT cell output and the contact-sheet images and paste them back here. Free; nothing touches AWS.
- [ ] Apply the `ecs.tf` change (new env vars `MODEL_MANIFEST`, `AWS_DEFAULT_REGION`). First `export AWS_PROFILE=terraform-deployer` and check `aws sts get-caller-identity --query Account --output text` prints `477656043770`. Then in `terraform/`: `terraform plan -out=tfplan`, paste the output for review, `terraform apply tfplan`. It only replaces the task definition; `gpu_max` stays 0.
- [ ] Build and push the image: `export AWS_PROFILE=terraform-deployer`, then `./scripts/build_push.sh` from the repo root. The script stops if the account is not `477656043770`. Expect a long first build; the `pip install` of the pinned set is the first real check that the versions fit together.
