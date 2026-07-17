#!/usr/bin/env bash
# Shared helpers for the analyzer-ng minikube "stage exerciser" (40-*) and the
# read-only DB auditor (inspect.sh). Sourced, never executed directly.
#
# Assumes the same port-forwards the 10/20/30 scripts rely on are already up:
#   svc/reportportal-uat   9999 : 9999   (auth / OAuth token)
#   svc/reportportal-api   8585 : 8585   (RP REST API -> publishes to analyzer)
# Optional (auto-started by ensure_pf if missing):
#   pod/reportportal-analyzer-0  15001 : 5001   (/health)
#   svc/reportportal-rabbitmq    15672 : 15672  (management API, direct publish)
#
# The analyzer pipeline is driven ENTIRELY through the RP API: reporting a launch
# makes RP publish `index`+`analyze`; "Make Decision" publishes `suggest`; changing
# an item's issue type publishes `defect_update` (the primary feedback signal).

set -uo pipefail

UAT_BASE=${UAT_BASE:-http://localhost:9999}
API_BASE=${API_BASE:-http://localhost:8585}
HEALTH_BASE=${HEALTH_BASE:-http://localhost:15001}
RABBIT_BASE=${RABBIT_BASE:-http://localhost:15672}
RABBIT_AUTH=${RABBIT_AUTH:-rabbitmq:rabbitmqpassword}
EXCHANGE=${EXCHANGE:-analyzer-default}
PROJECT=${PROJECT:-superadmin_personal}
PROJECT_ID=${PROJECT_ID:-1}
SUPERPW=${SUPERPW:-superadmin}
PG_DEPLOY=${PG_DEPLOY:-deploy/analyzer-pg}
LIB_HERE=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)

# ---- output helpers -------------------------------------------------------
c_hdr(){ printf '\n\033[1;36m========== %s ==========\033[0m\n' "$*"; }
c_sub(){ printf '\n\033[1;33m--- %s ---\033[0m\n' "$*"; }
c_ok(){  printf '\033[1;32mPASS\033[0m %s\n' "$*"; }
c_bad(){ printf '\033[1;31mFAIL\033[0m %s\n' "$*"; }
info(){  printf '  %s\n' "$*"; }
now_ms(){ python3 -c 'import time;print(int(time.time()*1000))'; }
jp(){ python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin),indent=2))' 2>/dev/null || cat; }

# ---- postgres (read + controlled write for proof queries) -----------------
# psql_t "SQL"   -> tuples-only, tab-separated (for scripting)
# psql_c "SQL"   -> pretty table (for human-readable audit output)
psql_t(){ kubectl exec "$PG_DEPLOY" -- psql -U analyzer -d analyzer -tAF $'\t' -c "$1" 2>/dev/null; }
psql_c(){ kubectl exec "$PG_DEPLOY" -- psql -U analyzer -d analyzer -c "$1" 2>&1; }
scalar(){ psql_t "$1" | head -1 | tr -d '[:space:]'; }

# ---- RP auth --------------------------------------------------------------
login(){
  TOKEN=$(curl -s -X POST "$UAT_BASE/uat/sso/oauth/token" -H 'Authorization: Basic dWk6dWltYW4=' \
    -d "grant_type=password&username=superadmin&password=$SUPERPW" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
  AUTH="Authorization: Bearer $TOKEN"
  [ -n "${TOKEN:-}" ] || { echo "login failed" >&2; return 1; }
}

# ---- RP reporting primitives ---------------------------------------------
# rp_launch NAME               -> echoes launch uuid
rp_launch(){
  local n=$(now_ms)
  curl -s -X POST "$API_BASE/api/v2/$PROJECT/launch" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$1\",\"startTime\":$n,\"mode\":\"DEFAULT\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])'
}
# rp_item LUUID NAME           -> echoes item uuid
rp_item(){
  local n=$(now_ms)
  curl -s -X POST "$API_BASE/api/v2/$PROJECT/item" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$2\",\"startTime\":$n,\"type\":\"STEP\",\"launchUuid\":\"$1\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])'
}
# rp_log LUUID IUUID MESSAGE   (MESSAGE may contain literal \n)
rp_log(){
  local n=$(now_ms)
  python3 - "$API_BASE" "$PROJECT" "$TOKEN" "$1" "$2" "$n" "$3" <<'PY' >/dev/null
import sys,json,urllib.request
api,proj,tok,luuid,iuuid,t,msg=sys.argv[1:8]
body=json.dumps({"itemUuid":iuuid,"launchUuid":luuid,"time":int(t),"level":"ERROR","message":msg.replace('\\n','\n')}).encode()
r=urllib.request.Request(f"{api}/api/v2/{proj}/log",data=body,method="POST",
  headers={"Authorization":f"Bearer {tok}","Content-Type":"application/json"})
try: urllib.request.urlopen(r,timeout=30).read()
except Exception as e: print("log err",e,file=sys.stderr)
PY
}
# rp_finish_item LUUID IUUID ISSUETYPE
rp_finish_item(){
  local n=$(now_ms)
  curl -s -X PUT "$API_BASE/api/v2/$PROJECT/item/$2" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"launchUuid\":\"$1\",\"endTime\":$n,\"status\":\"FAILED\",\"issue\":{\"issueType\":\"${3:-ti001}\",\"autoAnalyzed\":false}}" >/dev/null
}
# rp_finish_launch LUUID
rp_finish_launch(){
  local n=$(now_ms)
  curl -s -X PUT "$API_BASE/api/v2/$PROJECT/launch/$1/finish" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"endTime\":$n}" >/dev/null
}
# rp_numeric_item IUUID        -> analyzer/RP numeric item id
rp_numeric_item(){
  curl -s "$API_BASE/api/v1/$PROJECT/item/uuid/$1" -H "$AUTH" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])' 2>/dev/null
}
# rp_numeric_launch LUUID
rp_numeric_launch(){
  curl -s "$API_BASE/api/v1/$PROJECT/launch/uuid/$1" -H "$AUTH" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])' 2>/dev/null
}
# rp_analyze LID  (force auto-analysis of TO_INVESTIGATE items)
rp_analyze(){
  curl -s -X POST "$API_BASE/api/v1/$PROJECT/launch/analyze" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"launchId\":$1,\"analyzerMode\":\"ALL\",\"analyzerTypeName\":\"autoAnalyzer\",\"analyzeItemsMode\":[\"TO_INVESTIGATE\"]}" >/dev/null
}
# rp_suggest NID  -> raw JSON of suggested items ("Make Decision")
rp_suggest(){ curl -s "$API_BASE/api/v1/$PROJECT/item/suggest/$1" -H "$AUTH"; }
# rp_defect_update NID ISSUETYPE  -> feedback signal (publishes defect_update)
rp_defect_update(){
  curl -s -X PUT "$API_BASE/api/v1/$PROJECT/item" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"issues\":[{\"testItemId\":$1,\"issue\":{\"issueType\":\"$2\",\"autoAnalyzed\":false,\"ignoreAnalyzer\":false,\"comment\":\"exerciser\"}}]}" >/dev/null
}

