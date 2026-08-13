#!/usr/bin/env bash
# Deploy / redeploy the yente screening stack onto the keplaria-yente VM.
#
# Run FROM THE VM (the repo dir is pushed there by push.sh), not from a laptop.
set -euo pipefail

STACK_DIR=/opt/yente

# Elasticsearch refuses to start below this; Ubuntu's default is 65530.
if [ "$(sysctl -n vm.max_map_count)" -lt 262144 ]; then
  echo "raising vm.max_map_count"
  sudo sysctl -w vm.max_map_count=262144
fi
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-elasticsearch.conf >/dev/null

sudo mkdir -p "$STACK_DIR/data"
sudo cp "$(dirname "$0")/docker-compose.yml" "$STACK_DIR/docker-compose.yml"
sudo cp "$(dirname "$0")/manifest.yml" "$STACK_DIR/manifest.yml"
sudo cp "$(dirname "$0")/entities.ftm.json" "$STACK_DIR/data/entities.ftm.json"

# Local admin token guarding the manual reindex endpoint. Generated once and
# kept root-only; the service is VPC-internal and never publicly reachable.
if [ ! -f "$STACK_DIR/.env" ]; then
  printf 'YENTE_UPDATE_TOKEN=%s\n' "$(openssl rand -hex 24)" \
    | sudo tee "$STACK_DIR/.env" >/dev/null
  sudo chmod 600 "$STACK_DIR/.env"
  echo "generated $STACK_DIR/.env"
fi

cd "$STACK_DIR"
sudo docker compose pull -q
sudo docker compose up -d
echo "--- waiting for elasticsearch ---"
for _ in $(seq 1 60); do
  if sudo docker compose ps index --format '{{.Health}}' | grep -q healthy; then
    echo "index healthy"; break
  fi
  sleep 5
done
sudo docker compose ps
