# yente VM recovery

What to do when `keplaria-yente` is gone, broken, or unreachable. The screening
service has **no managed fallback** — lose it and every case scores
`SCREENING_UNAVAILABLE` (weight 0.30 against a 0.20 review threshold), so every
supplier lands `review` and parks at `awaiting_approval`. Nothing errors. The
symptom presents as a code bug, not an infrastructure one.

Runbook for operating the stack normally: [README.md](README.md).

## Diagnose first — three states that look alike

Run these in order. Do **not** skip to a restore; two of the three states need
no restore at all, and a restore costs ~2 minutes and a lot more risk.

```bash
gcloud compute instances list --filter=name=keplaria-yente \
  --format='table(name,status,machineType.basename(),networkInterfaces[0].networkIP)'
```

| What you see | State | Fix |
|---|---|---|
| No rows | Instance is gone | **Rebuild** — section below |
| `TERMINATED` | Nightly stop fired (normal; there is no start schedule) | `gcloud compute instances start keplaria-yente --zone us-central1-c` |
| `RUNNING` | Maybe serving, maybe not | Probe it — next block |

`RUNNING` is not `SERVING`. The containers take roughly 90s past boot, and
nothing outside the VPC can reach the service, so the only honest probe is over
IAP SSH. **Never read readiness from the serial console** — it carries no
container logs, and since the hostname is `keplaria-yente` every syslog line
contains the string "yente", so a grep for `yente.*ready` matches the word
"already" and reports a false READY.

```bash
gcloud compute ssh keplaria-yente --zone us-central1-c --tunnel-through-iap --quiet \
  --command 'sudo docker ps --format "{{.Names}}\t{{.Status}}"; \
             curl -s -o /dev/null -w "readyz=%{http_code}\n" localhost:8000/readyz'
```

Healthy is `yente-app` **and** `yente-index` both `(healthy)` plus
`readyz=200`. Anything else, and the stack — not the VM — is the problem:
`bash ~/yente-stack/deploy.sh` on the VM is idempotent and is the first thing
to try.

### If `start` refuses with a capacity error

`us-central1-c` stocks out **per machine family**, not per zone. A retry loop is
the wrong fix and will spin for ten minutes. The disk is zonal, so swapping
family is one command on the stopped VM; changing *zone* means imaging the disk
and rebuilding, and is rarely the right first move.

```bash
gcloud compute instances set-machine-type keplaria-yente \
  --zone us-central1-c --machine-type n2-standard-4   # or e2-, t2d-standard-4
gcloud compute instances start keplaria-yente --zone us-central1-c
```

All three are 4 vCPU / 16 GB and yente does not care which it runs on. **Do not
hardcode a family** — the direction reverses over time. `e2` was out
region-wide on creation day (which is why the VM was originally `t2d`); on
2026-08-19 `t2d` and `n2` both refused and `e2` started first try.

## Rebuild from snapshot

`keplaria-daily-snap` snapshots the boot disk at 06:00 daily. The index lives in
the `yente_index-data` Docker volume on that same root pd-ssd, so **the
snapshot carries the index** — a restored host serves without a reindex. That
is not an assumption; see *Drill* below.

Recovery point is the last 06:00 snapshot. Losing up to 24h of index state is
acceptable because the indexed data is a committed fixture
(`fixtures/watchlist/`), not accumulated state — if the snapshot is somehow
stale, reindex from the repo rather than hunting an older snapshot.

```bash
ZONE=us-central1-c

# 1. Newest snapshot of the boot disk.
SNAP=$(gcloud compute snapshots list --filter="sourceDisk~keplaria-yente$" \
  --sort-by=~creationTimestamp --limit=1 --format='value(name)')
echo "$SNAP"

# 2. Restore it. Match the original: pd-ssd, 60 GB, same zone.
gcloud compute disks create keplaria-yente --zone "$ZONE" \
  --type pd-ssd --source-snapshot "$SNAP"

# 3. Recreate the instance ON THE SAME INTERNAL IP (see below — this matters).
gcloud compute instances create keplaria-yente --zone "$ZONE" \
  --machine-type e2-standard-4 \
  --disk "name=keplaria-yente,boot=yes,auto-delete=no" \
  --subnet keplaria-uscentral1 --private-network-ip 10.10.0.2 --no-address \
  --service-account yente-vm@keplaria.iam.gserviceaccount.com \
  --scopes cloud-platform
```

Then wait for SERVING with the IAP probe above, and run the full assertion set
on the host:

```bash
gcloud compute ssh keplaria-yente --zone us-central1-c --tunnel-through-iap --quiet \
  --command 'bash ~/yente-stack/verify.sh'      # expect ALL CHECKS PASSED
```

`verify.sh` is what proves the *index* came back, not merely the filesystem:
it asserts `keplaria_synthetic` is the only indexed dataset, that `syn-co-001`
hits and is flagged, that the near-name decoy `syn-pe-006` surfaces
sub-threshold below the true hit, that a clean supplier flags nothing, and that
the app made no OpenSanctions fetch.

### Three things that make the rebuild work — and one that would break it

- **`10.10.0.2` must be reclaimed.** `app/nodes.py` reads `YENTE_BASE_URL`,
  which defaults to `http://10.10.0.2:8000` and is set to that in the deployed
  engine's environment. A restored host on any other address is invisible to
  the graph, and correcting it means **redeploying the engine** — slow, and the
  last thing you want mid-incident. The address is reserved
  (`keplaria-yente-ip`) precisely so nothing else can be handed it while the VM
  is down; `--private-network-ip 10.10.0.2` then always succeeds.
- **No network tags are needed.** `keplaria-allow-internal`,
  `keplaria-allow-iap-ssh`, and `keplaria-allow-psc-to-yente` are all scoped by
  source *range*, not by target tag, so any instance in `10.10.0.0/24` gets the
  same ingress. This is the usual rebuild trap and it does not apply here.
- **No external IP, ever.** `--no-address` is not optional. The VM's whole
  network posture is that it is unreachable from outside the VPC.
- **The service account must be attached.** `yente-vm@keplaria...` — omit it
  and the instance builds fine and serves fine, so the omission is silent.

### What is NOT covered by a snapshot restore

The PSC network attachment (`keplaria-psc2`) and the firewall rules are
independent resources; they survive the VM and need no action. `doctor.sh`
asserts both. If they are ever missing, the graph cannot reach yente no matter
how healthy the VM is — check doctor before suspecting the VM.

## Drill

`spikes/vm_recovery/drill.sh` runs the whole restore path **non-destructively**:
it restores the newest snapshot into a throwaway instance at `10.10.0.3` while
the live VM keeps serving `10.10.0.2`, waits for genuine SERVING, runs
`verify.sh` on the restored host, then deletes the instance and disk and checks
for residue.

```bash
bash spikes/vm_recovery/drill.sh          # KEEP=1 to leave it up for inspection
```

It refuses to start if a previous drill left an instance behind, and it walks
the machine-family list on a capacity refusal rather than retrying one family.
Result and timing: `spikes/vm_recovery/evidence.json`.

**Re-run it after any change to the VM, the stack, or the snapshot schedule.**
An untested backup is worth nothing, and this one went seven days untested.
