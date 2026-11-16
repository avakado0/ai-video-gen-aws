# GPU instances sit in public subnets with no NAT gateway

Spot GPU instances get a public IPv4 address, no inbound security-group rules, and reach ECS, ECR, SQS and CloudWatch through the Internet Gateway. S3 traffic uses a free Gateway Endpoint on the public route table. We rejected private subnets with a NAT gateway (about $32/month idle, before traffic) because the design goal is near-zero idle cost. A running public IP costs about $0.005/hr, which we accept.

## Consequences

- Each running instance is internet-addressable, so the empty inbound rule set is the only network defence. Do not add inbound rules.
- Interface endpoints, ALBs and NAT gateways are off limits without flagging the cost first.
