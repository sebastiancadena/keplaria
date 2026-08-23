#!/usr/bin/env bash
# Environment doctor: verifies the local toolchain this project depends on.
# Safe to run anytime; read-only. Exits non-zero if any REQUIRED check fails.
set -uo pipefail
pass=0; fail=0; warn=0
ok()   { printf ' PASS  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf ' FAIL  %s\n' "$1"; fail=$((fail+1)); }
meh()  { printf ' WARN  %s\n' "$1"; warn=$((warn+1)); }

echo "== tool presence =="
for t in uv uvx gcloud node npx git; do
  command -v "$t" >/dev/null && ok "$t $($t --version 2>/dev/null | head -1)" || bad "$t not on PATH"
done
command -v agents-cli >/dev/null && ok "agents-cli $(agents-cli --version 2>/dev/null)" || bad "agents-cli missing (uv tool install google-agents-cli)"
command -v wrangler   >/dev/null && ok "wrangler $(wrangler --version 2>/dev/null)"     || meh "wrangler missing (needed for keplaria.com work)"
command -v gh         >/dev/null && ok "gh $(gh --version 2>/dev/null | head -1)"       || meh "gh missing"

echo "== auth =="
[ "$(gcloud config get-value project 2>/dev/null)" = "keplaria" ] \
  && ok "gcloud project = keplaria" || bad "gcloud project != keplaria"
gcloud auth application-default print-access-token >/dev/null 2>&1 \
  && ok "ADC token mints" || bad "ADC broken (do NOT fix by re-running gcloud init blindly)"
# `wrangler whoami` is a network call, so a blip reads as "not authenticated" —
# which points at exactly the wrong remedy, since re-running the OAuth login
# would disturb working credentials. Retry once and separate the two cases.
if command -v wrangler >/dev/null; then
  if wrangler whoami >/dev/null 2>&1 || wrangler whoami >/dev/null 2>&1; then
    ok "wrangler authenticated"
  elif curl -sf -o /dev/null --max-time 10 https://api.cloudflare.com/client/v4/user 2>/dev/null \
       || curl -s -o /dev/null --max-time 10 -w '%{http_code}' https://api.cloudflare.com 2>/dev/null | grep -q '^[2345]'; then
    meh "wrangler not authenticated (Cloudflare reachable, so this is the token — re-run 'wrangler login')"
  else
    meh "wrangler whoami failed AND api.cloudflare.com is unreachable — likely network, NOT the token; do not re-run 'wrangler login' on this alone"
  fi
fi
command -v gh >/dev/null && { gh auth status >/dev/null 2>&1 \
  && ok "gh authenticated" || meh "gh not authenticated"; }
# Offline presence check only — no API call. The key is needed solely to edit
# the published dev.to article (id 4437730) from docs/build-piece/article.dev.md.
grep -q '^DEVTO_API_KEY=.\+' .env.secrets 2>/dev/null \
  && ok "DEVTO_API_KEY present in .env.secrets (can edit the published dev.to article)" \
  || meh "DEVTO_API_KEY absent from .env.secrets (only needed to edit dev.to article 4437730)"

echo "== ERP maintenance tooling =="
[ -x scripts/erp.py ] \
  && ok "scripts/erp.py present (suppliers | cases | links | audit | purge --yes)" \
  || meh "scripts/erp.py missing or not executable — ERP cleanup falls back to ad-hoc one-offs"
# The pre-recording question this answers: is a watchlist entity on record?
# An exact string search does NOT answer it — the ERP once held
# 'Comercializadora Andes Verde SAS' while the watchlist carried the same
# entity as 'Comercializadora Andes Verde S.A.S.', and a grep called it clean.
[ -f fixtures/watchlist/entities.ftm.json ] \
  && ok "watchlist fixture present (scripts/erp.py audit can run)" \
  || meh "watchlist fixture missing — 'erp.py audit' cannot check for sanctioned records"

echo "== judge-visibility (private planning layer must not leak) =="
# The leak vector is our own writing: specs, plans and subagent briefs drafted
# from strategy/ carry risk IDs, plan-step labels and private filenames into
# code and commit messages when copied verbatim. Caught in app/risk.py and in a
# commit message on 2026-08-14. This grep is the automated backstop.
# 'Ground Control' was removed from this pattern on 2026-08-17: it is PUBLIC
# vocabulary (demo-and-video.md caps public terms at five and lists it), and
# the architecture diagram is its sanctioned use. The internal-only terms
# (Launch, Constellation, Docking, Debris shield, Splashdown) never appeared
# here because they never leaked; add one only if it does.
# 'feature-admission' added 2026-08-20: a private filename reached a draft of
# spikes/run_streak/harness.py and this grep did not catch it, because the
# pattern listed every other file in that directory but not this one.
# Dated GATE LABELS added 2026-08-20 for the same reason as the line above:
# 'day-14 scope lock' reached scripts/doctor.sh and a spike evidence file
# while this grep watched only for filenames and risk ids. A gate name
# tells a reader the private plan's shape as surely as its filename does.
# GENERIC forms added 2026-08-22, after 'the day-12 gate (2026-08-22) decided
# always-on' and 'rules #11' were written straight into this file and the cost
# spike. The previous list named the gates it had already seen leak, so it
# could only ever catch a repeat -- the two shapes here catch a gate or a rule
# clause nobody has leaked yet, which is the only kind worth checking for.
LEAK_RE='flight plan|architecture-contracts|risk-register|gates-and-cut|scoring-constitution|execution-plan|demo-and-video|feature-admission|scope lock|feature freeze|ambition gate|day-[0-9]+ gate|rules #[0-9]|\bR[1-9][0-9]?\b'
# -I skips binary files; the risk-id alternative can match arbitrary bytes in
# vendored binaries. We only care about text leaks from our own writing.
# The generated architecture.svg is excluded because its base64 font payloads
# false-positive the risk-id pattern; every human-readable string in it comes
# from docs/architecture/build.py, which IS grepped.
leak_files=$(git grep -lEi -I "$LEAK_RE" -- ':!strategy' ':!scripts/doctor.sh' ':!docs/architecture/architecture.svg' ':!docs/architecture/judge-diagram.svg' 2>/dev/null)
[ -z "$leak_files" ] \
  && ok "no private planning vocabulary in the tracked tree" \
  || bad "private planning vocabulary in tracked files: $(echo "$leak_files" | tr '\n' ' ')"
# Commit messages are covered by the same rule and were the harder miss.
# Whether the commit is already PUSHED changes what can be done about it, so
# say which: "squash before pushing" is unactionable advice for a commit that
# is already public, and a warning nobody can act on is a warning everybody
# learns to scroll past.
leak_commits=""
for _sha in $(git log --format='%H' -n 40); do
  git log -1 --format='%s%n%b' "$_sha" | grep -qEi "$LEAK_RE" || continue
  if git merge-base --is-ancestor "$_sha" origin/main 2>/dev/null; then
    leak_commits="$leak_commits $(git log -1 --format='%h' "$_sha")(pushed)"
  else
    leak_commits="$leak_commits $(git log -1 --format='%h' "$_sha")(local)"
  fi
done
if [ -z "$leak_commits" ]; then
  ok "last 40 commit messages carry no private vocabulary"
elif printf '%s' "$leak_commits" | grep -q '(local)'; then
  meh "private vocabulary in an UNPUSHED commit message — reword it before pushing:$leak_commits"
else
  meh "private vocabulary in an already-pushed commit message:$leak_commits — rewriting public history this close to submission costs more than the clause does; left deliberately, do not force-push to tidy it"
fi

echo "== project =="
[ -d .venv ] && uv lock --check >/dev/null 2>&1 \
  && ok "uv.lock consistent with pyproject" || meh ".venv/lock drift (run: uv sync)"
# uv run pytest is a SUBSET (-m 'not live'). Report both halves so a test that
# silently landed in a live-marked file is visible rather than invisible.
if [ -d .venv ]; then
  sel=$(uv run pytest --collect-only -q 2>/dev/null | grep -oE '[0-9]+/[0-9]+ tests collected' | head -1)
  live=$(uv run pytest -m live --collect-only -q 2>/dev/null | grep -oE '^[0-9]+/[0-9]+ tests collected|[0-9]+ tests collected' | head -1)
  [ -n "$sel" ] && ok "default suite selection: $sel (live-only: ${live:-unknown})" \
    || meh "could not determine test selection split"
fi

# The architecture diagram is generated and scored: a submitted diagram that
# no longer matches build.py is a stale artifact describing a system that does
# not exist. The build is deterministic, so a byte-compare is a real check.
if [ -d .venv ] && [ -f docs/architecture/build.py ]; then
  tmp_svg=$(mktemp)
  if KEPLARIA_DIAGRAM_OUT="$tmp_svg" uv run python docs/architecture/build.py >/dev/null 2>&1 \
     && cmp -s "$tmp_svg" docs/architecture/architecture.svg; then
    ok "architecture.svg matches build.py output (diagram not stale)"
  else
    bad "architecture.svg does NOT match build.py — regenerate: uv run python docs/architecture/build.py (and re-export the PNG)"
  fi
  rm -f "$tmp_svg"
fi

# The judge diagram is the one that goes on camera, where a stale box is worse
# than a stale poster: it is narrated aloud. Same deterministic byte-compare.
# The PNG is NOT checked -- it is a browser render, so it is not reproducible
# byte-for-byte here; re-export it whenever this check fires.
if [ -d .venv ] && [ -f docs/architecture/build_judge_diagram.py ]; then
  tmp_jsvg=$(mktemp)
  if KEPLARIA_JUDGE_DIAGRAM_OUT="$tmp_jsvg"        uv run python docs/architecture/build_judge_diagram.py >/dev/null 2>&1      && cmp -s "$tmp_jsvg" docs/architecture/judge-diagram.svg; then
    ok "judge-diagram.svg matches its build (video diagram not stale)"
  else
    bad "judge-diagram.svg does NOT match build_judge_diagram.py — regenerate it and re-export the PNG"
  fi
  rm -f "$tmp_jsvg"
fi

# The public site states every number this project makes. It is generated from
# the claim ledger, so the failure mode is not a wrong number -- it is a page
# built before the ledger last changed. Same byte-compare as the diagrams.
if [ -d .venv ] && [ -f site/build_site.py ]; then
  tmp_site=$(mktemp -d)
  if KEPLARIA_SITE_OUT="$tmp_site" uv run python site/build_site.py >/dev/null 2>&1 \
     && cmp -s "$tmp_site/index.html" site/dist/index.html \
     && cmp -s "$tmp_site/proof.html" site/dist/proof.html; then
    ok "site/dist matches build_site.py output (keplaria.com not stale)"
  else
    bad "site/dist is STALE — rebuild: uv run python site/build_site.py && (cd site && wrangler deploy)"
  fi
  rm -rf "$tmp_site"
fi

# The name is an argument, and it was dropped once already: when the strategy
# brief was decomposed into topic files, the section explaining why the project
# is called Keplaria was carried into none of them, and no public surface said
# it for weeks. A rewrite of the site copy can silently drop it again, so the
# published page is checked for it rather than trusted.
if [ -f site/dist/index.html ]; then
  if grep -qi "kepler" site/dist/index.html \
     && grep -qi "station-keeping" site/dist/index.html; then
    ok "the site still explains the name (Kepler + station-keeping)"
  else
    bad "the site no longer explains the name — the orbital frame was dropped from public copy once before; see CLAUDE.md"
  fi
fi

# The README header and the site's share card are both brand assets copied in
# from the sibling repo. A copy can drift from its source silently, and a
# missing file renders as a broken image on the repo's front page.
header_src="$HOME/dev/git/keplaria-assets/assets/social/github-social-card.png"
if [ -f docs/assets/github-header.png ] && [ -f "$header_src" ]; then
  if cmp -s docs/assets/github-header.png "$header_src"; then
    ok "README header matches the brand repo's card"
  else
    bad "docs/assets/github-header.png has drifted from the brand repo — re-copy it"
  fi
elif [ ! -f docs/assets/github-header.png ]; then
  bad "docs/assets/github-header.png missing — the README header will render broken"
fi

# The brand repo is a sibling, nothing imports it, and no build fails without
# it -- so its absence is silent until a visual artifact is quietly wrong.
brand_guidelines="$HOME/dev/git/keplaria-assets/docs/brand-guidelines.md"
if [ -f "$brand_guidelines" ]; then
  ok "brand repo present (read it before any visual work)"
else
  meh "brand repo missing at $brand_guidelines — visual work will drift"
fi

# The submission lists a code repository and a reproducible README as required
# deliverables, and the site links the repo as "Source". Every strategy file
# calls it "the public repo" -- but nobody ever checked, and it is private, so
# each of those links 404s for a judge. Assumed state, never verified: the same
# class of error as a correct consumer of state nobody read.
repo_vis=$(gh repo view sebastiancadena/keplaria --json visibility -q .visibility 2>/dev/null)
if [ "$repo_vis" = "PUBLIC" ]; then
  ok "the code repository is public (judges can open the link the submission gives them)"
elif [ -z "$repo_vis" ]; then
  meh "could not read repo visibility (gh auth?) — verify by hand before submitting"
else
  # DELIBERATE, user's decision 2026-08-22: the repo stays private while the
  # contest is open so the work is not copyable mid-competition. It MUST be
  # public before submitting -- the rules require a repository and a
  # reproducible README, and the submission and the site both link it. Tracked
  # as a dated gate row in strategy/STATUS.md so /gate-check surfaces it;
  # warning rather than failing so a known state does not mask a new one.
  meh "the code repository is $repo_vis — deliberate while the contest is open; MUST be public before submitting (tracked gate)"
fi

# The public site must actually be reachable: it is the URL the video shows.
site_code=$(curl -sS -o /dev/null -w '%{http_code}' -m 12 https://keplaria.com 2>/dev/null)
if [ "$site_code" = "200" ]; then
  ok "keplaria.com serves 200"
else
  bad "keplaria.com returned '$site_code' (expected 200)"
fi

echo "== cloud infra (read-only) =="
state=$(gcloud functions describe billing-killswitch --region=us-central1 --gen2 \
  --format='value(state,serviceConfig.environmentVariables.DRY_RUN)' --project=keplaria 2>/dev/null)
echo "$state" | grep -q 'ACTIVE' && ok "billing kill switch deployed" || bad "billing kill switch not ACTIVE"
echo "$state" | grep -qi 'false' && ok "billing kill switch ARMED (DRY_RUN=false)" || meh "billing kill switch in dry-run mode"
gcloud pubsub topics describe billing-killswitch --project=keplaria >/dev/null 2>&1 \
  && ok "billing-killswitch topic" || bad "billing-killswitch topic missing"
gcloud compute networks describe keplaria-vpc --project=keplaria >/dev/null 2>&1 \
  && ok "keplaria-vpc network" || bad "keplaria-vpc missing"
yente_status=$(gcloud compute instances list --filter=name=keplaria-yente \
  --format='value(status)' --project=keplaria 2>/dev/null)
case "$yente_status" in
  RUNNING)
    ok "keplaria-yente VM RUNNING"
    # RUNNING is not SERVING. The VM reports RUNNING long before yente has
    # loaded its index, and the graph's screening call (app/nodes.py) waits
    # 30s before giving up and recording SCREENING_UNAVAILABLE. A sequence
    # started inside that window pays the 30s on every screened beat, which is
    # enough on its own to blow a timed run. Probe the service, over IAP --
    # never the serial console, which reports a booted VM as a working one.
    yente_catalog=$(timeout 90 gcloud compute ssh keplaria-yente --zone=us-central1-c \
      --tunnel-through-iap --project=keplaria --quiet --command \
      'curl -sf -m 5 http://127.0.0.1:8000/catalog' 2>/dev/null)
    if printf '%s' "$yente_catalog" | grep -q 'keplaria_synthetic'; then
      ok "yente SERVING, keplaria_synthetic indexed"
    elif [ -n "$yente_catalog" ]; then
      meh "yente answers but keplaria_synthetic is absent from /catalog — screening would return nothing rather than fail"
    else
      meh "keplaria-yente RUNNING but not answering on 8000 (index still loading?) — every screened beat would wait 30s and then record SCREENING_UNAVAILABLE"
    fi
    ;;
  TERMINATED) meh "keplaria-yente VM TERMINATED — the hourly start schedule should raise it within the hour; if it does not, start it by hand. On a capacity error, switch machine family (set-machine-type e2-/n2-/t2d-standard-4) rather than retrying the same one — see README" ;;
  "")         meh "keplaria-yente VM not created (us-central1 stockout — retry loop?)" ;;
  *)          meh "keplaria-yente VM in state $yente_status" ;;
