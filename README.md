# Keplaria

Agent project built on the [Google Agent Development Kit (ADK)](https://adk.dev)
for Python, targeting the
[Gemini Enterprise Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start).

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
| APIs enabled | Agent Platform (`aiplatform`), Run, Compute, Firestore, Pub/Sub, Secret Manager, Cloud Scheduler, BigQuery, Cloud Trace, IAP, Model Armor, Cloud Build, Artifact Registry, Cloud Functions, Eventarc, Cloud Billing (+Budgets) |

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
- **VM `keplaria-yente`** (`us-central1-c`): `t2d-standard-4` (e2 was stocked
  out region-wide on creation day), 60 GB pd-ssd, **no external IP**
  (10.10.0.2), service account `yente-vm@`. Nightly stop 01:00
  America/Bogota (`keplaria-nightly-stop`), daily snapshots, 7-day retention
  (`keplaria-daily-snap`). SSH:
  `gcloud compute ssh keplaria-yente --zone us-central1-c --tunnel-through-iap`
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

### Scaffolded project (2026-08-12)

`agents-cli scaffold enhance` ran with `--deployment-target cloud_run
--session-type agent_platform_sessions --region us-central1`. Result:
`agents-cli-manifest.yaml`, agent code under `app/` (workflow in `agent.py`,
server in `fast_api_app.py`), `tests/`, `deployment/` (Terraform), `Dockerfile`.
Sessions are persistent: `app/app_utils/services.py` resolves an
`agentengine://` session service against the Agent Engine with display name
`keplaria` (created on first boot).

**Scaffold gotcha:** `scaffold enhance` runs in overwrite mode and
`shutil.rmtree`s existing directories — it **crashes on symlinked dirs**
(e.g. `strategy/`). To re-run it: copy the repo (minus symlinks and `.env`)
to a scratch dir, enhance there, port the output back, and fix the project
name it embeds from the directory name.

Spike harnesses (HITL resume across SIGKILL, retry-on-503, ERP capability
checks) live under `spikes/` — each is a standalone
`uv run python spikes/<name>/spike.py`.

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
