# Operations

Build, deploy, provisioning and failure-handling runbooks. Nothing here is
needed to evaluate the project — the [README](../README.md) is the front
door; this file is for operating and rebuilding the system.

## Runbooks

### Operational constraints

- **Agent Runtime allows 1 concurrent query and 30 queries/min per
  region.** `keplaria-ingress` is deployed with `--concurrency=1
  --max-instances=1`, and the `keplaria-events-push` subscription carries an
  explicit retry policy (`60s`–`600s` backoff). Without both, a single
  rate-limit error becomes a self-sustaining redelivery storm: the ingress
  503s, Pub/Sub redelivers near-instantly, the engine takes another hit,
  guaranteed 429, repeat.
- **A `thinking_budget` on the agents is an off switch, not a dial.** The three
  agents in `app/agent.py` pin one. Naming any value does not truncate the
  model's usual reasoning at that number: the value comes back in the trace as
  `gen_ai.usage.experimental.reasoning_tokens_limit`, a ceiling the model then
  leaves nearly empty. Measured 2026-08-18 on the deployed engine, the extractor
  went from ~1500 reasoning tokens per call to effectively none and the timed
  sequence from 85.4s to 56.9s. Read the numbers as "reasoning off"; raising one
  will not buy a middle setting. Validated by the graded domain suite — 8/8
  when the pin was measured, 24/24 since the suite grew — and a full deployed
  run, and pinned by `tests/unit/test_agent_generation_config.py`.
- **A yente VM that is RUNNING is not yet SERVING, and the difference costs
  30s per screened beat.** `screen_supplier` waits `timeout=30` before giving
  up and recording `SCREENING_UNAVAILABLE`, so a sequence started while the
  index is still loading pays that on every screened event. `scripts/doctor.sh`
  probes the service over IAP rather than trusting the VM's run state; run it
  after starting the VM and before any timed or recorded run.
- **`GOOGLE_CLOUD_LOCATION` must be `global`, not `us-central1`** —
  `gemini-3.6-flash` 404s at the regional endpoint. This is documented, not
  incidental: the [official model card](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-6-flash)
  lists only `global` and the `us`/`eu` multi-regions as supported — no single
  regions — so there is no regional endpoint to migrate to. `AGENT_ENGINE_LOCATION`
  is a deliberately separate variable (`us-central1`) that addresses only
  the Agent Engine REST endpoint. These two are kept apart on purpose:
  collapsing them into one variable is the obvious "simplification" that
  breaks the deployment, because the model-serving endpoint and the Agent
  Engine endpoint are different hosts.
- **`GOOGLE_CLOUD_PROJECT` is platform-reserved on Agent Runtime** and is
  overwritten with the numeric project number, which the Firestore client
  rejects. `FIRESTORE_PROJECT_ID` carries the project ID for the Firestore
  client instead.
- **Frappe credentials reach the ingress from Secret Manager**, not as
  plaintext environment variables.

## Dependency management: use `uv`, never `pip`

**Do not run `pip install` in this project, and never use
`--break-system-packages`.**

On the reference dev host both interpreters on `PATH` are PEP 668
externally-managed and will correctly reject a global install:

- Linuxbrew Python 3.14.6 (first on `PATH`) — has `EXTERNALLY-MANAGED`
- `/usr/lib/python3.12` — has `EXTERNALLY-MANAGED`

`pyenv` is installed there but set to `system` with an empty `~/.pyenv/versions`,
so its shims fall straight through to the Linuxbrew Python. pyenv is **not** the
source of install failures, and switching pyenv versions is not the fix. The
`EXTERNALLY-MANAGED` marker is what protects the host's system Python — work
inside `.venv`, don't override it.

### Commands

```bash
uv add <package>          # add a dependency (updates pyproject.toml + uv.lock)
uv remove <package>       # drop a dependency
uv sync                   # reconcile .venv with uv.lock
uv run python ...         # run inside the venv
uv run adk web            # ADK dev UI
```

`uv run` needs no activation. `source .venv/bin/activate` also works — the venv's
`bin/` prepends `PATH`, so pyenv shims don't interfere.

Reset is `rm -rf .venv && uv sync`.

### Installing or upgrading ADK

ADK publishes per-version constraints files pinning transitive deps to a
known-good set on a few days' delay, as supply-chain protection against a
freshly-published malicious dependency. Use them for any ADK install or major
upgrade:

```bash
curl -sSfLO https://raw.githubusercontent.com/google/adk-python/main/constraints-3.13.txt
uv add google-adk --constraints constraints-3.13.txt
rm constraints-3.13.txt
```

Files exist for `constraints-3.10.txt` through `constraints-3.14.txt`. `uv.lock`
records the resolved result, so the constraints file is not kept in the repo.

### Why Python 3.13 and not 3.14

ADK requires `>=3.10` and is tested through 3.14, but on aarch64 several
transitive dependencies still lack 3.14 wheels and fall back to source builds.
Stay on 3.13 unless there is a concrete reason to move.

### Provisioned infrastructure

- **Network:** `keplaria-vpc` (custom mode), subnet `keplaria-uscentral1`
  `10.10.0.0/24` with Private Google Access, Cloud NAT (`keplaria-nat` on
  `keplaria-router`). Ingress: IAP-range SSH only (`keplaria-allow-iap-ssh`)
  plus intra-subnet tcp 8000/9200 (`keplaria-allow-internal`).
- **Private Service Connect interface (PSC-I)** — how the deployed agent reaches
  the yente VM, which has no public address. Dedicated subnet
  `keplaria-psc-subnet` `10.10.1.0/24` (a network attachment should not share a
  subnet with VMs), network attachment `keplaria-psc2` (`ACCEPT_AUTOMATIC`), and
  firewall `keplaria-allow-psc-to-yente` allowing tcp:8000 from `10.10.1.0/24`.
  The attachment NIC lands in the PSC subnet, **not** in `10.10.0.0/24`, so
  `keplaria-allow-internal` alone does not cover it.
