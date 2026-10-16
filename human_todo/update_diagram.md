# TODO (human): update the architecture diagram

Claude can't edit the draw.io PNG. `System_Diagrams/DRAFT2-AI Video Gen System.drawio.png` is out of date versus `docs/architecture/DRAFT_architecture.md`, which is the source of truth.

- [ ] Add: Cognito User Pool; CloudFront + S3 web app; API Gateway + Lambda; ECR; ECS cluster/service + capacity provider; CloudWatch alarms; Internet Gateway.
- [ ] Add the **DLQ** and the **failure-marker Lambda** (DLQ → Lambda → writes `.failed` marker to the clips bucket).
- [ ] Split the single S3 box into two buckets (models, clips). Hugging Face → models bucket only.
- [ ] Move SQS out of the ASG/VPC boxes (regional service). Put the ASG inside the VPC, spanning two AZs.
- [ ] Show the model weights being copied from the models bucket to the instance store on each Spot instance.
- [ ] Move Spot instances to a public subnet. Show the S3 Gateway Endpoint as a route-table target, not a hop.
- [ ] Fix the "Download Generated Clips With Pre-Signed URL" arrow so it ends at the User Interface, from the clips bucket.