esac
gcloud compute firewall-rules describe keplaria-allow-internal --project=keplaria \
  --format='value(allowed[].map().firewall_rule().list())' 2>/dev/null | grep -q 'tcp:8000' \
  && ok "yente port 8000 open inside keplaria-vpc" || bad "keplaria-allow-internal missing tcp:8000 (PSC-I → yente will fail)"
# The screening path must come back up WITHOUT a human, through 2026-10-01.
# Until 2026-08-22 keplaria-yente carried a STOP-ONLY schedule, so it went down
# every night at 01:00 and stayed down until someone noticed. The schedule is
# DISCOVERED from the instance, never named here: replacing or renaming the
# policy must not be able to turn this green by accident, and a policy that
# stops without starting must read as the defect it is.
yente_start_sched=""
yente_stop_sched=""
for _pol in $(gcloud compute instances describe keplaria-yente --zone=us-central1-c \
  --project=keplaria --format='value(resourcePolicies)' 2>/dev/null \
  | tr ';,' '\n\n' | sed 's|.*/||' | grep -v '^$'); do
  _spec=$(gcloud compute resource-policies describe "$_pol" --region=us-central1 \
    --project=keplaria \
    --format='value(instanceSchedulePolicy.vmStartSchedule.schedule,instanceSchedulePolicy.vmStopSchedule.schedule)' 2>/dev/null)
  # Only instance-schedule policies answer here; snapshot schedules print nothing.
  [ -n "$(printf '%s' "$_spec" | tr -d '[:space:]')" ] || continue
  yente_start_sched="$(printf '%s' "$_spec" | cut -f1)"
  yente_stop_sched="$(printf '%s' "$_spec" | cut -f2)"
