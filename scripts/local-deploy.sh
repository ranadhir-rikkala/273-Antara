#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/deploy/local/.kube/kubeconfig.yaml"
docker build -q -t antara-app:dev . >/dev/null
docker save antara-app:dev | docker exec -i antara-k3s-k3s-1 ctr -n k8s.io images import - >/dev/null
kubectl kustomize --load-restrictor LoadRestrictionsNone deploy/local/overlay | kubectl apply -f - >/dev/null
for name in api gateway agent tools ingest-cpu ingest-gpu reconciler; do
  kubectl -n antara rollout restart "deploy/$name" >/dev/null
done
for name in api gateway agent tools ingest-cpu ingest-gpu reconciler; do
  kubectl -n antara rollout status "deploy/$name" --timeout=180s >/dev/null
done
echo "Antara redeployed on local k3s"
