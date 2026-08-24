# Frozen-artifact set

One commit, six artifacts, one file. The submission needs
versioned **deployment, catalog, trace, test, metric and billing** artifacts
captured *from the frozen commit*, so that the submission cites a single
frozen state rather than six timestamps that may describe different code.

## Why it is a content check

Nothing in the deploy path records a commit. Cloud Build is handed a tarball
of the working tree, the engine is packaged by `agents-cli` the same way, and
neither the image nor the reasoning engine carries a SHA anywhere. "Deployed
from commit X" was therefore a memory, not a fact anyone could re-read.

`capture.py` makes it a fact the only way available: it pulls each Cloud Run
image through the registry API, reads the files the Dockerfile `COPY`s, and
compares them blob for blob with `git ls-tree` at the commit. A file that was
added, changed or removed after the image was built is drift, and drift fails
the section. It found real drift on its first run: the ingress image predated
the container-pin commit and four files differed.

The engine's image lives in a tenant-project registry that refuses pulls, so
the engine is checked the other way round: the graph's static import closure
is walked from `app.agent`, and any commit after the engine's `updateTime`
that touched a file in that closure (or `policy/`, `catalog/`,
`pyproject.toml`, `uv.lock`) is drift.

## What each section re-reads or re-runs

| Section | Source, right now |
|---|---|
| `commit` | `git rev-parse HEAD`; the tree must be clean; `--expect` pins the SHA STATUS names |
| `deployment.cloud_run` | latest ready revision of console, review, ingress; image diffed against the tree |
| `deployment.engine` | reasoning engine `updateTime`; commits since it that touch the graph closure |
| `catalog` | the Agent Registry entry whose `agentId` names that engine |
| `trace` | `spikes/lifecycle/harness.py` is driven live, then Cloud Trace is listed for the run's window |
| `test` | `uv run pytest` re-run (the `not live` subset; the live-marked count is recorded, not run) |
| `metric` | `tests/eval/run_domain_evals.sh` re-run; `spikes/run_streak` is checked to be against this engine |
| `billing` | the BigQuery billing export's coverage, row count, gross and net cost |

## Running it

```bash
uv run --env-file .env --env-file .env.secrets \
    python spikes/freeze/capture.py --expect <sha>
```

Both env files: the lifecycle harness writes to the ERP as the scoped
executor and the evals need the model endpoint. The Firestore emulator must
be listening on 8451 for the test and eval sections. `--skip-trace`,
`--skip-tests`, `--skip-evals` record a section as not run and make the
verdict `INCOMPLETE`, never `PASS`.

`scripts/doctor.sh` fails when the verdict is not `PASS`, and again when HEAD
has moved past the captured commit in any path an image carries.