done
if [ -n "$yente_start_sched" ]; then
  ok "yente restarts itself without a human (start '$yente_start_sched'${yente_stop_sched:+, stop '$yente_stop_sched'})"
else
  bad "keplaria-yente has NO automatic start schedule — a nightly stop or a crash leaves the screening path down until a human starts it; the hosted path is committed to stay reachable through 2026-10-01"
fi

# --- yente recovery posture (drill: spikes/vm_recovery, runbook: infra/yente/RECOVERY.md) ---
# Two invariants that only matter on the worst day, which is exactly why they
# need a check: neither is visible in normal operation and both were absent
# until 2026-08-20.
#
# 1. The boot disk must OUTLIVE the instance. It shipped with autoDelete=true,
#    so deleting the VM -- the very first thing anyone does in a rebuild --
#    would have destroyed the disk, leaving only the last 06:00 snapshot.
auto_del=$(gcloud compute instances describe keplaria-yente --zone=us-central1-c \
  --project=keplaria --format='value(disks[0].autoDelete)' 2>/dev/null)
case "$auto_del" in
  False) ok "yente boot disk survives an instance delete (autoDelete=false)" ;;
  True)  bad "yente boot disk has autoDelete=TRUE — deleting the VM destroys the disk and the index with it; gcloud compute instances set-disk-auto-delete keplaria-yente --zone us-central1-c --disk keplaria-yente --no-auto-delete" ;;
  *)     meh "could not read yente boot-disk autoDelete flag" ;;
