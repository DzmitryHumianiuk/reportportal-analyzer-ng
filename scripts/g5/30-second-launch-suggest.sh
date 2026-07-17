#!/usr/bin/env bash
set -uo pipefail
BASE=http://localhost:8080; PROJECT=superadmin_personal
TOKEN=$(cat .tok); AUTH="Authorization: Bearer $TOKEN"
jqp(){ python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin),indent=2))' 2>/dev/null || cat; }
hr(){ echo; echo "### $* ###"; }
N=$(python3 -c 'import time;print(int(time.time()*1000))')

hr "STEP 10  report a SECOND launch with the SAME failure signature"
L2=$(curl -s -X POST "$BASE/api/v2/$PROJECT/launch" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"name\":\"analyzer-ng-g5-demo\",\"startTime\":$N,\"mode\":\"DEFAULT\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
I2=$(curl -s -X POST "$BASE/api/v2/$PROJECT/item" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"name\":\"test_login_timeout\",\"startTime\":$((N+100)),\"type\":\"STEP\",\"launchUuid\":\"$L2\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -s -X POST "$BASE/api/v2/$PROJECT/log" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"itemUuid\":\"$I2\",\"launchUuid\":\"$L2\",\"time\":$((N+200)),\"level\":\"ERROR\",
  \"message\":\"java.net.SocketTimeoutException: Read timed out\n\tat java.base/java.net.SocketInputStream.socketRead0(Native Method)\n\tat com.example.LoginClient.authenticate(LoginClient.java:88)\n\tat com.example.tests.LoginTest.test_login_timeout(LoginTest.java:42)\"}" >/dev/null
curl -s -X PUT "$BASE/api/v2/$PROJECT/item/$I2" -H "$AUTH" -H 'Content-Type: application/json' -d "{
  \"launchUuid\":\"$L2\",\"endTime\":$((N+300)),\"status\":\"FAILED\",\"issue\":{\"issueType\":\"ti001\"}}" >/dev/null
curl -s -X PUT "$BASE/api/v2/$PROJECT/launch/$L2/finish" -H "$AUTH" -H 'Content-Type: application/json' -d "{\"endTime\":$((N+400))}" >/dev/null
echo "launch2 uuid=$L2 item2 uuid=$I2"
sleep 8
NID=$(curl -s "$BASE/api/v1/$PROJECT/item/uuid/$I2" -H "$AUTH" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "item2 numeric id=$NID"

hr "STEP 11  Make Decision -> suggested items for the new item $NID (should now match labeled item 2 = pb001)"
curl -s "$BASE/api/v1/$PROJECT/item/suggest/$NID" -H "$AUTH" | jqp
echo "$NID" > .nid
