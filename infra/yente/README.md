# yente screening service

Self-hosted [yente](https://github.com/opensanctions/yente) + Elasticsearch on
the private `keplaria-yente` VM. This is the screening backend the Compliance
agent calls.

## Layout

| File | Role |
|---|---|
| `docker-compose.yml` | The two-container stack (`yente-app`, `yente-index`) |
| `manifest.yml` | yente data manifest — **fixture only**, `catalogs: []` |
| `push.sh` | Copies these files + the fixture to the VM's `~/yente-stack/`, run from the workstation |
| `deploy.sh` | Idempotent deploy/redeploy, run on the VM |
| `check.py` | Screening assertions (stdlib only), run on the VM |
| `verify.sh` | `check.py` + an egress assertion |

The indexed data is `fixtures/watchlist/entities.ftm.json` — synthetic and
rights-cleared. See that directory's README for provenance and for why the
OpenSanctions catalog is not used.

## Network posture

- VM has **no external IP**; SSH is IAP-only.
- `yente-app` publishes `0.0.0.0:8000`, so a workload entering the VPC through
  a **PSC-I network attachment** reaches it at `10.10.0.2:8000`.
- Ingress is confined by `keplaria-allow-internal` — `tcp:8000,9200` from
  `10.10.0.0/24` only. That range is also where network-attachment NICs land,
  so no new firewall rule is needed to wire the attachment.
- Elasticsearch is **not published** — compose-internal network only.
- `YENTE_AUTO_REINDEX=false` and `catalogs: []` mean the service performs no
  outbound data fetch at all.

## Operating it

Everything runs from the VM. Get in with:

```bash
gcloud compute ssh keplaria-yente --zone us-central1-c --tunnel-through-iap
```

Push the current stack files there first (from the workstation):

```bash
bash infra/yente/push.sh
```

Deploy or redeploy (idempotent; generates `/opt/yente/.env` on first run;
fails loudly if Elasticsearch does not report healthy within 300s):

```bash
bash ~/yente-stack/deploy.sh
```

Load or reload the fixture into the index:

```bash
cd /opt/yente && sudo docker compose exec -T app yente reindex
```

`reindex` is a no-op unless `version` in `manifest.yml` increased, so bump it
after every fixture edit.

Verify:

```bash
bash ~/yente-stack/verify.sh
```

## Restart behaviour

Both containers use `restart: unless-stopped` and `docker.service` is enabled,
so the stack returns on its own after a VM stop/start. The Elasticsearch index
lives in the `yente_index-data` volume on the root pd-ssd, which persists — no
reindex is needed after a reboot. Verified end-to-end on 2026-08-13 with a real
stop/start cycle.

**The VM itself does not auto-start.** `keplaria-nightly-stop` is a stop-only
schedule (`0 1 * * *`, America/Bogota) with no matching start, so every morning
the VM must be started by hand, and `us-central1-c` regularly refuses the start
with a capacity error.

**A retry loop is the wrong fix.** The stockout is per machine *family*, not
per zone, so retrying the same family spins for ten minutes and then fails
anyway. The disk is zonal, so swapping family is one command on the stopped VM:

```bash
gcloud compute instances set-machine-type keplaria-yente \
  --zone us-central1-c --machine-type n2-standard-4   # or e2-, t2d-standard-4
gcloud compute instances start keplaria-yente --zone us-central1-c
```

All three are 4 vCPU / 16 GB and yente does not care which it runs on. Do not
hardcode a family — the direction reverses over time (`e2` was out region-wide
on creation day, which is why this VM was originally `t2d`).

The manual start is fine during the build phase but is **incompatible with the
judging-window continuity requirement** (no manual VM start between Sept 1 and
Oct 1). Resolve before recording week — see the risk register.

**If the VM is gone rather than stopped, see [RECOVERY.md](RECOVERY.md)** — the
rebuild-from-snapshot path, what a snapshot does and does not carry, and the
non-destructive drill that proves it.

## Match semantics worth knowing

`/match` has two independent knobs, and the defaults hide things:

- `cutoff` (default `0.5`) — results scoring below this are **not returned at
  all**.
- `threshold` (default `0.7`) — results at or above this get `match: true`.

So a plausible near-name false positive can be invisible at defaults. The
fixture contains two deliberate decoy pairs; `check.py` asserts that the decoy
`syn-pe-006` is absent at default cutoff, appears at `cutoff=0.0` scoring
`0.606` against the true hit's `1.000`, and is never auto-flagged. Any
reviewer-facing surface should query with an explicit low cutoff and show the
scores, rather than trusting the default to have surfaced everything.
