#!/usr/bin/env bash
# G5 step 2, adapted for minikube (see 10-report-launch.sh header).
set -uo pipefail
UAT_BASE=${UAT_BASE:-http://localhost:9999}
API_BASE=${API_BASE:-http://localhost:8585}
PROJECT=${PROJECT:-superadmin_personal}
HERE=$(cd "$(dirname "$0")" && pwd)
TOKEN=$(cat "$HERE/.tok"); AUTH="Authorization: Bearer $TOKEN"
LUUID=$(cat "$HERE/.luuid"); IUUID=$(cat "$HERE/.iuuid")
jqp(){ python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin),indent=2))' 2>/dev/null || cat; }
hr(){ echo; echo "### $* ###"; }

LID=$(curl -s "$API_BASE/api/v1/$PROJECT/launch/uuid/$LUUID" -H "$AUTH" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
IID=$(curl -s "$API_BASE/api/v1/$PROJECT/item/uuid/$IUUID" -H "$AUTH" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "numeric launchId=$LID itemId=$IID"; echo "$IID" > "$HERE/.iid"; echo "$LID" > "$HERE/.lid"

hr "STEP 7  enable auto-analysis (project attributes)"
curl -s -X PUT "$API_BASE/api/v1/project/$PROJECT" -H "$AUTH" -H 'Content-Type: application/json' -d '{
 "configuration":{"attributes":{
   "analyzer.isAutoAnalyzerEnabled":"true",
   "analyzer.autoAnalyzerMode":"ALL",
   "analyzer.minShouldMatch":"5",
   "analyzer.numberOfLogLines":"-1",
   "analyzer.indexingRunning":"true"}}}' | jqp
echo "-- read back --"
curl -s "$API_BASE/api/v1/project/$PROJECT" -H "$AUTH" | python3 -c 'import sys,json;a=json.load(sys.stdin)["configuration"]["attributes"];print({k:v for k,v in a.items() if k.startswith("analyzer.is") or k=="analyzer.autoAnalyzerMode"})'

hr "STEP 8  trigger auto-analysis on launch $LID"
curl -s -X POST "$API_BASE/api/v1/$PROJECT/launch/analyze" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"launchId\":$LID,\"analyzerMode\":\"ALL\",\"analyzerTypeName\":\"autoAnalyzer\",
  \"analyzeItemsMode\":[\"TO_INVESTIGATE\"]}" | jqp

hr "STEP 9  wait, then read back item $IID (autoAnalyzed + issueType)"
sleep 10
curl -s "$API_BASE/api/v1/$PROJECT/item/$IID" -H "$AUTH" | python3 -c 'import sys,json;d=json.load(sys.stdin);i=d.get("issue",{});print("autoAnalyzed=",i.get("autoAnalyzed"),"issueType=",i.get("issueType"))'
