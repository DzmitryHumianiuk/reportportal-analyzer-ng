#!/usr/bin/env bash
# Side-load every container image the minikube deploy needs.
#
# This minikube VM cannot reach registry-1.docker.io at run time (its upstream
# DNS refuses), so images are pulled on the HOST (which has working docker +
# internet) and copied into the VM with `minikube image load`. The values file
# forces pullPolicy=IfNotPresent so kubelet uses these instead of pulling.
#
# The analyzer-ng image itself is NOT listed here — build + load it separately:
#   docker build -t analyzer-ng:latest . && minikube image load analyzer-ng:latest
set -u
IMAGES=(
  pgvector/pgvector:pg16
  bitnamisecure/kubectl:latest
  curlimages/curl:latest
  docker.io/bitnamilegacy/postgresql:17.5.0-debian-12-r20
  docker.io/bitnamilegacy/rabbitmq:4.1.2-debian-12-r1
  reportportal/k8s-wait-for:latest
  reportportal/migrations:5.15.2
  reportportal/service-api:5.15.2
  reportportal/service-authorization:5.15.0
  reportportal/service-index:5.15.0
  reportportal/service-jobs:5.15.1
  reportportal/service-ui:5.15.3
)
fail=0
for img in "${IMAGES[@]}"; do
  echo "=== $img ==="
  docker pull "$img" >/dev/null 2>&1 || { echo "PULL FAILED $img"; fail=1; continue; }
  minikube image load "$img"     || { echo "LOAD FAILED $img"; fail=1; continue; }
  echo "ok $img"
done
echo "=== DONE (fail=$fail) ==="
exit $fail
