#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/deploy/local/.kube/kubeconfig.yaml"
docker build -q -t antara-app:dev . >/dev/null
for node in antara-k3s-k3s-1 antara-k3s-worker-1; do
  if docker inspect -f '{{.State.Running}}' "$node" 2>/dev/null | grep -q true; then
    docker save antara-app:dev | docker exec -i "$node" ctr -n k8s.io images import - >/dev/null
  fi
done
kubectl kustomize --load-restrictor LoadRestrictionsNone deploy/local/overlay | kubectl apply -f - >/dev/null
for name in api gateway agent tools ingest-cpu ingest-gpu reconciler; do
  kubectl -n antara rollout restart "deploy/$name" >/dev/null
done
for name in api gateway agent tools ingest-cpu ingest-gpu reconciler; do
  kubectl -n antara rollout status "deploy/$name" --timeout=180s >/dev/null
done
echo "Antara redeployed on local k3s"
