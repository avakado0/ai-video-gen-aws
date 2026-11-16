# No database: S3 object names are the job state

Job state lives in the clips bucket: `clips/{sub}/{job_id}.pending`, `.mp4` and `.failed`. `GET /jobs` lists the user's prefix and groups by `job_id`; `GET /jobs/{job_id}` HEADs the three keys. We chose this over DynamoDB or RDS to keep idle cost at zero and to avoid another resource to secure, for a user base of about 6.

## Consequences

- No transactions: the 3-active-jobs cap counts `.pending` markers and can overshoot by one under concurrent requests.
- Listing a prefix is the only query, so there is no cross-user view. Per-user cost comes from logs, not from queries over jobs.
- Everything expires after 1 day by lifecycle rule, so there is no job history.
- Moving to a database later means rewriting the API's job layer and the DLQ Lambda's marker write.