esac
# 2. 10.10.0.2 must be RESERVED, not ephemeral. app/nodes.py defaults
#    YENTE_BASE_URL to that address and the deployed engine carries it in its
#    environment, so a rebuild that cannot reclaim the address is invisible to
#    the graph and the only fix is redeploying the engine mid-incident.
addr=$(gcloud compute addresses describe keplaria-yente-ip --region=us-central1 \
  --project=keplaria --format='value(address,status)' 2>/dev/null)
if printf '%s' "$addr" | grep -q '10\.10\.0\.2'; then
  ok "10.10.0.2 reserved as keplaria-yente-ip (a rebuild can reclaim it)"
else
  bad "10.10.0.2 is NOT reserved — an ephemeral address can be handed to another VM while yente is down, and the graph reaches yente by that literal address"
fi
[ -f spikes/vm_recovery/evidence.json ] \
  && ok "VM recovery drill evidence present (spikes/vm_recovery/evidence.json)" \
  || bad "no VM recovery drill evidence — screening has no managed fallback, so the snapshot restore must be a tested path, not an assumed one; bash spikes/vm_recovery/drill.sh"

echo "== Agent Runtime deploy preconditions =="
[ -f .gcloudignore ] && grep -q '^strategy$' .gcloudignore \
  && ok ".gcloudignore excludes the private strategy symlinks" \
  || bad ".gcloudignore missing/incomplete — a deploy would package strategy/ into the container"
# app/risk.py's DEFAULT_POLICY_PATH resolves to this file at runtime — the
# first runtime-required file outside app/. If .gcloudignore ever excludes
# policy/, load_policy() fails closed on every case (POLICY_UNAVAILABLE ->
# blocked): a silent, total onboarding outage.
[ -f policy/supplier_risk.v2.json ] \
  && python3 -c "import json; json.load(open('policy/supplier_risk.v2.json'))" >/dev/null 2>&1 \
  && ok "policy fixture exists and parses (policy/supplier_risk.v2.json)" \
  || bad "policy fixture missing or does not parse — every case would fail closed to POLICY_UNAVAILABLE/blocked"
grep -qE '^policy/?$' .gcloudignore \
  && bad ".gcloudignore excludes policy/ — the runtime-required fixture would not ship, every case fails closed" \
  || ok "policy/ is not excluded from the deploy package"
# .gcloudignore controls the Cloud Build source upload; it does not control
# what lands inside the image — only the Dockerfile's COPY list does that.
# policy/ and fixtures/ live outside app/ (see app/risk.py DEFAULT_POLICY_PATH,
# app/documents.py FIXTURE_ROOT), so an unmodified `COPY ./app ./app`-only
# Dockerfile ships neither: load_policy() fails closed to blocked, and
# load_document() raises DocumentUnavailable on every documented event,
# quarantining the case before screening ever runs. Caught live on 2026-08-14
# — the deployed engine quarantined a lifecycle harness run at step 1 with
# zero indication in Firestore beyond "DocumentUnavailable" on a trace span.
grep -qE '^\s*COPY\s+\./policy\s+\./policy\s*$' Dockerfile \
  && ok "Dockerfile copies policy/ into the image" \
  || bad "Dockerfile does not COPY ./policy ./policy — load_policy() fails closed on every deployed case"
grep -qE '^\s*COPY\s+\./fixtures\s+\./fixtures\s*$' Dockerfile \
  && ok "Dockerfile copies fixtures/ into the image" \
  || bad "Dockerfile does not COPY ./fixtures ./fixtures — load_document() raises DocumentUnavailable on every deployed case with a document_ref"
gcloud compute network-attachments describe keplaria-psc2 --region=us-central1 \
  --project=keplaria >/dev/null 2>&1 \
  && ok "network attachment keplaria-psc2" || bad "keplaria-psc2 missing (PSC-I → yente will fail)"
gcloud compute firewall-rules describe keplaria-allow-psc-to-yente --project=keplaria \
  --format='value(allowed[].map().firewall_rule().list())' 2>/dev/null | grep -q 'tcp:8000' \
  && ok "PSC subnet may reach yente on 8000" \
  || bad "keplaria-allow-psc-to-yente missing tcp:8000 — the attachment NIC lands in 10.10.1.0/24, which keplaria-allow-internal does NOT cover"
# The producer PATCHes the attachment, so networkAdmin (not just networkUser) is required.
sa_roles=$(gcloud projects get-iam-policy keplaria --flatten='bindings[].members' \
  --filter='bindings.members:service-584548214478@gcp-sa-aiplatform.iam.gserviceaccount.com' \
  --format='value(bindings.role)' 2>/dev/null)
echo "$sa_roles" | grep -q 'compute.networkAdmin' \
  && ok "aiplatform SA has compute.networkAdmin (networkAttachments.update)" \
  || bad "aiplatform SA lacks compute.networkAdmin — PSC deploys 403 on networkAttachments.update"