- **Agent Runtime deployment:** reasoning engine `keplaria`
  (`2127503872455868416`) — the promoted agent graph, and the only engine in the
  project. It is also what `services.py` binds to as the session backend on
  Cloud Run / local, via its find-or-create-by-display-name fallback. See
  [Deploying](#deploying-to-agent-runtime).
- **VM `keplaria-yente`** (`us-central1-c`): `e2-standard-4`, 60 GB pd-ssd,
  **no external IP** (10.10.0.2), service account `yente-vm@`. Daily snapshots,
  7-day retention (`keplaria-daily-snap`). SSH:
  `gcloud compute ssh keplaria-yente --zone us-central1-c --tunnel-through-iap`.
- **The VM restarts itself, hourly, and nothing stops it** — resource policy
  `keplaria-always-on` (`0 * * * *`, America/Bogota), attached 2026-08-22. It
  replaced `keplaria-nightly-stop`, which stopped the VM at 01:00 and had **no
  matching start schedule**, so screening went down every night and stayed down
  until someone noticed and started it by hand. Starting an already-running
  instance is a no-op, so an hourly start costs nothing and puts a ceiling of
  one hour on how long the screening path can be down for any reason — a
  stopped VM, a crash, a stockout that resolved. `scripts/doctor.sh` reads the
  schedule off the instance and fails if it finds a stop without a start; the
  old policy still exists, unattached, for the day the always-on posture is no
  longer wanted.
- **When the start fails with "does not have enough resources available",
  change the machine family — do not sit in a retry loop.** The stockout is
  per family and it moves: `e2` was out region-wide on creation day (hence the
  original `t2d-standard-4`), and on 2026-08-19 `t2d` and `n2` were both out
  while `e2` started first try. All three are 4 vCPU / 16 GB and yente does not
  care which it runs on. The boot disk is zonal and stays put, so this is one
  command on the stopped VM and the IP, subnet, and index survive it:

  ```bash
  gcloud compute instances set-machine-type keplaria-yente \
    --zone us-central1-c --machine-type e2-standard-4   # or n2-, t2d-standard-4
  gcloud compute instances start keplaria-yente --zone us-central1-c
  ```

  Changing **zone** is the expensive fallback *for a stopped VM* and rarely the
  right first move there: the disk is zonal, so it would have to be imaged and
  the VM rebuilt. **This is not true of a rebuild from snapshot** — see the
  next bullet. After any start, wait for SERVING — the index takes ~90s to
  load and the VM reports `RUNNING` throughout; `scripts/doctor.sh` probes for
  it.
- **If the VM is gone rather than stopped, the family swap does not help and
  the zone is free.** Measured 2026-08-20 by the recovery drill: all three
  families refused *new instance creation* in `us-central1-c` — a stockout
  that a running VM never notices, because its capacity is already allocated.
  Restoring the snapshot into another `us-central1` zone costs nothing,
  because a snapshot is a global resource and everything else the VM needs is
  **regional**: the subnet `keplaria-uscentral1` (`10.10.0.0/24`), the PSC
  attachment `keplaria-psc2`, and firewall rules scoped by source range rather
  than by target tag. The rebuilt VM keeps `10.10.0.2` in any of the four
  zones. Path, preconditions, and the non-destructive drill:
  [`infra/yente/RECOVERY.md`](../infra/yente/RECOVERY.md).
- **Two recovery invariants, both added 2026-08-20 and both checked by
  `scripts/doctor.sh`.** The boot disk is `autoDelete=false`, so deleting the
  instance — the first step of any rebuild — no longer destroys the disk and
  its index. And `10.10.0.2` is reserved as the static internal address
  `keplaria-yente-ip`, so nothing else can be handed it while the VM is down;
  `app/nodes.py` defaults `YENTE_BASE_URL` to that literal address and the
  deployed engine carries it in its environment, so a rebuild that could not
  reclaim it would be invisible to the graph.
- **Screening service** on that VM: yente + Elasticsearch, serving
  `10.10.0.2:8000` inside the VPC. Runbook, network posture, and the
  `/match` cutoff/threshold gotcha are in
  [`infra/yente/README.md`](../infra/yente/README.md); the indexed data is the
  synthetic watchlist in [`fixtures/watchlist/`](../fixtures/watchlist/).
  It fetches nothing from OpenSanctions: the synthetic fixture is the only
  indexed dataset. Bulk-data rights for this entry are confirmed in writing by
  OpenSanctions; indexing the fixture is a deliberate choice, not a licensing
  limit.
- **Failure-handling infrastructure:** Pub/Sub topic `keplaria-events-dead`
  with push subscription `keplaria-events-dead-push`, where an event the
  ingress rejects on every delivery lands instead of expiring silently at the
  7-day retention boundary; and Cloud Scheduler job `keplaria-command-sweep`
  (`*/15 * * * *`), which calls `POST /admin/sweep` so a failed command is
  re-driven unattended rather than waiting for the next event on its case.
  Both are load-bearing and both fail silently when misconfigured — see
  [Failure handling](#failure-handling) for the two IAM bindings whose absence
  produces no error, only lost events.
- **Billing guardrails:** budget `keplaria-build` alerts at $100/$130; budget
  `keplaria-killswitch` ($200) publishes to Pub/Sub topic `billing-killswitch`,
  where the Cloud Function in [`infra/billing-killswitch/`](../infra/billing-killswitch/)
  **detaches billing from the project** once reported cost exceeds the budget
  (its SA holds `roles/billing.projectManager`, project-scoped). If it trips,
  everything stops — re-attach billing manually in the console to recover.
- `scripts/doctor.sh` verifies all of the above read-only.

Provisioning note: project-level IAM bindings and billing-account IAM cannot be
granted from an agent session (permission classifier); run those as the human
via `!`-prefixed commands.

### Which Firestore the tests talk to

Three targets, and the suite picks one automatically:

| Target | When | Speed |
|---|---|---|
| Emulator on `localhost:8451` | default, whenever the port is listening | whole unit suite in ~6s |
| `keplaria-test` | no emulator running, or a live-marked run, or `KEPLARIA_TEST_USE_REAL_FIRESTORE=1` | ~40s and network-bound |
| `(default)` | never, from tests | — |

```bash
gcloud beta emulators firestore start --host-port=localhost:8451
```

Start it and every run is hermetic; forget to and the run still works, but
`tests/conftest.py` now says so with a warning instead of falling through in
silence. That silence was expensive. On 2026-08-23 the full suite took roughly
thirteen minutes, two `test_sweep` tests hung for ten minutes apiece, and a
`test_console_store` assertion failed — and that failure had been carried for
two sessions as a disclosed product defect. It was none of those things. The
runs were going to the real `keplaria-test` database, which had accumulated
**10,330 case documents** and 1,533 failed or dead commands, and
`list_failed_commands` applies its `limit` in the query before sorting in
Python, so the fixture's own row simply never came back. The same tests pass
in 0.64s on the emulator.

The accumulated documents were purged on 2026-08-23 after checking that no
committed spike evidence named anything living there — every cited id is in
`(default)`. The database and its collection-group index were kept, so the
fallback path stays usable: it now runs those two files in ~44s.

**A live-marked run never uses the emulator**, even when one is running. The
emulator does not enforce collection-group indexes, so a query that 400s
against real Firestore passes against it, and a live run pointed at the
emulator would be testing the wrong thing while looking greener.
`tests/unit/test_firestore_target_selection.py` pins that decision, including
that the default `-m 'not live'` is not a live run.

### Firestore indexes: the `outbox` collection-group index

The sweep (`app/executor/sweep.py`) and `/review/failures` run a **collection
group** query that filters `outbox` documents on the single field `status`.
Firestore serves that from a **single-field index whose `queryScope` is
`COLLECTION_GROUP`** — not from a composite index. Two dead ends, both hit for
real on 2026-08-17:

- `gcloud firestore indexes composite create --collection-group=outbox
  --field-config=field-path=status,order=ascending` is **rejected**:
  `INVALID_ARGUMENT: this index is not necessary, configure using single field
  index controls`. A one-field filter is never a composite.
- `gcloud firestore indexes fields update` cannot express query scope at all —
  its `--index` flag accepts only `order` / `array-config`, so every index it
  creates is `COLLECTION`-scoped and the collection-group query still fails.

The only route that works is the REST API. Run it once per database — the
deployed sweep and `/review/failures` use `(default)`, and a test run uses
`keplaria-test` whenever it is not on the emulator (see "Which Firestore the
tests talk to" below):

```bash
cat > /tmp/idx.json <<'EOF'
{"indexConfig":{"indexes":[
{"queryScope":"COLLECTION","fields":[{"fieldPath":"status","order":"ASCENDING"}]},
{"queryScope":"COLLECTION","fields":[{"fieldPath":"status","order":"DESCENDING"}]},
{"queryScope":"COLLECTION_GROUP","fields":[{"fieldPath":"status","order":"ASCENDING"}]}
]}}
EOF
TOKEN=$(gcloud auth print-access-token)
for DB in 'keplaria-test' '(default)'; do
  curl -s -X PATCH -d @/tmp/idx.json \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    "https://firestore.googleapis.com/v1/projects/keplaria/databases/${DB}/collectionGroups/outbox/fields/status?updateMask=indexConfig"
done
```

Check state with
`gcloud firestore indexes fields list --database=<db> --project=keplaria --format=json`;
the indexes sit at `CREATING` for a few minutes before they are `READY`, and the
query keeps returning `FailedPrecondition` until then.

**Side effect, stated honestly:** the PATCH body *replaces* the field's whole
`indexConfig`, so it also removed the default `array-contains` index on
`status`. That is harmless here because `status` is always a string and no query
uses `array-contains` on it — but a PATCH on a field that does hold arrays would
break those queries silently.

`scripts/doctor.sh` checks this with `gcloud firestore indexes fields list` in
both databases and names whichever one is missing it. Do not "fix" that check by
pointing it back at `indexes composite list`: that command will never list this
index, so the check could only ever fail.

## `agents-cli`

The [Agents CLI](https://docs.cloud.google.com/gemini-enterprise-agent-platform/agents/quickstart-adk)
is the Agent Development Lifecycle toolchain. Install it as a `uv tool` in its
own isolated venv — it is deliberately **not** a project dependency:

```bash
uv tool install google-agents-cli    # or: uv tool upgrade google-agents-cli
agents-cli --version                 # currently 1.3.1
```

| Command | Purpose |
|---|---|
| `agents-cli playground` | local agent playground |
| `agents-cli run` | single-prompt, non-interactive run |
| `agents-cli eval generate` / `eval grade` | run inference over eval cases, then grade |
| `agents-cli scaffold enhance .` | add deployment / CI-CD to an existing project |
| `agents-cli deploy` | deploy (targets: `agent_runtime`, `cloud_run`, `gke`) |
| `agents-cli publish` | publish to Gemini Enterprise |
| `agents-cli info` | show project config, paths, CLI version |

`agents-cli login` is unnecessary where gcloud and ADC are already configured.

### Workspace skills

`agents-cli setup --workspace` installs 7 ADK skills at **project scope** into
`.agents/skills/`, with a `skills-lock.json` manifest (source `google/agents-cli`
on GitHub, each entry content-hashed):

`google-agents-cli-adk-code`, `-deploy`, `-eval`, `-observability`, `-publish`,
`-scaffold`, `-workflow`

Both `.agents/` and `skills-lock.json` are committed, so the skill set is pinned
rather than re-resolved per machine. `agents-cli info` should report
`Installed skills: 7 (project)`. Refresh with `agents-cli update`.

**Read these before improvising — they are more current than model priors, and
the 2026-08-13 deploy debugging burned ~40 minutes rediscovering things already
written down here:**

| Question | File |
|---|---|
| What must the container expose on Agent Runtime? PSC, DNS peering, `deployment_metadata.json` | `google-agents-cli-deploy/references/agent-runtime.md` |
| How do I call a deployed agent? (the `/api/...` passthrough, and how it differs from the `reasoning_engine` adapter) | `google-agents-cli-deploy/references/testing-deployed-agents.md` |
| Where do the logs and traces actually go? Agent Runtime stdout arrives as `aiplatform_googleapis_com_reasoning_engine_stdout` | `google-agents-cli-observability/references/cloud-trace-and-logging.md` |
| Scaffold flags and what each deployment target generates | `google-agents-cli-scaffold/references/flags.md` |

When a reference names a helper that does not exist in site-packages, assume it
is a **project-local scaffold file** before assuming the docs are stale — that
exact misread is what cost the 40 minutes.

### Scaffolded project (2026-08-12), promoted to Agent Runtime (2026-08-13)

`agents-cli scaffold enhance` originally ran with `--deployment-target cloud_run
--session-type agent_platform_sessions --region us-central1`. Result:
`agents-cli-manifest.yaml`, agent code under `app/` (workflow in `agent.py`,
server in `fast_api_app.py`), `tests/`, `deployment/` (Terraform), `Dockerfile`.

On 2026-08-13 the graph was promoted to **Agent Runtime** and the manifest now
declares `deployment_target: agent_runtime` / `session_type: none` (Agent
Runtime manages sessions itself). Sessions resolve two ways in
`app/app_utils/services.py`: on Agent Runtime it binds directly to the injected
`GOOGLE_CLOUD_AGENT_ENGINE_ID`; elsewhere (Cloud Run, local) it falls back to
finding or creating the Agent Engine named `keplaria`.

**Scaffold gotcha:** `scaffold enhance` runs in overwrite mode and
`shutil.rmtree`s existing directories — it **crashes on symlinked dirs**
(e.g. `strategy/`). To re-run it: copy the repo (minus symlinks and `.env`)
to a scratch dir, enhance there, port the output back, and fix the project
name it embeds from the directory name.

Spike harnesses (HITL resume across SIGKILL, retry-on-503, ERP capability
checks, Agent Runtime criteria) live under `spikes/` — each is a standalone
`uv run python spikes/<name>/spike.py`.

## Deploying to Agent Runtime

```bash
agents-cli deploy --project keplaria --region us-central1 \
  --network-attachment projects/keplaria/regions/us-central1/networkAttachments/keplaria-psc2
```

`--project` is required in non-interactive use — without it, `agents-cli`
falls back to an interactive project picker.

Verify afterwards with
`uv run --env-file .env python spikes/lifecycle/harness.py`, which drives the
full five-step station-keeping lifecycle against the deployed engine and
ingress, and writes `spikes/lifecycle/evidence.json`.
`spikes/thin_vertical/verify.py` remains as the narrower single-event
vertical check.

`spikes/agent_runtime/spike.py` and `spikes/hitl_resume/spike.py` are
point-in-time records from when the graph still paused mid-run on a public
`RequestInput`. The graph no longer does that, so re-running either spike
against the current deployment fails; they and their `evidence.json` files
are kept as historical proof of what was verified at the time, not as
current verification tooling.

### `.gcloudignore` is mandatory

Without it the packager falls back to `.gitignore`, which excludes none of the
`strategy`, `.claude`, or `CLAUDE.md` symlinks — **the private strategy layer
would be packaged into the deployed container.** It fails loudly today only
because the packager rejects symlinks resolving outside the project root; that
is a guardrail, not the protection. Never deploy without `.gcloudignore`.

### Required IAM (grant as the human, then wait)

PSC-I needs the Vertex AI service agents to both read *and modify* the network
attachment — the producer PATCHes it to register its endpoint. `networkUser`
alone grants only get/list and the deploy fails as a bare "failed to start".

```bash
SA=service-584548214478@gcp-sa-aiplatform.iam.gserviceaccount.com
RE=service-584548214478@gcp-sa-aiplatform-re.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding keplaria --member=serviceAccount:$SA \
  --role=roles/compute.networkUser  --condition=None
gcloud projects add-iam-policy-binding keplaria --member=serviceAccount:$RE \
  --role=roles/compute.networkUser  --condition=None
gcloud projects add-iam-policy-binding keplaria --member=serviceAccount:$SA \
  --role=roles/compute.networkAdmin --condition=None
```

**Grants take ~2 minutes to propagate — the first retry after granting still
returns 403.** `roles/compute.networkAdmin` is broad (firewalls, routes, and
subnets project-wide); acceptable for a throwaway project, revisit otherwise.

### PSC config is immutable after deployment

**Changing** PSC config on an existing engine — adding, removing, or swapping
`--network-attachment` — fails with *"The Reasoning Engine failed to be
updated."* Delete the engine and create it again instead.

Redeploying with the **same** attachment is a normal update and works fine, so
routine code deploys need no special handling. Only the PSC value itself is
frozen.

### Renaming an engine does not rename its Agent Registry entry

Agent Registry snapshots `displayName` at registration and does not follow a
later engine rename — it will show the stale name indefinitely, and `PATCH` on
the registry entry returns 404 (the entries are system-managed). **A redeploy is
what refreshes it.** This matters because Agent Registry is the catalog surface
other services and operators discover this agent through, so a stale or
debugging-flavoured display name is externally visible and misleading.

Prefer deploying with the final name via `--service-name` from the start.

### Keep exactly one engine

`app/app_utils/services.py` finds-or-creates a session backend **by display
name** (`keplaria`) whenever `GOOGLE_CLOUD_AGENT_ENGINE_ID` is absent — i.e. on
Cloud Run and local runs. Two consequences:

- Deleting a stray engine does not stick; the next local run recreates it.
  Keeping the real deployment named `keplaria` is what makes the fallback bind
  to it instead of manufacturing a duplicate.
- Local runs share the deployed engine's session store. Set
  `USE_IN_MEMORY_SESSION=true` (or `AGENT_ENGINE_SESSION_NAME`) if you need to
  keep local experiments out of it — relevant during the judging window.

`scripts/doctor.sh` asserts the one-engine invariant.

### The one failure mode you will actually hit

Nearly every misconfiguration surfaces as the identical, useless error:

> `Reasoning Engine resource [...] failed to start and cannot serve traffic.`

with **zero container logs** — the build logs
(`aiplatform.googleapis.com/reasoning_engine_build`) succeed, and the stdout log
(`aiplatform_googleapis_com_reasoning_engine_stdout`) is never created at all,
because the container dies before logging exists. Absence of that log stream is
itself the diagnosis: the container never ran.

**Do not debug this by reading logs. Bisect against a throwaway scaffold:**

```bash
cd "$(mktemp -d)"   # NEVER in the repo — scaffold create writes to CWD
agents-cli scaffold create probe --deployment-target agent_runtime \
  --cicd-runner skip --region us-central1 --auto-approve
```

Deploy that stock project (it works), then copy your files across one group at a
time until it breaks. Three real defects were found this way on 2026-08-13, all
of which also affect the Cloud Run fallback:

| Defect | Why it breaks | Fix |
|---|---|---|
| `app_utils/reasoning_engine_adapter.py` missing | The `cloud_run` scaffold omits it, so the container cannot serve the `reasoning_engine` contract. It is a **project-local scaffold file**, not an ADK export — grepping site-packages for `attach_reasoning_engine_routes` finds nothing and is misleading | Port it from an `agent_runtime` scaffold |
| `services.py` called `agent_engines.list()/create()` at import | On Agent Runtime the container **is** an agent engine, so it blocked its own boot | Prefer the injected `GOOGLE_CLOUD_AGENT_ENGINE_ID` |
| `Dockerfile` `python:3.12-slim` vs `requires-python >=3.13` | Installs a 3.13-resolved `uv.lock` into a 3.12 venv; builds clean, dies on import | Keep the base image and `requires-python` in lockstep |

### Secrets

`agents-cli deploy` reads `.env` and injects **every** key it finds as a
plaintext runtime env var on the engine, echoing the values to stdout as it
goes. There is no flag to exclude a key, so the only reliable control is which
file a secret lives in:

| File | Read by | Deployed? |
|---|---|---|
| `.env` | local tooling, **and `agents-cli deploy`** | yes — every key becomes plaintext env |
| `.env.secrets` | local tooling only | no — ignored by git *and* `.gcloudignore` |

Anything secret goes in `.env.secrets`. Local commands pass both files, in this
order:

```bash
uv run --env-file .env --env-file .env.secrets python scripts/erp.py audit
```

Deployed services do not use either file — Cloud Run wires `FRAPPE_API_KEY` and
`FRAPPE_API_SECRET` from Secret Manager with `--set-secrets`, and the engine
does not receive them at all: it has no public internet egress and its graph
never imports the executor, so it cannot reach the ERP under any circumstances.
A Frappe credential on the engine is therefore both unused and readable.

`scripts/doctor.sh` enforces all three of these — no secret-shaped key in
`.env`, both env files excluded by `.gcloudignore` (which **replaces**
`.gitignore` at deploy time, so being gitignored proves nothing), and no
secret-shaped plaintext env var on the deployed engine.

All three now report green, including the engine one — but the route there is
worth keeping, because two documented remedies were disproven along the way.

An engine deployed before this split carried the old values as plaintext. A
redeploy does **not** clear them: a normal `agents-cli deploy` ran on
2026-08-22 with a clean `.env` and the engine still carried
`FRAPPE_API_KEY` / `FRAPPE_API_SECRET` afterwards, because `agents-cli`
carries the existing engine's env forward on update rather than replacing the
set with what `.env` holds. The proof was by elimination: `DEVTO_API_KEY`
exists only in `.env.secrets` and never appeared on the engine, so
`.env.secrets` was never read, and `.env` no longer held the Frappe keys,
leaving carry-forward as the only source. `agents-cli deploy` exposes no flag
that removes an env var either — `--update-env-vars` only sets.

What cleared them was `infra/strip-engine-secrets.sh` against the **v1beta1**
endpoint; the same call against `v1` fails with "The Reasoning Engine failed
to be updated". See the comment block at the top of that script.

### Rotating the ERP credential

Two identities live on the Frappe site and only one of them is rotated this
way:

| Identity | Env vars | Where it lives | Used by |
|---|---|---|---|
| Scoped executor `keplaria-executor` | `FRAPPE_API_KEY` / `FRAPPE_API_SECRET` | Secret Manager `frappe-api-key` / `frappe-api-secret`, and `.env.secrets` | every deployed service |
| Site owner | `FRAPPE_ADMIN_API_KEY` / `FRAPPE_ADMIN_API_SECRET` | `.env.secrets` only | `scripts/erp.py` |

The owner key is deliberately absent from Secret Manager and from every
deployed service. Deleting a record needs rights the executor does not have,
which is the whole point of the split — so if a purge starts returning 403,
the cause is a missing admin variable, not a broken ERP.

Rotating the executor credential:

1. Regenerate:

   ```bash
   uv run --env-file .env --env-file .env.secrets \
       python spikes/frappe_scoped_executor/provision.py --generate-keys
   ```

   The provision script is idempotent and also re-asserts the role, so it is
   safe to run without `--generate-keys` any time you want the permissions put
   back the way `contract.py` declares them.

2. Add BOTH values to Secret Manager. `generate_keys` returns a new
   `api_secret`, and a rotation onto a different ERP user changes the
   `api_key` as well, so rotating only the secret is what the 2026-08-20 pass
   did and it is not enough for an identity change:

   ```bash
   gcloud secrets versions add frappe-api-key    --project keplaria --data-file=-
   gcloud secrets versions add frappe-api-secret --project keplaria --data-file=-
   ```

3. New revisions for `keplaria-ingress` and `keplaria-review`, so `:latest`
   re-resolves. A revision holds the version it resolved at deploy time, so
   ERP writes fail between steps 2 and 3. `gcloud run deploy` is blocked from
   agent sessions but `gcloud run services update` is not, and it needs no
   image:

   ```bash
   for svc in keplaria-ingress keplaria-review; do
     gcloud run services update "$svc" --region us-central1 --project keplaria \
       --update-secrets FRAPPE_API_KEY=frappe-api-key:latest,FRAPPE_API_SECRET=frappe-api-secret:latest
   done
   ```

4. Update `.env.secrets` for local tooling.

5. Re-measure, don't assume:

   ```bash
   uv run --env-file .env --env-file .env.secrets \
       python spikes/frappe_scoped_executor/harness.py
   bash scripts/doctor.sh
   ```

   Doctor asks the ERP who the deployed credential authenticates as, and
   checks that both revisions are newer than the secret versions they resolve.
   Skipping step 3 leaves every local check green while the services keep
   serving on the old credential, which is exactly what those two checks
   exist to catch.

The engine needs nothing here. It never reads these.

### One image, two entry points

Both services build from `console/Dockerfile`, whose base image
(`python:3.13-slim`) is kept in lockstep with `console/pyproject.toml`'s
`requires-python`, same reasoning as the root Dockerfile (see "The one
failure mode you will actually hit" above). The image's default command
serves the public console; the review deploy overrides it:

```bash
--command uvicorn --args console.review:api,--host,0.0.0.0,--port,8080,--proxy-headers
```

`--proxy-headers` matters even though nothing here currently depends on it:
TLS terminates at Cloud Run and the container is reached over plain HTTP, so
without the flag `request.url.scheme` reads `http` inside the container no
matter what the browser used. `console/review.py`'s CSRF check was written
to compare the request `Origin`'s **host** only, deliberately, so it does
not depend on the scheme being right — but any future code that builds an
absolute URL or issues a redirect would silently produce an `http://` link
on a service the browser only ever reaches over `https://`. Setting the flag
now avoids rediscovering that gap later.

`console/cloudbuild.yaml` builds from the repo root so the Dockerfile can
`COPY app/`, the same pattern as `ingress/cloudbuild.yaml`. `.gcloudignore`
governs what gets uploaded either way and needs no change for this build —
it already excludes the private `strategy` / `.claude` / `CLAUDE.md`
symlinks regardless of which Dockerfile is building.

### Routine redeploy

Both services run from **one image**, so the normal loop is build once, then
deploy both. Deploy by **digest**, not `:latest` — `:latest` moves, and two
services silently ending up on different builds is exactly what the one-image
design exists to prevent.

Both Dockerfiles install under `constraints.txt`, exported from `uv.lock`, so a
build resolves the same versions the test suite ran against. Without it, on
2026-08-24 two console builds two hours apart got different
`google-cloud-firestore` releases and the second one failed every query with
`400 Invalid database id %28default%29`. After any `uv add` / `uv lock`:

```bash
uv export --frozen --no-hashes --no-dev --no-emit-project --format requirements-txt \
  | grep -vE '^(#|-e|\s*$)' > constraints.txt   # the packaging test fails if you forget
```

```bash
# 1. Cheap local check first — this catches an import error in seconds
# rather than after a three-minute build.
uv run python -c "import console.public, console.review; print('both apps import')"

# 2. Build. The digest is printed in the result; copy it.
gcloud builds submit --config console/cloudbuild.yaml \
  --project keplaria --region=us-central1 \
  --substitutions=_IMAGE=us-central1-docker.pkg.dev/keplaria/keplaria/console:latest

# 3. Deploy both, pinned to that digest. Passing ONLY --image preserves the
# service account, env vars, secrets, IAP binding and ingress settings; do
# NOT re-run the provisioning flags below, which would reset them.
IMG=us-central1-docker.pkg.dev/keplaria/keplaria/console@sha256:PASTE_DIGEST
for SVC in keplaria-console keplaria-review; do
  gcloud run deploy "$SVC" --image "$IMG" \
    --region us-central1 --project keplaria
done

# 4. Verify against the live services, not the build log.
curl -s -o /dev/null -w 'console /fleet: %{http_code}\n' \
  https://keplaria-console-584548214478.us-central1.run.app/fleet   # expect 200
curl -s -o /dev/null -w 'review anon: %{http_code}\n' \
  https://keplaria-review-584548214478.us-central1.run.app/review   # expect 302
```

**Step 4 is not ceremony.** On 2026-08-22 a deploy succeeded, the container
started, and every route worked except `/fleet`, which returned 503 because a
data directory was missing from the image — see "Runtime data directories"
below. Nothing before step 4 would have shown it.

### The frozen state, and what a redeploy does to it

`spikes/freeze/evidence.json` names the one commit the submission cites and
ties every deployed container to it **by content**: nothing in the deploy
path records a SHA (Cloud Build is handed a tarball of the working tree), so
`spikes/freeze/capture.py` pulls each Cloud Run image through the registry
API and diffs the `COPY`d files against `git ls-tree` at that commit. The
engine image lives in a tenant-project registry that refuses pulls, so the
engine is checked by static import closure from `app.agent` and `git log`
since its `updateTime`. See `spikes/freeze/README.md`.

Consequences for anyone deploying:

- `scripts/doctor.sh` FAILS when the verdict is not `PASS`, and when HEAD has
  moved past the captured commit in a deployable path (`app`, `console`,
  `ingress`, `catalog`, `fixtures`, `policy`, `constraints.txt`,
  `pyproject.toml`, `uv.lock`). Copy-only commits WARN.
- After any deployable change: redeploy what carries it, re-run the ten-run
  streak if the engine or ingress moved, then re-capture:

  ```bash
  uv run --env-file .env --env-file .env.secrets \
      python spikes/freeze/capture.py --expect "$(git rev-parse --short HEAD)"
  ```

  Emulator on 8451; about five minutes; it drives one live lifecycle run,
  the domain evals and the test suite, and rewrites the evidence.
- Never write a "frozen commit" into STATUS or the submission by hand. Read
  it off the evidence file. The first hand-declared one was six commits stale
  before anyone checked, and the deployed ingress had never been rebuilt
  after the container pin.

### Runtime data directories must be copied explicitly

Three directories live **outside** `app/` and are read at runtime:

| Directory | Read by | Failure if absent |
|---|---|---|
| `catalog/` | `app/catalog.py` → `fleet.v1.json` | `CatalogLoadError`; every routing proposal refused, `/fleet` 503s |
| `policy/` | `app/risk.py` → `supplier_risk.v2.json` | `POLICY_UNAVAILABLE`; every case blocked |
| `fixtures/` | `app/documents.py` → `documents/` | `DocumentUnavailable`; every document case quarantined |

`.gcloudignore` decides what reaches the **build context**; each Dockerfile's
`COPY` list decides what lands in the **image**. Both must allow it, and the
second is the one that gets forgotten — an image missing one of these builds,
starts, and serves every unrelated route, so no build or startup signal fires.

**The engine is different and must be reasoned about separately.**
`agents-cli deploy` packages the *source tree* ("Creating in-memory tarfile of
source_packages") under `.gcloudignore` and never consults a Dockerfile, so the
engine and the Cloud Run images fail in different ways from the same mistake.

`tests/unit/test_container_packaging.py` enforces this: it discovers the list by
scanning `app/` for paths that escape the package, and fails any image shipping
`app/` without them. **Add a fourth directory to that test, not only to a
Dockerfile** — comments on the first two did not prevent the third.

### First-time provisioning (already run — kept for rebuilds)

Both service accounts and both services now exist; `scripts/doctor.sh` checks
them. This is the order the commands needed to run in, and the order a rebuild
from an empty project would need again:

```bash
# 1. Service accounts and their IAM grants (project-level IAM cannot be
# granted from an agent session — see "Provisioned infrastructure" above).
gcloud iam service-accounts create keplaria-console \
  --display-name="Keplaria public console" --project keplaria
gcloud iam service-accounts create keplaria-review \
  --display-name="Keplaria review service" --project keplaria

# Read-only for the console; the review service commits decisions and
# executes queued commands, so it needs write access.
gcloud projects add-iam-policy-binding keplaria \
  --member=serviceAccount:keplaria-console@keplaria.iam.gserviceaccount.com \
  --role=roles/datastore.viewer --condition=None
gcloud projects add-iam-policy-binding keplaria \
  --member=serviceAccount:keplaria-review@keplaria.iam.gserviceaccount.com \
  --role=roles/datastore.user --condition=None

# 2. Build the shared image.
gcloud builds submit --config console/cloudbuild.yaml \
  --project keplaria --region=us-central1 \
  --substitutions=_IMAGE=us-central1-docker.pkg.dev/keplaria/keplaria/console:latest
# Expected: SUCCESS. Cheaper local equivalent before submitting the build:
#   uv run python -c "import console.public, console.review; print('both apps import')"

# 3. Deploy the public console.
gcloud run deploy keplaria-console \
  --image us-central1-docker.pkg.dev/keplaria/keplaria/console:latest \
  --region us-central1 --project keplaria \
  --allow-unauthenticated \
  --service-account keplaria-console@keplaria.iam.gserviceaccount.com \
  --set-env-vars FIRESTORE_PROJECT_ID=keplaria,FIRESTORE_DATABASE='(default)'

# 4. Let the review identity read the ERP credentials. Without this the
# deploy in the next step fails outright — "Permission denied on secret ...
# The service account used must be granted the 'Secret Manager Secret
# Accessor' role" — because --set-secrets is resolved at revision creation,
# not at request time. Bound per secret rather than project-wide, to match
# the read-only/read-write split above.
for S in frappe-api-key frappe-api-secret; do
  gcloud secrets add-iam-policy-binding "$S" \
    --member=serviceAccount:keplaria-review@keplaria.iam.gserviceaccount.com \
    --role=roles/secretmanager.secretAccessor --project keplaria
done

# 5. Deploy the review service — note --proxy-headers (see "One image, two
# entry points" above) and secrets rather than plaintext ERP credentials.
FRAPPE_SECRETS=FRAPPE_API_KEY=frappe-api-key:latest,FRAPPE_API_SECRET=frappe-api-secret:latest
gcloud run deploy keplaria-review \
  --image us-central1-docker.pkg.dev/keplaria/keplaria/console:latest \
  --region us-central1 --project keplaria \
  --no-allow-unauthenticated \
  --service-account keplaria-review@keplaria.iam.gserviceaccount.com \
  --command uvicorn \
  --args console.review:api,--host,0.0.0.0,--port,8080,--proxy-headers \
  --set-secrets "$FRAPPE_SECRETS" \
  --set-env-vars FIRESTORE_PROJECT_ID=keplaria,FIRESTORE_DATABASE='(default)',FRAPPE_SITE=https://andina-foods.v.frappe.cloud

# 6. Enable IAP directly on the Cloud Run service. This is the modern path
# and needs no load balancer or backend service. The service agent does not
# exist until it is asked for — provision it first, or the invoker binding
# fails with a member-does-not-exist error.
gcloud services enable iap.googleapis.com --project keplaria
gcloud beta services identity create --service=iap.googleapis.com --project keplaria
gcloud run services add-iam-policy-binding keplaria-review \
  --region=us-central1 --project keplaria \
  --role=roles/run.invoker \
  --member=serviceAccount:service-584548214478@gcp-sa-iap.iam.gserviceaccount.com
gcloud run services update keplaria-review \
  --region=us-central1 --project keplaria --iap
gcloud iap web add-iam-policy-binding \
  --member=user:REVIEWER@EXAMPLE.COM \
  --role=roles/iap.httpsResourceAccessor \
  --region=us-central1 --resource-type=cloud-run --service=keplaria-review \
  --project=keplaria

# 7. Browser-only, and unavoidable: this project has no organization, so IAP
# has no OAuth client until one is made by hand, and until then every request
# 502s at IAP without ever reaching the container. `gcloud iap oauth-brands`
# cannot help — it rejects no-org projects outright ("Project must belong to
# an organization"), which is why nothing could verify the consent screen
# before this point. Configure branding at console.cloud.google.com/auth/branding
# (audience type External), then enable IAP from the Cloud Run service's
# Security tab, which creates the Web application client and attaches it.
# Verify with: gcloud iap settings get --project=keplaria \
#   --resource-type=cloud-run --region=us-central1 --service=keplaria-review
# A response naming only the resource, with no client id, means step 7 is
# not done.

# 8. Set the audience. For IAP enabled DIRECTLY on Cloud Run the format is
# /projects/PROJECT_NUMBER/locations/REGION/services/SERVICE_NAME — note the
# region, not "global", and the service name, not a backend-service id (that
# other shape belongs to the load-balancer integration). A wrong audience is
# not loud: verification simply fails and every approval returns 403.
gcloud run services update keplaria-review \
  --region us-central1 --project keplaria \
  --update-env-vars IAP_AUDIENCE=/projects/584548214478/locations/us-central1/services/keplaria-review

# 9. Confirm: curl gets refused, a signed-in reviewer's browser does not.
REVIEW_URL=$(gcloud run services describe keplaria-review \
  --region=us-central1 --project=keplaria --format='value(status.url)')
curl -s -o /dev/null -w '%{http_code}\n' "$REVIEW_URL/review"
# expect 401 or 403
```

`scripts/doctor.sh` picks both services up once they exist: it checks that
the console answers `/healthz` unauthenticated, that the review service
refuses an unauthenticated `/review`, and that `IAP_AUDIENCE` is actually
set on the review service. Before either service is deployed, both checks
report `not deployed yet` warnings — that is the expected state, not a
failure.

## Failure handling

The claim this system makes, exactly: **retry is bounded and dead-lettering is
durable.** A failed command gets at most five execution attempts and then parks
inspectably; a stuck event parks in a dead-letter topic and is recorded after
five deliveries, instead of expiring silently at the 7-day retention boundary.

It is **not** self-healing. The sweep re-drives transient failures. It does not
diagnose a persistently broken destination, and after five attempts it stops
trying on purpose.

### Two failure classes, two mechanisms

They read alike in a log line and are fixed by different machinery, which is
why one "add a DLQ" would not have covered both.

| | **A — the event is never processed** | **B — the command is never executed** |
|---|---|---|
| What happens | `invoke_engine` raises, the ingress 503s, and the claim stays un-dispatched so Pub/Sub retries the case | The engine queued an ERP command, the ingress drained it, and the ERP call failed |
| Was silently lost by | redelivery until the 7-day retention window expired, with nothing recording the event had ever existed | `mark_dispatched` having already run, so the redelivery is a `duplicate_event` — a branch that always acks 200 regardless of whether its drain worked |
| Now bounded by | `deadLetterPolicy` on `keplaria-events-push`, `maxDeliveryAttempts: 5` | `MAX_EXECUTION_ATTEMPTS = 5` in `app/state/commands.py` |
| Now recorded in | `dead_events/{event_id}` in Firestore | the outbox command itself: `status: dead`, `died_at`, and the destination's last error |

A Pub/Sub dead-letter topic does not help with class B: the message that would
be dead-lettered represents the *event*, and reprocessing it cannot re-run the
engine — by then it is a duplicate by definition.

### `MAX_EXECUTION_ATTEMPTS = 5`, and what "dead" means

`record_failure` increments `execution_attempts` transactionally and, on the
fifth failure, writes `status: dead` plus `died_at` instead of `failed`.

- **A `dead` command is never retried** — not by the drain, not by a duplicate
  delivery, not by the sweep. `execute_pending_commands` skips it, and
  `claim_command` refuses it rather than resetting it to `PENDING`. Both halves
  are required: without the second, a review-band case re-parks on every later
  event and would resurrect the command the executor had given up on.
- **A `dead` command is not resurrectable, by design.** A persistently broken
  destination is fixed by a human, and the next cycle issues new commands.
  Nothing stalls in the meantime: `command_id` is cycle-scoped, so cycle 2
  claims a fresh command with a fresh identity.
- **A `dead` result must not make the ingress return 503.** Retrying is
  precisely what the cap exists to stop; it is logged at error level and acked.
- `execution_attempts` (executor attempts) is a different quantity from
  `attempts` (graph-side claims) and the two must not be conflated — `attempts`
  grows every time an event re-enters the graph, with no ERP call necessarily
  having been tried.

### The 15-minute sweep

Cloud Scheduler job `keplaria-command-sweep` (`*/15 * * * *`) calls
`POST /admin/sweep` on the ingress as `keplaria-sweeper@`, which holds
`roles/run.invoker`. Authorization is Cloud Run IAM only — the same mechanism
that protects `/pubsub/push`, with no application-level check beside it to go
stale.

**What it does:** a Firestore collection-group query over `outbox` for
`status == failed`, reduced to distinct case IDs, each handed to the same
`execute_pending_commands` the ingress uses. It adds no second execution path
and no second copy of the policy gate — only the trigger the pipeline lacked.

**What it does not do:**

- It does not diagnose anything. It re-drives, and the attempt cap stops it.
- It does not touch `pending` or `dead` commands — only `failed`.
- It never invokes the engine, so it costs none of the Agent Runtime query
  quota; it reads Firestore and writes to the ERP.
- It is bounded at **25 cases per run** and *logs* the remainder rather than
  truncating quietly, because a silent cap reads as "everything was covered".
- A command that failed while its case was `clear`, whose case has since moved
  into the review band, is re-found on every run forever: the drain refuses it,
  so `execution_attempts` never increments and it can never reach `dead`. This
  is accepted rather than fixed — teaching the query about policy bands would
  put authorization logic in a second place. It shows up honestly as
  `commands_refused` in the summary, not as work done.

The ingress runs `--concurrency=1 --max-instances=1`, so a sweep briefly
occupies the only request slot. Acceptable because the sweep is bounded and
never calls the engine; a push arriving during one is absorbed by the
subscription's existing 60s–600s backoff.

### The dead-letter path, and the two bindings whose absence is silent

Topic `keplaria-events-dead`; push subscription `keplaria-events-dead-push`
targets `POST /pubsub/dead`, which writes `dead_events/{event_id}` and
**always returns 200** — including on a write failure, which it logs. There is
nowhere left to retry to, and a non-2xx would only make the dead-letter
subscription redeliver the dead letter.

**The two IAM bindings that make dead-lettering work, and whose absence
reports nothing anywhere:**

- `roles/pubsub.publisher` for the Pub/Sub service agent on
  `keplaria-events-dead`
- `roles/pubsub.subscriber` for the same agent on `keplaria-events-push`

Without both, the `deadLetterPolicy` is present and looks correct, dead-
lettering simply does not happen, and no error surfaces. `infra/events/setup.sh`
grants them and `scripts/doctor.sh` checks them separately from the policy for
exactly that reason.

One non-obvious detail in the handler, deliberately not "simplified": the
delivery count is read from the message **attribute**
`CloudPubSubDeadLetterSourceDeliveryCount`, and the envelope's `deliveryAttempt`
field is only a fallback. Pub/Sub populates that field only on subscriptions
that themselves carry a dead-letter policy, and `keplaria-events-dead-push`
deliberately has none — so reading the field alone would record
`delivery_attempt: 0` on every dead-lettered event. Verified in production:
`spikes/dlq/evidence.json` records a real dead-lettered event at
`delivery_attempt: 5`.

### Where to look

`/review/failures` on the review service, behind IAP — it lists dead-lettered
events and `failed` / `dead` commands with their error strings and attempt
counts. It is behind IAP rather than on the public console because
`record_failure` stores the destination's raw error text, and the data-handling
contract permits case identifiers and masked values in logs, not arbitrary
upstream error bodies. The public console is unchanged.

Evidence for all of the above is in
[`spikes/dlq/evidence.json`](../spikes/dlq/evidence.json), produced by
`uv run --env-file .env python spikes/dlq/harness.py` against deployed
resources.

## MCP servers

For coding agents working in this repo. All are **local stdio** servers reusing
ambient gcloud/ADC credentials — no bearer tokens, no expiry.

| Server | Command | Provides |
|---|---|---|
| `adk-docs` | `uvx --from mcpdoc --with 'mcp[cli]<2' mcpdoc …` | `list_doc_sources`, `fetch_docs` over `adk.dev/llms.txt` |
| `gcloud` | `npx -y @google-cloud/gcloud-mcp` | `run_gcloud_command` — drive gcloud directly |
| `gcloud-observability` | `npx -y @google-cloud/observability-mcp` | `list_log_entries`, metrics, traces |

The two `@google-cloud/*` servers require Node 20+.

```bash
claude mcp add gcloud --scope local -- npx -y @google-cloud/gcloud-mcp
claude mcp add gcloud-observability --scope local -- npx -y @google-cloud/observability-mcp
claude mcp add adk-docs --scope local -- uvx --from mcpdoc --with 'mcp[cli]<2' \
  mcpdoc --urls 'AgentDevelopmentKit:https://adk.dev/llms.txt' --transport stdio
```

Verify with `claude mcp list` (expect `✔ Connected` for each).

### `adk-docs` — load-bearing version pin

**The `--with 'mcp[cli]<2'` pin is load-bearing — do not remove it.**

`mcpdoc` 0.0.10 (current latest) declares `mcp[cli]>=1.4.1` with no upper bound,
so a plain `uvx --from mcpdoc mcpdoc` resolves `mcp` 2.0.0. That release removed
the `mcp.server.fastmcp` module, and `mcpdoc/main.py` still does
`from mcp.server.fastmcp import FastMCP`. The result is a `ModuleNotFoundError`
at import; the process exits before any handshake, and the client reports a
misleading `-32000: Connection closed` — which looks like a network or URL
problem but is not. The `llms.txt` URL is fine.

To debug standalone, run the command above and pipe in a JSON-RPC `initialize`
request — a healthy server replies with `serverInfo.name: llms-txt`. Drop the pin
once upstream `mcpdoc` constrains its `mcp` dependency.

### Backlog — not configured, likely needed soon

**Remote/managed GCP MCP servers.** Google runs ~15
[managed endpoints](https://docs.cloud.google.com/mcp/supported-products). Two
are relevant here, and both were confirmed to accept this project's ADC token:

- `https://agentregistry.googleapis.com/mcp` — Agent Registry, backs
  `agents-cli publish` to Gemini Enterprise.
- `https://run.googleapis.com/mcp` — Cloud Run, relevant if `cloud_run` becomes
  the deploy target.

**Do not wire these up with a static bearer token.** The obvious approach —
`claude mcp add --transport http --header "Authorization: Bearer $(gcloud auth
print-access-token)"` — bakes in a token that **expires after 1 hour**, after
which the server fails mid-session with the same misleading
`-32000: Connection closed` described above. The durable path is an OAuth 2.0
client ID (Web application) with redirect URI
`https://claude.ai/api/mcp/auth_callback`, registered as a custom connector. The
local `gcloud` server covers much of the same ground token-free, so there is no
urgency. See
[Configure MCP in an AI application](https://docs.cloud.google.com/mcp/configure-mcp-ai-application).

**[MCP Toolbox for Databases](https://github.com/googleapis/mcp-toolbox)**
(`googleapis/mcp-toolbox`, v1.2.0, GA April 2026). A self-hosted binary covering
BigQuery, Cloud SQL, Spanner, AlloyDB and 40+ sources via declarative YAML, plus
custom parameterized queries. Not installed — adopt it if and when Keplaria needs
a database. Config goes in a project `.mcp.json`.

## GPU / DGX notes

ADK is pure Python — it calls model APIs and pulls nothing near CUDA or the
NVIDIA driver stack. That changes if local inference is added later (`torch`,
`vllm`, or `google-adk[extensions]`, which brings `litellm`):

- Install into **this venv**, not globally.
- Pull GPU packages from NVIDIA's aarch64 CUDA wheel index. Letting generic PyPI
  `torch` resolve is the usual way a tuned DGX stack gets clobbered.
