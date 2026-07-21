#!/usr/bin/env bash
# test-api.sh — confirm the bootstrapped 'agent' user can authenticate against
# DokuWiki's JSON-RPC API (lib/exe/jsonrpc.php).
#
# Usage:
#   AGENT_PASS='...' ./test-api.sh https://<app>.fly.dev [agent_username]
#
# AGENT_PASS must equal the DOKU_AGENT_PASSWORD Fly secret value.
#
# It calls core.whoAmI, which returns the authenticated user (and errors out
# when unauthenticated) — so a successful run proves the agent's credentials
# work AND that the account is in the 'api' group that the API is restricted to.
set -euo pipefail

URL="${1:?usage: AGENT_PASS=<secret> $0 <base-url> [agent-user]}"
USER="${2:-agent}"
PASS="${AGENT_PASS:?set AGENT_PASS to the DOKU_AGENT_PASSWORD value}"

endpoint="${URL%/}/lib/exe/jsonrpc.php"
payload='{"jsonrpc":"2.0","method":"core.whoAmI","id":1}'
tmp="$(mktemp)"

echo "==> POST $endpoint  (authenticated as '$USER')"
code=$(curl -sS -o "$tmp" -w '%{http_code}' \
  -u "${USER}:${PASS}" \
  -H 'Content-Type: application/json' \
  -d "$payload" "$endpoint")
echo "HTTP $code"
cat "$tmp"; echo

ok=0
if [ "$code" = "200" ] && grep -q "\"login\":\"${USER}\"" "$tmp" && grep -q '"api"' "$tmp"; then
  ok=1
fi

echo
echo "==> same call with NO credentials (expect denial)"
curl -sS -o "$tmp" -w 'HTTP %{http_code}\n' \
  -H 'Content-Type: application/json' \
  -d "$payload" "$endpoint"
cat "$tmp"; echo

if [ "$ok" = "1" ]; then
  echo "PASS: '$USER' authenticated and is in the 'api' group."
  exit 0
fi
echo "FAIL: response did not confirm '$USER' in the 'api' group."
exit 1