re_roles=$(gcloud projects get-iam-policy keplaria --flatten='bindings[].members' \
  --filter='bindings.members:service-584548214478@gcp-sa-aiplatform-re.iam.gserviceaccount.com' \
  --format='value(bindings.role)' 2>/dev/null)
echo "$re_roles" | grep -q 'compute.networkUser' \
  && ok "aiplatform-re SA has compute.networkUser" || bad "aiplatform-re SA lacks compute.networkUser"
gcloud services list --enabled --project=keplaria 2>/dev/null | grep -q 'cloudresourcemanager' \
  && ok "cloudresourcemanager API enabled" || bad "cloudresourcemanager disabled (Cloud Logging + OTel resource detector need it)"
# Base image and requires-python must agree or the lock installs into the wrong venv.
img=$(grep -oP '^FROM python:\K[0-9]+\.[0-9]+' Dockerfile 2>/dev/null | head -1)
req=$(grep -oP 'requires-python\s*=\s*">=\K[0-9]+\.[0-9]+' pyproject.toml 2>/dev/null | head -1)
[ -n "$img" ] && [ "$img" = "$req" ] \
  && ok "Dockerfile python:$img matches requires-python >=$req" \
  || bad "Dockerfile python:${img:-?} vs requires-python >=${req:-?} — builds clean, dies on import"

# One engine named keplaria. services.py finds-or-creates by display name, so a
# second engine means a stray duplicate and a non-deterministic session backend.
# There is no `gcloud ai reasoning-engines` surface — this is REST-only.
names=$(curl -s -m 30 -H "Authorization: Bearer $(gcloud auth print-access-token 2>/dev/null)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/584548214478/locations/us-central1/reasoningEngines" \
  2>/dev/null | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
print("\n".join(e.get("displayName","?") for e in d.get("reasoningEngines",[])))' 2>/dev/null)
rc=$?
count=$(printf '%s' "$names" | grep -c . || true)
if [ "$rc" -ne 0 ]; then
  meh "could not list reasoning engines (API/auth?)"
elif [ "$count" -eq 1 ] && [ "$names" = "keplaria" ]; then
  ok "exactly one reasoning engine, named keplaria"
elif [ "$count" -eq 0 ]; then
  bad "no reasoning engine deployed — the agent graph is down (judging needs it through Oct 1)"
else
  bad "expected 1 engine named keplaria, found $count: $(printf '%s' "$names" | tr '\n' ' ')— strays surface in Agent Registry"
fi

echo "== thin vertical =="
gcloud firestore databases list --format='value(name)' --project=keplaria 2>/dev/null \
  | grep -q '(default)$' \
  && ok "firestore (default) database" || bad "firestore (default) database missing"
gcloud pubsub topics describe keplaria-events --project=keplaria >/dev/null 2>&1 \
  && ok "keplaria-events topic" || bad "keplaria-events topic missing"
gcloud pubsub subscriptions describe keplaria-events-push --project=keplaria \
  --format='value(pushConfig.oidcToken.serviceAccountEmail)' 2>/dev/null \
  | grep -q 'keplaria-pubsub-push' \
  && ok "push subscription authenticates with OIDC" \
  || bad "keplaria-events-push missing its OIDC service account — the endpoint would accept anonymous pushes"
ingress_url=$(gcloud run services describe keplaria-ingress --region=us-central1 \
  --format='value(status.url)' --project=keplaria 2>/dev/null)
if [ -n "$ingress_url" ]; then
  # /healthz is NOT valid auth evidence here: it 404s both anonymously and
  # with a valid identity token, so it proves nothing about the IAM check.
  # POST /pubsub/push goes through the same Cloud Run IAM gate and is
  # rejected before the app code ever sees it, so it is a real witness.
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${ingress_url}/pubsub/push" \
    -H 'Content-Type: application/json' -d '{"message":{"data":""}}' 2>/dev/null)
  case "$code" in
    401|403) ok "ingress rejects anonymous POST /pubsub/push ($code)" ;;
    *)       bad "ingress returned $code to an anonymous POST /pubsub/push — expected 401/403" ;;
  esac
else
  bad "keplaria-ingress service not deployed"
fi
gcloud components list --filter='id=cloud-firestore-emulator' --format='value(state)' 2>/dev/null \
  | grep -q 'Installed' \
  && ok "firestore emulator component installed" \
  || meh "firestore emulator component not installed (gcloud components install cloud-firestore-emulator)"
# The Agent Runtime engine allows only 1 concurrent query; a serialised
# ingress is what stops a single 429 from becoming a redelivery storm (see
# infra/events/setup.sh for the incident this guards against).
concurrency_scale=$(gcloud run services describe keplaria-ingress --region=us-central1 \
  --project=keplaria \
  --format='value(spec.template.spec.containerConcurrency,spec.template.metadata.annotations["autoscaling.knative.dev/maxScale"])' 2>/dev/null)
if [ "$concurrency_scale" = "$(printf '1\t1')" ]; then
  ok "ingress containerConcurrency=1 and maxScale=1 (serialised against the engine's 1-concurrent-query quota)"
else
  bad "ingress concurrency/maxScale = '${concurrency_scale:-unknown}', expected 1 and 1 — a 429 can become a redelivery storm"
fi
retry_backoff=$(gcloud pubsub subscriptions describe keplaria-events-push --project=keplaria \
  --format='value(retryPolicy.minimumBackoff)' 2>/dev/null)
[ -n "$retry_backoff" ] \
  && ok "keplaria-events-push has a retry policy (minimumBackoff=$retry_backoff)" \
  || bad "keplaria-events-push has no retry policy — a 429/503 redelivers near-instantly and can exhaust the engine quota"

echo "== console + review services =="
console_url=$(gcloud run services describe keplaria-console --region=us-central1 \
  --format='value(status.url)' --project=keplaria 2>/dev/null)
if [ -n "$console_url" ]; then
  # Probes "/", not "/healthz". Observed 2026-08-16: a request to /healthz on a
  # *.run.app URL comes back 404 with a Google-styled error page, on BOTH this
  # service and keplaria-ingress, even though each registers the route and the
  # deployed console's own /openapi.json lists it. Every other path on the same
  # services reaches the container. Whatever eats it sits in front of Cloud Run,
  # so a /healthz probe can never pass from outside and says nothing about the
  # app. "/" is the better check anyway: it is the page a judge actually opens.
  code=$(curl -s -o /dev/null -w '%{http_code}' "$console_url/")
  [ "$code" = "200" ] && ok "public console answers unauthenticated (200)" \
    || bad "public console returned $code unauthenticated"
