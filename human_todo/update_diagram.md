# TODO (human): update the architecture diagram

Claude can't edit the draw.io PNG. `System_Diagrams/DRAFT2-AI Video Gen System.drawio.png` was out of date versus `docs/architecture/DRAFT_architecture.md`, which is the source of truth.

- [x] Add: Cognito User Pool; CloudFront + S3 web app; API Gateway + Lambda; ECR; ECS cluster/service + capacity provider; CloudWatch alarms; Internet Gateway.
- [x] Add the **DLQ** and the **failure-marker Lambda** (DLQ → Lambda → writes `.failed` marker to the clips bucket).
- [x] Split the single S3 box into two buckets (models, clips). Hugging Face → models bucket only.
- [x] Move SQS out of the ASG/VPC boxes (regional service). Put the ASG inside the VPC, spanning two AZs.
- [x] Show the model weights being copied from the models bucket to the instance store on each Spot instance.
- [x] Move Spot instances to a public subnet. Show the S3 Gateway Endpoint as a route-table target, not a hop.
- [ ] Add the input-upload path: browser → clips bucket (`inputs/` prefix) via a pre-signed POST, and the worker reading `inputs/` from the same bucket. Add `GET /config` / `model_manifest.json` feeding the API Lambda and ECS task if there is room. (Spec 3B/3C updated for the multimodal front end.)
- [x] Fix the "Download Generated Clips With Pre-Signed URL" arrow so it ends at the User Interface, from the clips bucket.
- [ ] Add an SNS topic (email) fed by the DLQ alarm, and optionally an AWS Budgets alert next to it. Optional: label the ASG "max 2".
- [ ] Before the LinkedIn post (plan 08): change the DLQ arrow label "After 5 Failures" to "After 8 Failures" (`maxReceiveCount = 8`).
- [ ] Before the LinkedIn post (plan 08): change the subnet label "g4dn/g5 × 2 AZs = 4 pools" to the g5-only fleet (g4dn left in plan 06), e.g. "g5.2xlarge × 2 AZs = 2 pools". Check the spec for the exact current wording.
- [ ] Optional: add the scale-in Lambda (ECS task STOPPED event → ASG to 0, plan 07) and the budget cut-off Lambda (SNS → scale to 0, ADR 0004).
- [ ] After editing, export the FINAL PNG again (light and dark) and check it on a white background.
