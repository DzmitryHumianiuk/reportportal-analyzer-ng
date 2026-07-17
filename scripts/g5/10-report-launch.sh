#!/usr/bin/env bash
# T5.2 G5 scenario driver — talks to the real ReportPortal API over the gateway.
set -uo pipefail
BASE=http://localhost:8080
PROJECT=superadmin_personal
NOW=$(python3 -c 'import time;print(int(time.time()*1000))')

hr(){ echo; echo "### $* ###"; }
jq_or_raw(){ python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin),indent=2))' 2>/dev/null || cat; }

hr "STEP 0  login"
TOKEN=$(curl -s -X POST "$BASE/uat/sso/oauth/token" -H 'Authorization: Basic dWk6dWltYW4=' \
  -d 'grant_type=password&username=superadmin&password=erebus' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
echo "access_token acquired (len=${#TOKEN})"
AUTH="Authorization: Bearer $TOKEN"

hr "STEP 1  enable auto-analysis on project $PROJECT"
curl -s -X PUT "$BASE/api/v1/$PROJECT" -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "configuration": {
    "analyzerConfiguration": {
      "isAutoAnalyzerEnabled": "true",
      "analyzer_mode": "ALL",
      "minShouldMatch": "5",
      "numberOfLogLines": "-1",
      "indexingRunning": "true"
    }
  }
}' | jq_or_raw

hr "STEP 2  start launch"
LUUID=$(curl -s -X POST "$BASE/api/v2/$PROJECT/launch" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"name\":\"analyzer-ng-g5-demo\",\"startTime\":$NOW,\"mode\":\"DEFAULT\",
  \"attributes\":[{\"key\":\"scenario\",\"value\":\"g5\"}]}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "launch uuid=$LUUID"

hr "STEP 3  start a failing test item"
IUUID=$(curl -s -X POST "$BASE/api/v2/$PROJECT/item" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"name\":\"test_login_timeout\",\"startTime\":$((NOW+100)),\"type\":\"STEP\",\"launchUuid\":\"$LUUID\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "item uuid=$IUUID"

hr "STEP 4  attach an ERROR log with a stack trace"
curl -s -X POST "$BASE/api/v2/$PROJECT/log" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"itemUuid\":\"$IUUID\",\"launchUuid\":\"$LUUID\",\"time\":$((NOW+200)),\"level\":\"ERROR\",
  \"message\":\"java.net.SocketTimeoutException: Read timed out\n\tat java.base/java.net.SocketInputStream.socketRead0(Native Method)\n\tat com.example.LoginClient.authenticate(LoginClient.java:88)\n\tat com.example.tests.LoginTest.test_login_timeout(LoginTest.java:42)\"}" | jq_or_raw

hr "STEP 5  finish item as FAILED / To Investigate (ti001)"
curl -s -X PUT "$BASE/api/v2/$PROJECT/item/$IUUID" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"launchUuid\":\"$LUUID\",\"endTime\":$((NOW+300)),\"status\":\"FAILED\",\"issue\":{\"issueType\":\"ti001\",\"autoAnalyzed\":false}}" | jq_or_raw

hr "STEP 6  finish launch (auto-analysis triggers on finish when enabled)"
curl -s -X PUT "$BASE/api/v2/$PROJECT/launch/$LUUID/finish" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"endTime\":$((NOW+400))}" | jq_or_raw

echo "$LUUID" > .luuid; echo "$IUUID" > .iuuid; echo "$TOKEN" > .tok
echo "LUUID=$LUUID IUUID=$IUUID saved"