else
  meh "keplaria-console not deployed yet"
fi

review_url=$(gcloud run services describe keplaria-review --region=us-central1 \
  --format='value(status.url)' --project=keplaria 2>/dev/null)
if [ -n "$review_url" ]; then
  # The whole point of the service: an unauthenticated caller gets nothing.
  code=$(curl -s -o /dev/null -w '%{http_code}' "$review_url/review")
  case "$code" in
    401|403) ok "review service refuses unauthenticated callers ($code)" ;;
    302|303|307)
      # IAP fronts a browser surface, so it bounces an anonymous caller to
      # Google sign-in instead of refusing flatly. That is still "gets
      # nothing" — but only if the bounce goes to Google. A redirect
      # anywhere else would mean something other than IAP answered.
      loc=$(curl -sI "$review_url/review" | tr -d '\r' | sed -n 's/^[Ll]ocation: //p')
      case "$loc" in
        https://accounts.google.com/*)
          ok "review service bounces anonymous callers to Google sign-in ($code)"
          # The scopes in that redirect decide whether a judge can sign in at
          # all. Google requires OAuth verification only for SENSITIVE or
          # RESTRICTED scopes; on 2026-08-20 IAP asked for `openid email`,
          # both non-sensitive, which is why the console's standing "your app
          # requires verification" banner does not gate access — proven the
          # same day by an outside Workspace account clearing consent and
          # being refused by IAP's access list instead. Anything beyond these
          # three would make verification mandatory and lock every judge out
          # of a service that still looks healthy from here.
          scopes=$(printf '%s' "$loc" | sed -n 's/.*[?&]scope=\([^&]*\).*/\1/p' \
            | sed 's/%20/ /g; s/+/ /g')
          extra=$(printf '%s' "$scopes" | tr ' ' '\n' \
            | grep -vE '^(openid|email|profile)$' | tr '\n' ' ')
          [ -z "$extra" ] \
            && ok "IAP requests only non-sensitive scopes (${scopes:-none}) — no OAuth verification needed" \
            || bad "IAP now requests sensitive scopes ($extra) — verification becomes mandatory and judges are blocked at consent" ;;
        *)
          bad "review service redirected ($code) somewhere other than Google sign-in: ${loc:-<none>}" ;;
      esac ;;
    *) bad "review service returned $code to an unauthenticated caller" ;;
  esac
  aud=$(gcloud run services describe keplaria-review --region=us-central1 \
    --project=keplaria --format='value(spec.template.spec.containers[0].env)' 2>/dev/null \
    | grep -c IAP_AUDIENCE)
  [ "$aud" -ge 1 ] && ok "review service has IAP_AUDIENCE set" \
    || bad "review service missing IAP_AUDIENCE — it will refuse every decision"

  # The two accounts the organizers gave repository access must also be able
  # to open the review console for themselves, without asking anyone. Read the
  # live IAP policy rather than trusting the runbook — a grant made by hand is
  # exactly the kind that gets lost in a re-provision.
  #
  # Each account is matched by REGEX, not by the address the rules quote, and
  # not by principal type. Both reasons were observed on 2026-08-20 while
  # making the grant: testing@ is a Google GROUP, so `user:` is rejected
  # outright with a type error — and IAM then stored it under Devpost's older
  # challengepost.com domain, because devpost.com is an alias of it. A grep for
  # the quoted address would report a binding missing while it sits in the
  # policy under its canonical name, which is the same false refusal this
  # check exists to catch.
  iap_policy=$(gcloud iap web get-iam-policy --resource-type=cloud-run \
    --region=us-central1 --service=keplaria-review --project=keplaria \
    --format='value(bindings.members)' 2>/dev/null)
  missing=""
  for judge in 'Devpost testing:testing@(devpost|challengepost)\.com' \
               'Google hackathons:cloudhackathons@google\.com'; do
    label=${judge%%:*}
    pattern=${judge#*:}
    echo "$iap_policy" | grep -qE "$pattern" || missing="$missing $label"
  done
  [ -z "$missing" ] \
    && ok "both judging accounts hold IAP access to the review console" \
    || bad "judging accounts missing IAP access:$missing — a judge gets a Google sign-in they cannot pass"
else
  meh "keplaria-review not deployed yet"
fi

echo "== MCP: adk-docs probe (known failure mode: mcp>=2 breaks mcpdoc with a misleading -32000) =="
probe='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"doctor","version":"0"}}}'
if command -v uvx >/dev/null; then
  resp=$(printf '%s\n' "$probe" | timeout 60 uvx --from mcpdoc --with 'mcp[cli]<2' \
    mcpdoc --urls 'AgentDevelopmentKit:https://adk.dev/llms.txt' --transport stdio 2>/dev/null | head -1)
  echo "$resp" | grep -q 'llms-txt' && ok "adk-docs MCP server handshakes" || meh "adk-docs MCP probe failed (check network / mcp pin)"
fi

echo "== dead-letter and sweep =="
DLQ_TOPIC_PATH=$(gcloud pubsub topics describe keplaria-events-dead \
  --project=keplaria --format='value(name)' 2>/dev/null || true)
[ -n "$DLQ_TOPIC_PATH" ] \
  && ok "dead-letter topic keplaria-events-dead exists" \
  || bad "dead-letter topic keplaria-events-dead missing — a stuck event is dropped at retention"

DLQ_ATTEMPTS=$(gcloud pubsub subscriptions describe keplaria-events-push \
  --project=keplaria --format='value(deadLetterPolicy.maxDeliveryAttempts)' 2>/dev/null || true)
[ "$DLQ_ATTEMPTS" = "5" ] \
  && ok "keplaria-events-push dead-letters after 5 deliveries" \
  || bad "keplaria-events-push has no dead-letter policy (maxDeliveryAttempts='${DLQ_ATTEMPTS:-unset}') — stuck events expire silently"

# Both bindings or dead-lettering silently does not happen. Checked separately
# from the policy above because the policy can be set and look correct while
# the agent lacks permission to act on it.
PROJECT_NUMBER=$(gcloud projects describe keplaria --format='value(projectNumber)' 2>/dev/null || true)
PUBSUB_AGENT="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud pubsub topics get-iam-policy keplaria-events-dead --project=keplaria \
  --format=json 2>/dev/null | grep -q "$PUBSUB_AGENT" \
  && ok "pubsub service agent can publish to the dead-letter topic" \
  || bad "pubsub service agent lacks publisher on keplaria-events-dead — dead-lettering will silently not happen"
gcloud pubsub subscriptions get-iam-policy keplaria-events-push --project=keplaria \
  --format=json 2>/dev/null | grep -q "$PUBSUB_AGENT" \
  && ok "pubsub service agent can ack on keplaria-events-push" \
  || bad "pubsub service agent lacks subscriber on keplaria-events-push — dead-lettering will silently not happen"

SWEEP_STATE=$(gcloud scheduler jobs describe keplaria-command-sweep \
  --location=us-central1 --project=keplaria --format='value(state)' 2>/dev/null || true)
[ "$SWEEP_STATE" = "ENABLED" ] \
  && ok "keplaria-command-sweep is ENABLED (failed commands are re-driven unattended)" \
  || bad "keplaria-command-sweep state='${SWEEP_STATE:-missing}' — a failed command gets no second chance"

# The emulator does not enforce collection-group indexes, so the sweep's unit
# tests pass locally whether or not this exists in production.
#
# The sweep's query filters a collection GROUP on ONE field (status), which
# Firestore serves from a SINGLE-FIELD index whose queryScope is
# COLLECTION_GROUP — not from a composite. `gcloud firestore indexes composite
# list` therefore never lists it no matter how long you wait, so a check
# written against that command can only ever report missing. The right command
# is `gcloud firestore indexes fields list`; see README "Firestore indexes" for
# how the index itself is created (REST PATCH — no gcloud verb can express
# query scope).
#
# Checked in BOTH databases, and named individually when one is missing. The
# index is needed in "(default)" for the deployed sweep and /review/failures,
# and in "keplaria-test" because that is the database `uv run pytest` uses
# whenever FIRESTORE_EMULATOR_HOST is unset (see tests/conftest.py). A check
# that inspects only "(default)" reports green while the repo's own default
# test run fails on a missing index in the other database.
missing_index=""
for fsdb in "(default)" "keplaria-test"; do
  gcloud firestore indexes fields list --database="$fsdb" --project=keplaria \
    --format=json 2>/dev/null \
    | FSDB="$fsdb" python3 -c '
import json, os, sys
try:
    fields = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for f in fields:
    if not f.get("name", "").endswith("/collectionGroups/outbox/fields/status"):
        continue
    for idx in f.get("indexConfig", {}).get("indexes", []):
        scoped = idx.get("queryScope") == "COLLECTION_GROUP"
        on_status = [x.get("fieldPath") for x in idx.get("fields", [])] == ["status"]
        if scoped and on_status and idx.get("state") == "READY":
            sys.exit(0)
sys.exit(1)
' 2>/dev/null \
    || missing_index="${missing_index}${fsdb} "
done
[ -z "$missing_index" ] \
  && ok "outbox/status COLLECTION_GROUP index READY in (default) and keplaria-test (the sweep's query needs it)" \
  || bad "no READY outbox/status COLLECTION_GROUP index in: ${missing_index}— the sweep query and /review/failures FailedPrecondition there; the emulator hides this because it auto-indexes"


echo "== cost observability (a budget that watches nothing reports 0.00 forever) =="
# Two ways the spend numbers can go quietly wrong, both of which look like good
# news rather than like breakage:
#
#   1. A budget scoped to the wrong project reports 0.0 on every notification.
#      Nothing errors; the alert simply never fires. So the project filter is
#      verified rather than assumed.
#   2. Every alerting budget here includes credits, so all of them read 0.0
#      while a credit covers the account. `keplaria-gross-observe` is the only
#      one that excludes credits and therefore the only source of the real burn
#      rate. If it is deleted or flipped to include credits, gross visibility
#      disappears silently.
billing_account="$(gcloud billing projects describe keplaria \
  --format='value(billingAccountName)' 2>/dev/null | sed 's|billingAccounts/||')"
if [ -z "$billing_account" ]; then
  bad "project keplaria has no billing account attached (the kill switch detaches it — re-attach in the console)"
else
  budget_report="$(gcloud billing budgets list --billing-account="$billing_account" \
    --format=json 2>/dev/null \
    | python3 -c '
import json, sys
try:
    budgets = json.load(sys.stdin)
except Exception:
    print("ERR"); sys.exit(0)
want = "584548214478"
misscoped, observer = [], None
for b in budgets:
    f = b.get("budgetFilter", {})
    projects = f.get("projects") or []
    name = b.get("displayName", "?")
    if projects and not any(p.endswith(want) for p in projects):
        misscoped.append(name)
    if name == "keplaria-gross-observe":
        observer = f.get("creditTypesTreatment")
print("MISSCOPED=" + ",".join(misscoped))
print("OBSERVER=" + str(observer))
' 2>/dev/null)"
  case "$budget_report" in
    *ERR*) bad "could not list budgets on $billing_account" ;;
    *)
      misscoped="$(printf '%s\n' "$budget_report" | sed -n 's/^MISSCOPED=//p')"
      observer="$(printf '%s\n' "$budget_report" | sed -n 's/^OBSERVER=//p')"
      [ -z "$misscoped" ] \
        && ok "every project-scoped budget points at keplaria (584548214478)" \
        || bad "budget(s) scoped to another project: ${misscoped} — these report 0.00 forever and can never fire"
      case "$observer" in
        EXCLUDE_ALL_CREDITS)
          ok "keplaria-gross-observe excludes credits (the only source of gross burn rate)" ;;
        None)
          bad "budget keplaria-gross-observe is missing — gross burn is unobservable; every other budget nets out credits and reads 0.00" ;;
        *)
          bad "keplaria-gross-observe has creditTypesTreatment=${observer}, not EXCLUDE_ALL_CREDITS — it now reports the same netted 0.00 as the others" ;;
      esac ;;
  esac
