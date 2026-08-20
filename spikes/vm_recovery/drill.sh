#!/usr/bin/env bash
# yente VM recovery drill — restore the newest boot-disk snapshot into a
# throwaway instance BESIDE the live one, prove it serves and kept its index,
# then remove every trace.
#
# Non-destructive by construction: it never touches keplaria-yente, and the
# drill instance takes 10.10.0.3 so it cannot contend for the 10.10.0.2 the
# deployed graph screens against (app/nodes.py YENTE_BASE_URL).
#
#   bash spikes/vm_recovery/drill.sh            # full drill + teardown
#   KEEP=1 bash spikes/vm_recovery/drill.sh     # leave it up for inspection
#
# Writes spikes/vm_recovery/evidence.json.
set -uo pipefail

ZONE=us-central1-c          # home zone: where the live VM and its disk live
LIVE=keplaria-yente
DRILL=keplaria-yente-drill
DRILL_IP=10.10.0.3
SUBNET=keplaria-uscentral1
SA=yente-vm@keplaria.iam.gserviceaccount.com
# yente does not care which 4vCPU/16GB family it runs on; the us-central1-c
# stockout is per FAMILY, so the recovery path must be able to walk this list.
FAMILIES=(e2-standard-4 n2-standard-4 t2d-standard-4)
# ...and the zone is NOT a constraint either. keplaria-uscentral1 is a REGIONAL
# subnet and keplaria-psc2 a regional attachment, so a snapshot (a global
# resource) can be restored into any us-central1 zone and still hold 10.10.0.2,
# the same range-scoped firewall rules, and the same PSC path. Home zone first,
# because staying there keeps the rebuild identical to the original.
ZONES=(us-central1-c us-central1-a us-central1-b us-central1-f)
READY_BUDGET=300

OUT=spikes/vm_recovery
mkdir -p "$OUT"
LOG=$OUT/drill.log
: > "$LOG"

say() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }
now() { date +%s; }

fail() { say "DRILL FAILED: $*"; teardown; exit 1; }

teardown() {
  [ "${KEEP:-0}" = "1" ] && { say "KEEP=1 — leaving $DRILL up in ${USED_ZONE:-$ZONE}. Remove it yourself."; return; }
  say "--- teardown ---"
  # Sweep EVERY zone, not just the one that worked: a partial restore can leave
  # a disk behind in a zone whose instance create then refused.
  for z in "${ZONES[@]}"; do
    gcloud compute instances delete "$DRILL" --zone "$z" --quiet >>"$LOG" 2>&1 \
      && say "deleted instance $DRILL in $z"
    gcloud compute disks delete "$DRILL" --zone "$z" --quiet >>"$LOG" 2>&1 \
      && say "deleted disk $DRILL in $z"
  done
}

# ---------------------------------------------------------------- preflight
say "=== preflight ==="
live_status=$(gcloud compute instances describe "$LIVE" --zone "$ZONE" \
  --format='value(status)' 2>>"$LOG") || fail "cannot read the live VM"
say "live $LIVE is $live_status at $(gcloud compute instances describe "$LIVE" \
  --zone "$ZONE" --format='value(networkInterfaces[0].networkIP)')"

for z in "${ZONES[@]}"; do
  gcloud compute instances describe "$DRILL" --zone "$z" >/dev/null 2>&1 \
    && fail "$DRILL already exists in $z — a previous drill left residue. Remove it first."
done

SNAP=$(gcloud compute snapshots list --filter="sourceDisk~${LIVE}\$" \
  --sort-by=~creationTimestamp --limit=1 --format='value(name)' 2>>"$LOG")
[ -n "$SNAP" ] || fail "no snapshot of $LIVE found"
SNAP_TS=$(gcloud compute snapshots describe "$SNAP" --format='value(creationTimestamp)')
say "restoring from $SNAP (created $SNAP_TS)"

# ------------------------------------------------------------------ restore
T0=$(now)
say "=== restore ==="

created=""; USED_ZONE=""; REFUSALS=0
for z in "${ZONES[@]}"; do
  for fam in "${FAMILIES[@]}"; do
    if ! gcloud compute disks describe "$DRILL" --zone "$z" >/dev/null 2>&1; then
      gcloud compute disks create "$DRILL" --zone "$z" --type pd-ssd \
        --source-snapshot "$SNAP" >>"$LOG" 2>&1 \
        || { say "  disk restore failed in $z"; break; }
      say "  disk restored in $z ($(( $(now) - T0 ))s)"
    fi
    say "creating instance on $fam in $z ..."
    if gcloud compute instances create "$DRILL" --zone "$z" \
        --machine-type "$fam" --disk "name=$DRILL,boot=yes,auto-delete=no" \
        --subnet "$SUBNET" --private-network-ip "$DRILL_IP" --no-address \
        --service-account "$SA" --scopes cloud-platform >>"$LOG" 2>&1; then
      created=$fam; USED_ZONE=$z; break 2
    fi
    REFUSALS=$(( REFUSALS + 1 )); say "  $fam refused in $z — trying the next family"
  done
  say "  all families exhausted in $z — trying the next zone"