# enable auto-analysis + indexing on the project
rp_enable_analysis(){
  curl -s -X PUT "$API_BASE/api/v1/project/$PROJECT" -H "$AUTH" -H 'Content-Type: application/json' -d '{
   "configuration":{"attributes":{
     "analyzer.isAutoAnalyzerEnabled":"true",
     "analyzer.autoAnalyzerMode":"ALL",
     "analyzer.minShouldMatch":"5",
     "analyzer.numberOfLogLines":"-1",
     "analyzer.indexingRunning":"true"}}}' >/dev/null
}

# ---- direct AMQP publish (train_models fallback) --------------------------
amqp_publish(){ # amqp_publish JSON_PAYLOAD
  python3 - "$RABBIT_BASE" "$RABBIT_AUTH" "$EXCHANGE" "$1" <<'PY'
import sys,json,base64,urllib.request
base,auth,exch,payload=sys.argv[1:5]
u,p=auth.split(":",1)
body=json.dumps({"properties":{"content_type":"application/json"},"routing_key":"train_models",
  "payload":payload,"payload_encoding":"string"}).encode()
r=urllib.request.Request(f"{base}/api/exchanges/analyzer/{exch}/publish",data=body,method="POST",
  headers={"Authorization":"Basic "+base64.b64encode(auth.encode()).decode(),"Content-Type":"application/json"})
print(urllib.request.urlopen(r,timeout=15).read().decode())
PY
}

# ---- health ---------------------------------------------------------------
health(){ curl -s "$HEALTH_BASE/health"; }
health_field(){ health | python3 -c "import sys,json;print(json.load(sys.stdin).get('$1'))" 2>/dev/null; }

# ---- polling (bounded waits, never blind sleeps) --------------------------
# poll_ge TIMEOUT_S MIN "SQL(scalar count)" [LABEL]  -> returns 0 if value>=MIN
poll_ge(){
  local timeout=$1 min=$2 sql=$3 label=${4:-value} start=$(date +%s) v=0
  while :; do
    v=$(scalar "$sql"); v=${v:-0}
    [[ "$v" =~ ^-?[0-9]+$ ]] || v=0
    if [ "$v" -ge "$min" ]; then info "$label reached $v (>= $min) after $(( $(date +%s)-start ))s"; return 0; fi
    if [ $(( $(date +%s)-start )) -ge "$timeout" ]; then info "$label stuck at $v (< $min) after ${timeout}s timeout"; return 1; fi
    sleep 3
  done
}
