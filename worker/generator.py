"""What the runner needs from a model. The real one is wan.py; tests use a fake."""


class Generator:
    def generate(self, job, inputs, should_abort, out_dir):
        """Make the clip for `job` and return the path of the .mp4 inside `out_dir`.

        `inputs` is {slot: [Path, ...]}. Poll `should_abort()` between steps; when it turns true,
        stop and raise `worker.errors.Aborted`. Raise `PermanentJobError` for a job that can
        never work (the user sees its message); anything else is retried.
        """
        raise NotImplementedError
