#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/../deploy/local"
export KUBECONFIG="$PWD/.kube/kubeconfig.yaml"
case "${1:-up}" in
  up)
    docker run --rm --privileged busybox:1.36.1@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662 \
      sysctl -w fs.inotify.max_user_instances=1024 fs.inotify.max_user_watches=524288 >/dev/null
    docker compose up -d k3s
    until docker exec antara-k3s-k3s-1 test -s /var/lib/rancher/k3s/server/token 2>/dev/null; do sleep 2; done
    printf 'K3S_TOKEN=%s\n' "$(docker exec antara-k3s-k3s-1 cat /var/lib/rancher/k3s/server/token)" > .env
    docker compose up -d worker
    until [ "$(kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready')" -ge 2 ]; do sleep 3; done
    kubectl get nodes
    ;;
  stop-worker)
    docker stop antara-k3s-worker-1
    ;;
  start-worker)
    docker start antara-k3s-worker-1
    until kubectl get node antara-worker --no-headers 2>/dev/null | grep -q ' Ready'; do sleep 3; done
    for name in api agent tools ingest-cpu ingest-gpu reconciler gateway temporal temporal-ui otel-collector prometheus grafana; do
      kubectl -n antara rollout restart "deploy/$name" >/dev/null
    done
    echo "Worker node back; request path rebalancing across both nodes"
    ;;
  *)
    echo "usage: $0 [up|stop-worker|start-worker]" >&2
    exit 1
    ;;
esac
