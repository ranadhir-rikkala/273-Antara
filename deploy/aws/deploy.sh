#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
source "$STATE_FILE"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH=(ssh -i "$KEY_NAME.pem" -o StrictHostKeyChecking=accept-new)

ip() { aws ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text; }
server_ip=$(ip "$SERVER_ID")
gpu_ip=$(ip "$GPU_ID")

docker build --platform linux/amd64 -t antara-app:dev "$ROOT"
for host in "$server_ip" "$gpu_ip"; do
  docker save antara-app:dev | "${SSH[@]}" "ubuntu@$host" sudo k3s ctr images import -
done

rsync -az -e "${SSH[*]}" "$ROOT/deploy/" "ubuntu@$server_ip:deploy/"
"${SSH[@]}" "ubuntu@$server_ip" \
  "kubectl kustomize --load-restrictor LoadRestrictionsNone deploy/k3s | kubectl apply -f - && kubectl -n antara get pods"