done
[ -n "$created" ] || fail "no 4vCPU capacity in any family in any us-central1 zone"
[ "$USED_ZONE" = "$ZONE" ] || say "NOTE: home zone $ZONE had no capacity; restored into $USED_ZONE"
T_CREATED=$(now)
say "instance up on $created in $USED_ZONE ($(( T_CREATED - T0 ))s from restore start)"

# ------------------------------------------------------- wait until SERVING
# RUNNING is not SERVING. Probe the service the way doctor.sh does: over IAP
# SSH, reading container health and /readyz. Nothing outside the VPC can reach
# this host at all, and the serial console does not carry container logs.
say "=== waiting for SERVING (budget ${READY_BUDGET}s) ==="
PROBE='sudo docker ps --format "{{.Names}}\t{{.Status}}"; echo "readyz=$(curl -s -o /dev/null -w %{http_code} localhost:8000/readyz)"'
serving=""
while [ $(( $(now) - T_CREATED )) -lt "$READY_BUDGET" ]; do
  probe=$(gcloud compute ssh "$DRILL" --zone "$USED_ZONE" --tunnel-through-iap --quiet \
            --command "$PROBE" 2>>"$LOG")
  if grep -q 'readyz=200' <<<"$probe" \
     && [ "$(grep -c '(healthy)' <<<"$probe")" -ge 2 ]; then
    serving=1; break
  fi
  sleep 10
done
T_SERVING=$(now)
[ -n "$serving" ] || fail "not SERVING within ${READY_BUDGET}s"
say "SERVING after $(( T_SERVING - T0 ))s total"
printf '%s\n' "$probe" | tee -a "$LOG"

# ------------------------------------------------- the index survived check
# Booting proves the disk restored. Only a real /match proves the Elasticsearch
# index came back with it — that is the thing an untested backup hides.
say "=== screening assertions on the restored host ==="
checks=$(gcloud compute ssh "$DRILL" --zone "$USED_ZONE" --tunnel-through-iap --quiet \
  --command 'bash ~/yente-stack/verify.sh' 2>>"$LOG")
printf '%s\n' "$checks" | tee -a "$LOG"
grep -q 'ALL CHECKS PASSED' <<<"$checks" || fail "screening assertions failed on the restored host"

RESTORE_SECONDS=$(( T_SERVING - T0 ))
say "=== PASS — restored and serving in ${RESTORE_SECONDS}s ==="

# ----------------------------------------------------------------- teardown
teardown

say "=== residue check ==="
residue=$(gcloud compute instances list --filter="name~drill" --format='value(name)';
          gcloud compute disks list --filter="name~drill" --format='value(name)')
if [ -n "$residue" ]; then say "RESIDUE LEFT: $residue"; else say "no residue"; fi

# ----------------------------------------------------------------- evidence
python3 - "$SNAP" "$SNAP_TS" "$created" "$RESTORE_SECONDS" "$residue" "$USED_ZONE" "$ZONE" \
  "$(( T_CREATED - T0 ))" "$(( T_SERVING - T_CREATED ))" "$REFUSALS" <<'PY'
import json, subprocess, sys
snap, snap_ts, family, secs, residue, used_zone, home_zone, to_instance, \
    boot_to_serving, refusals = sys.argv[1:11]
commit = subprocess.run(["git","rev-parse","--short","HEAD"],
                        capture_output=True, text=True).stdout.strip()
json.dump({
  "spike": "vm_recovery",
  "criterion": "the yente VM can be rebuilt from its daily snapshot and serve screening again, with the index intact",
  "result": "PASS",
  "date": subprocess.run(["date","-u","+%Y-%m-%dT%H:%M:%SZ"],
                         capture_output=True, text=True).stdout.strip(),
  "commit": commit,
  "method": "restore-beside — snapshot restored into a throwaway instance at "
            "10.10.0.3 while the live VM kept serving 10.10.0.2; torn down after",
  "source_snapshot": snap,
  "snapshot_created": snap_ts,
  "machine_family_used": family,
  "zone_used": used_zone,
  "home_zone": home_zone,
  "zone_fallback_exercised": used_zone != home_zone,
  "restore_to_serving_seconds": int(secs),
  "timing_breakdown": {
    "snapshot_to_running_instance_seconds": int(to_instance),
    "running_instance_to_serving_seconds": int(boot_to_serving),
    "note": "the total includes every refused create attempt and, when the "
            "zone fallback fires, a cross-zone snapshot restore (~117s "
            "measured) rather than the ~25s a same-zone restore takes. Read "
            "the total as an observed worst case, not a floor.",
  },
  "capacity_refusals": int(refusals),
  "serving_definition": "both containers (healthy) AND /readyz 200, probed over "
                        "IAP SSH — RUNNING is not SERVING and nothing outside "
                        "the VPC can reach the service",
  "index_survived": True,
  "index_evidence": "infra/yente/verify.sh ALL CHECKS PASSED on the restored "
                    "host: catalog is keplaria_synthetic only, syn-co-001 hits "
                    "and is flagged, decoy syn-pe-006 surfaces sub-threshold "
                    "below the true hit, clean supplier flags nothing, no "
                    "OpenSanctions egress in the app log",
  "residue": residue.split() or None,
  "log": "spikes/vm_recovery/drill.log",
  "runbook": "infra/yente/RECOVERY.md",
}, open("spikes/vm_recovery/evidence.json","w"), indent=2)
print("wrote spikes/vm_recovery/evidence.json")
PY
