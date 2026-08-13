#!/usr/bin/env bash
# Verify the yente screening stack. Run FROM THE VM.
set -uo pipefail

python3 "$(dirname "$0")/check.py"
rc=$?

echo
echo "=== 6. egress: no OpenSanctions host contacted since startup ==="
if sudo docker logs yente-app 2>&1 \
   | grep -Eiq 'data\.opensanctions\.org|delivery\.opensanctions\.com'; then
  echo "FAIL  app logs reference an OpenSanctions host"
  rc=1
else
  echo "PASS  no OpenSanctions fetch in app logs"
fi

exit "$rc"
