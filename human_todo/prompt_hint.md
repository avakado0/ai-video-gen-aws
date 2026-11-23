# Plan 09: prompt tips (owner's steps)

Run `export AWS_PROFILE=terraform-deployer` first, and check `aws sts get-caller-identity` shows account 477656043770.

- [ ] In `terraform/`: `terraform plan`, read it, then `terraform apply`. Expected: the API Lambda and the ECS task definition change (they carry `model_manifest.json` as an env var, which now has `prompt_hint`), and the changed `frontend/` files are re-uploaded to the web bucket by Terraform. No GPU starts (`gpu_max` stays 0).
- [ ] Invalidate the CloudFront cache: `aws cloudfront create-invalidation --distribution-id "$(terraform output -raw cloudfront_distribution_id)" --paths "/*"`
- [ ] Open the app: the tips show under the Prompt field with a "Show prompt tips" checkbox; untick it, reload, and they stay hidden.
