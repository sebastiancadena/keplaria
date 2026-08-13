# Keplaria

Agent project built on the [Google Agent Development Kit (ADK)](https://adk.dev)
for Python, targeting the
[Gemini Enterprise Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start).

## Architecture

The agent graph and its adapters run on two different runtimes:

- **Agent Runtime** hosts the ADK graph — reasoning engine `keplaria`
  (`projects/584548214478/locations/us-central1/reasoningEngines/2127503872455868416`).
  It reaches the private yente screening VM over the `keplaria-psc2` PSC-I
  network attachment and keeps agent execution state in Agent Platform
  Sessions.
- **Cloud Run** hosts `keplaria-ingress`, the authenticated Pub/Sub push
  adapter — the only public-facing entry point, and the component that talks
  to both Firestore and the ERP.

Everything is in `us-central1`.

### Event flow

```text
topic keplaria-events
  -> OIDC-authenticated push subscription keplaria-events-push
  -> private Cloud Run ingress (keplaria-ingress)
  -> Firestore inbox transaction (claims event_id, creates/advances the
     case, bumps case_version)
  -> Agent Runtime graph: parse -> LLM coordinator routing proposal ->
     deterministic policy validation (app/policy.py) -> yente screening
     over PSC-I -> queue ERP command
  -> ingress drains the command outbox and performs the ERP write
```

**The ERP executor runs in the ingress, not in the graph.** The
PSC-attached engine has no public internet egress — Cloud NAT is
`ENDPOINT_TYPE_VM`, which does not cover a PSC interface NIC — so the engine
itself cannot reach Frappe Cloud. The deterministic executor that performs
ERP writes therefore lives in the ingress process. This is a genuinely
separate component from the agent graph, by design, not a workaround.

**Fail-closed routing.** The LLM coordinator only proposes a route; a
deterministic policy layer (`app/policy.py`) decides whether it is
permitted. A refused proposal routes to a `quarantine_case` terminal node
that performs no Firestore command claim and no ERP write.

**Idempotency.** Every side effect is a Firestore command with a
deterministic ID (`{case_id}:{action}`). A command already `DONE` is never
re-driven, so a replayed event produces exactly one ERP write. ERP records
are keyed by supplier name, so a duplicate create collides natively (409)
and is reported rather than retried blindly.

### Operational constraints

- **Agent Runtime allows 1 concurrent query and 30 queries/min per
  region.** `keplaria-ingress` is deployed with `--concurrency=1
  --max-instances=1`, and the `keplaria-events-push` subscription carries an
  explicit retry policy (`60s`–`600s` backoff). Without both, a single
  rate-limit error becomes a self-sustaining redelivery storm: the ingress
  503s, Pub/Sub redelivers near-instantly, the engine takes another hit,
  guaranteed 429, repeat.
- **`GOOGLE_CLOUD_LOCATION` must be `global`, not `us-central1`** —
  `gemini-3.6-flash` 404s at the regional endpoint. `AGENT_ENGINE_LOCATION`
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

### Verification

- `scripts/doctor.sh` — 36 read-only checks covering toolchain, auth,
  provisioned infra, and the event-flow wiring (topic, push subscription
  OIDC, ingress auth, concurrency/maxScale, retry policy).
- `spikes/thin_vertical/verify.py` — proves the vertical end to end against
  the deployed engine and ingress, and writes
  `spikes/thin_vertical/evidence.json`. This is the current post-deploy
  verification script — see [Deploying](#deploying-to-agent-runtime).

## Setup from a fresh clone

Requires [uv](https://docs.astral.sh/uv/) and the `gcloud` CLI. uv provisions its
own Python — no system interpreter, pyenv, or Homebrew Python is involved.

```bash
uv sync                                       # creates .venv from uv.lock
gcloud auth application-default login         # once per machine
cp .env.example .env                          # then edit if needed
uv run adk web                                # ADK dev UI
```

## Environment

| | |
|---|---|
| Python | **3.13.13**, uv-managed (pinned in `.python-version`) |
| Package manager | **uv** |
| Venv | `.venv/` — self-contained, links to no system interpreter |
| ADK | `google-adk` 2.5.0 |
| Reference dev host | NVIDIA DGX Spark — Ubuntu 24.04, **aarch64** |
| Domain | **keplaria.com** — registered via Cloudflare Registrar (2026-08-11) |
| Cloudflare CLI | `wrangler` (nvm-managed Node), OAuth-authenticated |

The keplaria.com zone is live on Cloudflare nameservers; no DNS records point
anywhere yet. `wrangler` is logged in as the personal account (the same
identity as gcloud and git in this repo), with
write scopes for Workers, Pages, DNS routes, D1, KV, queues, email routing, and
SSL certs, so it can deploy and wire up DNS without re-authenticating. Brand
assets for the site live in the sibling repo `~/dev/git/keplaria-assets`.

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

## Google Cloud

| | |
|---|---|
| Project ID | `keplaria` (project number `584548214478`) |
| Region | `us-central1` |
| APIs enabled | Agent Platform (`aiplatform`), **Agent Registry (`agentregistry`)**, **Cloud Resource Manager**, Run, Compute, Firestore, Pub/Sub, Secret Manager, Cloud Scheduler, BigQuery, Cloud Trace, IAP, Model Armor, Cloud Build, Artifact Registry, Cloud Functions, Eventarc, Cloud Billing (+Budgets) |

`cloudresourcemanager.googleapis.com` is not optional: the GCP OpenTelemetry
resource detector and the Cloud Logging client both call it, and it was disabled
until 2026-08-13.

Authentication is via Application Default Credentials. Verify with:

```bash
gcloud config list                                  # expect project = keplaria
gcloud auth application-default print-access-token  # expect a token
```

`aiplatform.googleapis.com` is now surfaced as the **Agent Platform API** — the
Vertex AI API under its current name, not a separate product.

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
- **VM `keplaria-yente`** (`us-central1-c`): `t2d-standard-4` (e2 was stocked
  out region-wide on creation day), 60 GB pd-ssd, **no external IP**
  (10.10.0.2), service account `yente-vm@`. Nightly stop 01:00
  America/Bogota (`keplaria-nightly-stop`), daily snapshots, 7-day retention
  (`keplaria-daily-snap`). SSH:
  `gcloud compute ssh keplaria-yente --zone us-central1-c --tunnel-through-iap`
  — **the nightly stop has no matching start schedule, so the VM is
  `TERMINATED` most mornings and must be started by hand; `us-central1-c`
  returns capacity errors on start often enough to need a retry loop.**
- **Screening service** on that VM: yente + Elasticsearch, serving
  `10.10.0.2:8000` inside the VPC. Runbook, network posture, and the
  `/match` cutoff/threshold gotcha are in
  [`infra/yente/README.md`](infra/yente/README.md); the indexed data is the
  synthetic watchlist in [`fixtures/watchlist/`](fixtures/watchlist/).
  It fetches nothing from OpenSanctions — bulk-data rights are unconfirmed.
- **Billing guardrails:** budget `keplaria-build` alerts at $100/$130; budget
  `keplaria-killswitch` ($200) publishes to Pub/Sub topic `billing-killswitch`,
  where the Cloud Function in [`infra/billing-killswitch/`](infra/billing-killswitch/)
  **detaches billing from the project** once reported cost exceeds the budget
  (its SA holds `roles/billing.projectManager`, project-scoped). If it trips,
  everything stops — re-attach billing manually in the console to recover.
- `scripts/doctor.sh` verifies all of the above read-only.

Provisioning note: project-level IAM bindings and billing-account IAM cannot be
granted from an agent session (permission classifier); run those as the human
via `!`-prefixed commands.

## Configuration

Credentials and platform selection go in a `.env` at the project root. `.env` is
gitignored — never commit keys.

```dotenv
GOOGLE_GENAI_USE_ENTERPRISE=true
GOOGLE_CLOUD_PROJECT=keplaria
GOOGLE_CLOUD_LOCATION=us-central1
```

`GOOGLE_GENAI_USE_ENTERPRISE` routes the SDK to Gemini Enterprise Agent Platform
endpoints. `GOOGLE_GENAI_USE_VERTEXAI` is the **legacy alias** for the same
flag — prefer the former in new code. Setting both to conflicting values raises
`ValueError`. With ADC configured, no `GOOGLE_API_KEY` is needed; that variable
is for the Gemini Developer API instead.

**Region is `us-central1`** — the full GCP region name (there is no bare
`central1`; regions always carry the geography prefix). Use it consistently for
local runs, `agents-cli deploy --region`, and any infrastructure.
`GOOGLE_CLOUD_LOCATION` has no SDK default, so it must be set explicitly rather
than relied upon.

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

Verify afterwards with `uv run python spikes/thin_vertical/verify.py`, which
proves the event-to-ERP vertical end to end against the deployed engine and
ingress, and writes `spikes/thin_vertical/evidence.json`.

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
what refreshes it.** This matters because Agent Registry is the cataloging
surface the Fleet track claim points at, so a stale or debugging-flavoured name
is judge-visible.

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

`agents-cli deploy` reads `.env` and injects every key as a **plaintext runtime
env var, echoing them to stdout** — `FRAPPE_API_KEY` / `FRAPPE_API_SECRET`
currently ship this way. Move them to `--secrets ENV=SECRET` (Secret Manager)
when the scoped executor identity is built, and rotate.

## ERPNext (Frappe Cloud)

- Site: **`andina-foods.v.frappe.cloud`** on the $10 shared plan. The site was
  renamed after creation, so **dashboard URLs use the immutable internal name
  `erpnext-ojg-vfe.v.frappe.cloud`** (old host 308-redirects; note HTTP clients
  drop `Authorization` on cross-host redirects).
- Company: **Andina Foods** — currency USD, country Colombia, abbreviation
  `AF`, no demo data.
- API credentials: Secret Manager `frappe-api-key` / `frappe-api-secret`
  (mirrored in local `.env` as `FRAPPE_SITE` / `FRAPPE_API_KEY` /
  `FRAPPE_API_SECRET`). These are the owner's keys — rotate once a scoped
  executor identity exists.
- **Known issue:** the site's cron scheduler has never ticked (Frappe Cloud
  support ticket filed 2026-08-12), so Email Queue does not auto-flush.
  Outbound email works via API dispatch instead: queue with
  `communication.email.make`, then `email_queue.send_now` — proven in
  `spikes/frappe_capability/`. Probe for the fix: Email Queue `f7pj5o8901`
  flushing on its own + fresh `Scheduled Job Log` entries.

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
