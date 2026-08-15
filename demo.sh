#!/usr/bin/env bash
# End-to-end demo. Run from the artifact root (the directory containing actiongate/).
#   ./actiongate/demo.sh
set -euo pipefail

PY="${PY:-./work/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
SECRET="whsec_demo_do_not_use_in_production"
STATE="$(mktemp -d)"
PORT="${PORT:-8787}"

echo "=== 1. The published sample verifier vs raw-byte verification ==="
node actiongate/probe_docs_verifier.js || echo "(node not found, skipping)"

echo
echo "=== 2. Start the receiver ==="
"$PY" -m actiongate.cli --state "$STATE" --secret "$SECRET" --threshold 0.65 \
      serve --port "$PORT" &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null && break
  sleep 0.1
done

PAYLOAD="$STATE/payload.json"
"$PY" - "$PAYLOAD" <<'EOF'
import json, sys, pathlib
blob = json.loads(pathlib.Path("actiongate/data/test.json").read_text())
pathlib.Path(sys.argv[1]).write_bytes(json.dumps(blob[5]["payload"]).encode())
EOF

echo
echo "=== 3. Forged signature must be refused ==="
code=$(curl -s -o "$STATE/forged.json" -w '%{http_code}' -X POST \
  -H "x-signature: $(printf '0%.0s' $(seq 1 64))" \
  --data-binary @"$PAYLOAD" "http://127.0.0.1:$PORT/webhook")
echo "HTTP $code  $(cat "$STATE/forged.json")"
[ "$code" = "401" ] || { echo "FAIL: expected 401"; exit 1; }

echo
echo "=== 4. Tampered body under a valid signature must be refused ==="
SIG=$("$PY" -m actiongate.cli --secret "$SECRET" sign "$PAYLOAD")
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "x-signature: $SIG" \
  --data-binary "$(cat "$PAYLOAD" | sed 's/Kestrel/Attacker/')" \
  "http://127.0.0.1:$PORT/webhook")
echo "HTTP $code (body edited after signing)"
[ "$code" = "401" ] || { echo "FAIL: expected 401"; exit 1; }

echo
echo "=== 5. Correctly signed request is processed and gated ==="
curl -s -X POST -H "x-signature: $SIG" --data-binary @"$PAYLOAD" \
  "http://127.0.0.1:$PORT/webhook"
echo

echo
echo "=== 6. Review queue (nothing here reached the CRM) ==="
"$PY" -m actiongate.cli --state "$STATE" queue

echo "=== 7. A human decides, and the log records who ==="
KEY=$("$PY" -m actiongate.cli --state "$STATE" queue | grep -o 'test_[a-z_]*:[0-9]*' | head -1)
"$PY" -m actiongate.cli --state "$STATE" --secret "$SECRET" \
      approve "$KEY" --reviewer laksh@example.com --note "checked the transcript span"

echo
echo "=== 8. Audit chain ==="
"$PY" -m actiongate.cli --state "$STATE" audit

echo
echo "=== 9. Tamper with the audit log and re-verify ==="
cp "$STATE/audit.jsonl" "$STATE/audit.bak"
"$PY" - "$STATE/audit.jsonl" <<'EOF'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]); lines = p.read_text().splitlines()
for i, line in enumerate(lines):
    e = json.loads(line)
    if e["record"]["event"] == "GATE_DECISION":
        e["record"]["decision"] = "AUTO_COMMIT"      # rewrite history
        e["record"]["confidence"] = 0.99
        lines[i] = json.dumps(e, sort_keys=True); break
p.write_text("\n".join(lines) + "\n")
print("  (edited one GATE_DECISION record in place)")
EOF
"$PY" -m actiongate.cli --state "$STATE" audit || true
mv "$STATE/audit.bak" "$STATE/audit.jsonl"

echo
echo "=== 10. Evaluation: precision/recall and the threshold choice ==="
"$PY" -m actiongate.cli eval

echo
echo "state dir was $STATE"
