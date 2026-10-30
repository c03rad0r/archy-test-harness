#!/usr/bin/env bash
# Create or remove the temporary device token used by the Playwright lane.
#
# The token is a bearer credential for the node's RPC API. It is created on the
# node only for the duration of a test run and removed on exit. The plaintext
# token is printed once by `create`; it is never written to a file by this
# script.
set -euo pipefail

ACTION=${1:-create}
NODE=${PLAYWRIGHT_NODE:-archipelago@192.168.50.199}
NAME=${PLAYWRIGHT_TOKEN_NAME:-playwright-regression}

case "$ACTION" in
    create)
        TOKEN=$(openssl rand -hex 32)
        HASH=$(printf '%s' "$TOKEN" | sha256sum | awk '{print $1}')
        ssh -q -o BatchMode=yes -o ConnectTimeout=10 "$NODE" \
            "python3 - '$HASH' '$NAME' <<'PY'
import json, os, sys, time
path = '/var/lib/archipelago/device-tokens.json'
digest, name = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = json.load(f)
data = [d for d in data if d.get('name') != name]
data.append({'name': name, 'hash': digest, 'created': int(time.time())})
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
os.chmod(path, 0o600)
PY"
        printf '%s' "$TOKEN"
        ;;
    delete)
        ssh -q -o BatchMode=yes -o ConnectTimeout=10 "$NODE" \
            "python3 - '$NAME' <<'PY'
import json, sys
path = '/var/lib/archipelago/device-tokens.json'
name = sys.argv[1]
with open(path) as f:
    data = json.load(f)
data = [d for d in data if d.get('name') != name]
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
PY"
        ;;
    *)
        echo "usage: $0 [create|delete]" >&2
        exit 2
        ;;
esac