fi

# The BigQuery billing export is the only per-SKU source, and it is forward-only:
# if it is switched off, the gap in the data can never be backfilled. The console
# grants this service account OWNER on the dataset when the export is saved, so
# the grant is the honest signal that the export is still configured.
bq --project_id=keplaria show --format=prettyjson keplaria:billing_export 2>/dev/null \
  | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for a in d.get("access", []):
    if a.get("userByEmail") == "billing-export-bigquery@system.gserviceaccount.com":
        sys.exit(0)
sys.exit(1)
' 2>/dev/null \
  && ok "BigQuery billing export still wired to keplaria:billing_export" \
  || bad "billing export service account has no access to keplaria:billing_export — the export is off, and it is forward-only so the gap is unrecoverable"

# The kill switch detaching billing takes the whole project down, including the
# judge-facing path, and it does not recover on its own. Before 2026-08-20 it
# announced this only by printing to a log nobody reads. The alert policy is the
# difference between noticing in minutes and noticing when someone opens the
# demo — so its existence, and its having somewhere to send to, are checked.
alert_state="$(gcloud alpha monitoring policies list --project=keplaria \
  --format=json 2>/dev/null \
  | python3 -c '
import json, sys
try:
    policies = json.load(sys.stdin)
except Exception:
    print("ERR"); raise SystemExit
