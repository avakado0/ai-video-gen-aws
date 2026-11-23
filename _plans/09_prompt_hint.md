# Plan 09: Wan 2.2 prompt tips, as docs and as a UI hint the user can switch off

## Context

The rea-video repo (Apache-2.0, audited 2026-11-23) holds a Wan 2.2 prompt-writing profile in
`rea_video/capabilities/prompting.yaml` (block `wan-2.2`, lines 507-528). The owner wants those
rules (1) in `docs/` and (2) shown in the UI as a prompt hint with an on/off switch. We do not
install rea-video. We copy and adapt only the rules. The rules come from vendor guides and are
"not tested one by one" (the file says so); they are guidance, not a guarantee.

## Decisions (grilled 2026-11-23)

1. **Hint text lives in the manifest**, as an optional field `prompt_hint`. The UI stays
   manifest-driven (spec 3B: no model-specific front-end code); a model swap changes the hint
   with no JS change.
2. **Short bullets only** in the manifest: a list of about 5 English tips, at most ~600 characters
   in total. Reason: the manifest travels as a Lambda env var (4 KB cap for all env vars
   together) and as an ECS task env var. Full rules, the Chinese negative-prompt list and sources
   stay in `docs/`.
3. **Toggle:** a "Show prompt tips" checkbox next to the Prompt field, default ON, choice kept in
   `localStorage`. Not rendered at all when the manifest has no `prompt_hint` or `prompt` is
   `none`. Client-side only: no API, job payload or worker change.

No ADR: easy to reverse, nothing surprising. Add a glossary line to `CONTEXT.md` (see below).

## Changes (TDD: write the failing test first for each)

1. `docs/wan22_prompting.md` (new). Adapted rules: structure (style first, then subject, motion,
   scene, aesthetic tags), length (text to video 60-200 chars, image to video 100 or fewer; our
   manifest cap is 500), image to video = motion and camera only, do not repeat what is in the
   first frame, "fixed shot" wording, camera/light/shot-size vocabulary, one shot per job, no audio.
   Negative prompt: the official list (kept in Chinese, with a gloss), plus a note that our worker
   already applies Wan's default negative prompt when the user gives none and that the
   speed-up-LoRA caveat does not apply to us (we do not use one). Source and licence line:
   JuneYaooo/rea-video, Apache-2.0, plus the upstream vendor links; state it is untested guidance.
   Check the claims against our trial 1 (`docs/wan_spike_findings.md`) and `worker/wan22.py`
   before writing them down (for example the real prompt length handling and 24 fps).
2. `lambdas/common/manifest.py`: validate `prompt_hint` when present (list of non-empty strings,
   total length <= 600, rejected if `prompt` is `none`?; decide in the test). Tests in
   `tests/manifest/test_manifest.py`: absent is fine, good value is fine, wrong types and
   over-length fail. Check `lambdas/api/manifest.py` re-exports it the same way.
3. `model_manifest.json`: add `prompt_hint` with the ~5 tips. Check the Terraform-encoded
   manifest still fits the Lambda 4 KB env budget (measure `len(json.dumps(manifest))` plus the
   other env vars; add this as a test or a note).
4. Front end: `frontend/js/form.js` renders the checkbox and the tip list under the Prompt field
   when `manifest.prompt_hint` exists; toggle shows/hides the list and writes `localStorage`.
   Put the pure logic (read/write preference, decide visibility) in a small module, for example
   `frontend/js/prompthint.js`, with a `node --test` file `tests/frontend/prompthint.test.mjs`
   (same pattern as `cost.js` / `elapsed.js`). Update the `Manifest` typedef in
   `frontend/js/api/ApiClient.js`. Add `prompt_hint` to one mock manifest (for example
   `frontend/js/mock/manifests/text-image.json`) so mock mode shows it; leave the others without,
   which covers the hidden case. Small CSS in `frontend/style.css`.
5. Docs sync: spec 3B in `docs/architecture/DRAFT_architecture.md` (manifest field list and the
   "front end renders generically" bullet), a `CONTEXT.md` glossary line ("Prompt hint: optional
   short tips in the manifest, shown beside the prompt field, user can hide them"), and
   `CLAUDE.md` test count and manifest description. Add a line to `_effort_logs/` if the repo
   keeps one for the day.
6. Deploy note for the owner (`human_todo/`): the manifest change needs the API Lambda and
   the web app redeployed: `terraform apply` for the env var, then the usual front-end upload.
   Per CLAUDE.md, start that message with `export AWS_PROFILE=terraform-deployer` and the
   `aws sts get-caller-identity` check (account 477656043770). Claude does not run it.
7. Artifact: after the repo commits, update the Control Room artifact only if the hourly rule in
   CLAUDE.md allows it (spec changed, so it is a trigger).

## Commits

One logical change each, add by path: docs file; manifest validation + tests; manifest field;
front end + tests; spec/glossary/CLAUDE.md sync. No attribution lines per CLAUDE.md. Nothing in the diff
may carry personal details.

## Verification

- `.venv/bin/pytest` (all offline, was 426) and `node --test tests/frontend/*.test.mjs`.
- `terraform fmt -check && terraform validate` in `terraform/` (the manifest is read by `file()`).
- Mock run: `python3 -m http.server -d frontend 8000`, `config.json` with `"mock": true`:
  default manifest shows the tips and the checkbox; unticking hides them and survives a reload;
  `?manifest=text` (no hint) shows no checkbox.
- Size check: print the manifest JSON length and confirm the Lambda env total stays well under 4 KB.
