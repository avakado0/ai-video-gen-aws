# Worker image and first GPU steps (yours)

The worker code, tests and Dockerfile are written (`_plans/02_gpu_worker.md`). Nothing has run on a GPU yet.

- [x] Free about 25 GB of disk (the image build needs it; the image is about 8 GB).
- [x] Create a Kaggle account (phone verification) for the GPU spike.
- [x] Run the spike: kaggle.com, New Notebook, File, Import Notebook, upload `spike/wan_t4_quality_spike.ipynb`. In Settings pick Accelerator **GPU T4 x2** and Internet **On**. Run all (about 40 to 60 minutes, about 30 GB of weights download to `/tmp`). Copy the REPORT cell output and the contact-sheet images and paste them back here. Free; nothing touches AWS.
- [x] Apply the `ecs.tf` change (new env vars `MODEL_MANIFEST`, `AWS_DEFAULT_REGION`). First `export AWS_PROFILE=terraform-deployer` and check `aws sts get-caller-identity --query Account --output text` prints `477656043770`. Then in `terraform/`: `terraform plan -out=tfplan`, paste the output for review, `terraform apply tfplan`. It only replaces the task definition; `gpu_max` stays 0.
- [x] Build and push the image (done 2026-11-20: 4.16 GB compressed in ECR, pip install took 18 min): `export AWS_PROFILE=terraform-deployer`, then `./scripts/build_push.sh` from the repo root. The script stops if the account is not `477656043770`. Expect a long first build; the `pip install` of the pinned set is the first real check that the versions fit together.

## Speed spike (optional, free, about 30 minutes)
Same Kaggle setup as above (GPU T4 x2, Internet On). Upload `spike/wan_t4_speed_spike.ipynb`, run the cells one at a time with Shift+Enter (not Run all), paste the SPEED REPORT back. The last cell is optional.

The `ecs.tf` apply now also changes the saved usage query (adds `quality`). Same `terraform plan` / `apply`, same profile reminder.
