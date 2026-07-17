#!/usr/bin/env bash
set -uo pipefail
BASE=http://localhost:8080; PROJECT=superadmin_personal
TOKEN=$(cat .tok); AUTH="Authorization: Bearer $TOKEN"
LUUID=$(cat .luuid); IUUID=$(cat .iuuid)
jqp(){ python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin),indent=2))' 2>/dev/null || cat; }
hr(){ echo; echo "### $* ###"; }

LID=$(curl -s "$BASE/api/v1/$PROJECT/launch/uuid/$LUUID" -H "$AUTH" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
IID=$(curl -s "$BASE/api/v1/$PROJECT/item/uuid/$IUUID" -H "$AUTH" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "numeric launchId=$LID itemId=$IID"; echo "$IID" > .iid; echo "$LID" > .lid

hr "STEP 7  enable auto-analysis (project attributes)"
curl -s -X PUT "$BASE/api/v1/project/$PROJECT" -H "$AUTH" -H 'Content-Type: application/json' -d '{
 "configuration":{"attributes":{
   "analyzer.isAutoAnalyzerEnabled":"true",
   "analyzer.autoAnalyzerMode":"ALL",
   "analyzer.minShouldMatch":"5",
   "analyzer.numberOfLogLines":"-1",
   "analyzer.indexingRunning":"true"}}}' | jqp
echo "-- read back --"
curl -s "$BASE/api/v1/project/$PROJECT" -H "$AUTH" | python3 -c 'import sys,json;a=json.load(sys.stdin)["configuration"]["attributes"];print({k:v for k,v in a.items() if k.startswith("analyzer.is") or k=="analyzer.autoAnalyzerMode"})'

hr "STEP 8  trigger auto-analysis on launch $LID"
curl -s -X PUT "$BASE/api/v1/$PROJECT/launch/analyze" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"launchId\":$LID,\"analyzerMode\":\"ALL\",\"analyzerTypeName\":\"autoAnalyzer\",
  \"analyzeItemsMode\":[\"TO_INVESTIGATE\"]}" | jqp
