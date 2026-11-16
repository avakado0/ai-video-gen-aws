# Model weights live in S3 and are copied to instance NVMe on every boot

Weights are stored only in the models bucket and copied to the instance store at `/data` by `user_data` on every Spot boot. We rejected EBS volumes or snapshots to avoid standing storage cost and per-instance volume handling. The price is cold-start time: each boot re-copies about 29 GB and pulls a 6-10 GB worker image (S3 to EC2 transfer is free through the gateway endpoint).

## Consequences

- Weights plus working space must fit the instance store: about 225 GB on `g4dn.2xlarge`, 450 GB on `g5.2xlarge`. Treat 225 GB as the cap. Larger models mean changing instance types, never adding EBS.
- A first job after idle waits for boot, copy and image pull before generation starts. The UI shows elapsed time and a static note instead of progress stages.
- The models bucket is created by hand and never managed by Terraform, so a `terraform destroy` cannot delete the weights.
