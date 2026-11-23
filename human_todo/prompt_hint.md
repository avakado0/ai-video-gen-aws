# Plan 09: prompt tips (owner's steps)

Run `export AWS_PROFILE=terraform-deployer` first, and check `aws sts get-caller-identity` shows account 477656043770.

- [ ] `cd terraform && terraform plan`, read it, then `terraform apply`. Expected: the API Lambda and the ECS task definition change, because `model_manifest.json` has the new `prompt_hint` key (they carry the manifest as an env var).
- [ ] Upload the front end the usual way (the `frontend/` files changed: `form.js`, `prompthint.js`, `style.css`).
- [ ] Open the app: the tips show under the Prompt field with a "Show prompt tips" checkbox; untick it, reload, and they stay hidden.