for p in policies:
    conds = p.get("conditions", [])
    if not any("conditionMatchedLog" in c and "BILLING DETACHED" in
               c.get("conditionMatchedLog", {}).get("filter", "") for c in conds):
        continue
    if not p.get("enabled", False):
        print("DISABLED")
    elif not p.get("notificationChannels"):
        print("NOCHANNEL")
    else:
        print("OK")
    raise SystemExit
print("MISSING")
' 2>/dev/null)"
case "$alert_state" in
  OK)        ok "billing-detach alert exists, enabled, with a notification channel" ;;
  MISSING)   bad "no alert policy watches for BILLING DETACHED — the kill switch would take the project down silently (infra/monitoring/alert-billing-detached.json)" ;;
  DISABLED)  bad "the billing-detach alert policy is DISABLED — a detach would go unannounced" ;;
  NOCHANNEL) bad "the billing-detach alert policy has no notification channel — it would fire into nothing" ;;
  ERR)       warn "could not list alert policies to check the billing-detach alert" ;;
esac


echo "== deploy-time secret hygiene =="
# `agents-cli deploy` reads .env and injects EVERY key in it as a PLAINTEXT
# runtime env var on the engine, echoing the values to stdout as it goes. There
# is no flag to exclude one, so the only control is which file a secret lives
# in: .env ships, .env.secrets does not. A secret added to .env leaks on the
# next deploy and nothing fails at the time — the deploy succeeds, and the
# value simply becomes readable to anyone with viewer access on the engine.
#
# Matched on key NAME, not on the value, so a rotated credential is still
# caught. Add new secret-shaped names here as they appear.
if [ -f .env ]; then
  leaked="$(grep -oE '^[[:space:]]*[A-Z0-9_]*(SECRET|API_KEY|TOKEN|PASSWORD|CREDENTIAL)[A-Z0-9_]*[[:space:]]*=' .env \
    | tr -d ' =' | sort -u | paste -sd, - || true)"
  [ -z "$leaked" ] \
    && ok ".env carries no secret-shaped keys (agents-cli would deploy them as plaintext)" \
    || bad ".env contains secret-shaped key(s): ${leaked} — the next agents-cli deploy injects these as plaintext env on the engine and echoes them to stdout; move them to .env.secrets"
else
  warn ".env not found — skipping the deploy-time secret check"
fi

# .gcloudignore REPLACES .gitignore at deploy time, so a file being gitignored
# says nothing about whether it is uploaded into the container.
if [ -f .gcloudignore ]; then
  missing=""
  for f in .env .env.secrets; do
    grep -qxF "$f" .gcloudignore || missing="${missing}${f} "
  done
  [ -z "$missing" ] \
    && ok ".gcloudignore excludes .env and .env.secrets from the deployed container" \
    || bad ".gcloudignore does not exclude: ${missing}— it REPLACES .gitignore at deploy time, so these ship inside the image"
fi

# The engine has no public internet egress and its graph never imports the
# executor, so it cannot reach the ERP at all. Any Frappe credential on it is
# both unused and readable — belt and braces against a redeploy putting them back.
engine_env="$(curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/keplaria/locations/us-central1/reasoningEngines/2127503872455868416" 2>/dev/null \
  | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("ERR"); raise SystemExit
env = d.get("spec", {}).get("deploymentSpec", {}).get("env", []) or []
bad = [e["name"] for e in env
       if any(t in e["name"] for t in ("SECRET", "API_KEY", "TOKEN", "PASSWORD"))]
print(",".join(bad))
' 2>/dev/null)"
case "$engine_env" in
  ERR) warn "could not read the engine deployment spec to check for plaintext secrets" ;;
  "")  ok "deployed engine carries no secret-shaped plaintext env vars" ;;
  *)   bad "deployed engine carries plaintext secret env var(s): ${engine_env} — readable by anyone with viewer access on the engine; the graph does not use them (no public egress, executor runs on Cloud Run)" ;;
esac

echo "== public claims =="
# A number in the README that no longer matches the run that produced it is a
# metric-honesty failure, so this is a FAIL and not a warning. But a script
# that could not RUN is a third outcome: reporting that as a stale claim would
# repeat the mistake the wrangler check made, where an inability to answer
# read as a bad answer.
ledger_out="$(uv run python scripts/claim_ledger.py --check 2>&1)"; ledger_rc=$?
if ! printf '%s' "$ledger_out" | grep -q 'claims:'; then
  meh "claim ledger did not run, so no public number was checked (uv run python scripts/claim_ledger.py --check)"
elif [ "$ledger_rc" -eq 0 ]; then
  ok "public claims match their evidence ($(printf '%s' "$ledger_out" | tail -1))"
else
  bad "a public number no longer matches the run that produced it, or lost the evidence it cited: $(printf '%s' "$ledger_out" | grep -E 'MISMATCH|EVIDENCE GONE' | sed 's/^ *//' | tr '\n' ' ') — regenerate with --render after fixing the prose"
fi

echo
printf '%d passed, %d failed, %d warnings\n' "$pass" "$fail" "$warn"
[ "$fail" -eq 0 ]
