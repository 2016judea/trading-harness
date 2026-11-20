#!/bin/bash
# Give the live connector its SEC contact, then redeploy production.
#
#   scripts/set_sec_contact.sh "Trading Harness you@yourdomain.com"
#
# SEC blocks (403) any client whose User-Agent doesn't name a reachable contact, so
# the three valuation tools can't read filings until this is set. The value lives
# only in Vercel's encrypted env (SEC_USER_AGENT); it is never committed. The
# operator runs this with their own contact; nobody else should choose it.
set -euo pipefail
UA="${1:?usage: $0 \"Name contact@domain\"}"
case "$UA" in *@*.*) ;; *) echo "SEC wants an email-shaped contact in the string" >&2; exit 1;; esac
set -a; . "$HOME/Desktop/workspace/bricks/.env"; set +a
T=team_XnhPzBEcaSfEmZFo9M2isLFY
P=prj_thkiPEuBcGLN45WWSA7UGRDlTR0V
API=https://api.vercel.com
auth=(-H "Authorization: Bearer $VERCEL_TOKEN" -H 'content-type: application/json')

body=$(python3 -c 'import json,sys; print(json.dumps({"key":"SEC_USER_AGENT","value":sys.argv[1],"type":"encrypted","target":["production","preview","development"]}))' "$UA")
curl -sf -X POST "$API/v10/projects/$P/env?teamId=$T&upsert=true" "${auth[@]}" -d "$body" >/dev/null
echo "SEC_USER_AGENT set on trading-harness-mcp"

dep=$(curl -sf -X POST "$API/v13/deployments?teamId=$T" "${auth[@]}" \
  -d '{"name":"trading-harness-mcp","project":"'$P'","target":"production","gitSource":{"type":"github","repoId":1337455748,"ref":"main"}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
printf "redeploying %s" "$dep"
for _ in $(seq 1 40); do
  s=$(curl -sf "$API/v13/deployments/$dep?teamId=$T" "${auth[@]}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["readyState"])')
  [ "$s" = READY ] && break
  [ "$s" = ERROR ] && { echo " ERROR"; exit 1; }
  printf "."; sleep 5
done
echo " $s"
cd "$(dirname "$0")/.." && python3 scripts/live_cases.py
