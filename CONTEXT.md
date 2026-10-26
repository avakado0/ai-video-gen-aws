# Context

Domain language for the AI video generation pipeline.

## Terms

- **Deployment**: one running copy of the system. Serves exactly one model.
- **Capability manifest**: `model_manifest.json`. Declares the deployment model's input slots, prompt limit and tunable params. The front end, API and worker all read it.
- **Job**: one generation request from one user, identified by a UUID `job_id`, owned by a Cognito `sub`.
- **Input slot**: one named file input declared by the manifest (`kind` image, audio or video; required or optional). A slot holds one file, or several when it declares `max_files` above 1 (e.g. reference images). Examples: `image`, `first_frame`, `source_video`, `audio`.
- **Input**: the file a user uploads for an input slot. Stored at `inputs/{sub}/{job_id}/{slot}/{index}`. Temporary.
- **Param**: a manifest-declared generation setting (e.g. duration, seed) with a type, default and range.
- **Clip**: the generated mp4, `clips/{sub}/{job_id}.mp4`.
- **Job marker**: a small S3 object that records job state. `.pending` (submitted, JSON), `.failed` (permanent failure, JSON reason). A job with a `.mp4` is ready.
- **Model shape**: the combination of input slots a model takes (text only, text+image, first/last frame, video+text, video+audio, avatar image+audio). Not a separate code path; it is just a manifest.
- **Release**: a worker returning a job to the queue because of a Spot interruption. Not a failure.
