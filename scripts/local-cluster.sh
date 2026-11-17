#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/../deploy/local"
export KUBECONFIG="$PWD/.kube/kubeconfig.yaml"
WORKER_OFF="$PWD/.worker-off"
DEPLOYMENTS="api gateway grafana otel-collector prometheus reconciler temporal temporal-ui"
SCALED="agent ingest-cpu ingest-gpu tools"
STANDBYS="app-db-standby temporal-db-standby object-store-replica"
PAIRS="jaeger ollama"
prepare_node() {
  docker exec "$1" mount --make-rshared /
  docker exec "$1" sh -c '[ -f /etc/machine-id ] || { rmdir /etc/machine-id 2>/dev/null; cat /proc/sys/kernel/random/uuid | tr -d - > /etc/machine-id; }'
}
wait_ready_nodes() {
  until [ "$(kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready')" -ge "$1" ]; do sleep 3; done
}
single_node() {
  kubectl cordon antara-worker >/dev/null 2>&1 || true
  for name in $SCALED; do
    kubectl -n antara patch scaledobject "$name" --type merge -p '{"spec":{"minReplicaCount":1}}' >/dev/null
  done
  kubectl -n antara patch scaledobject ingest-gpu --type merge -p '{"spec":{"maxReplicaCount":1}}' >/dev/null
  for name in $DEPLOYMENTS $SCALED; do
    kubectl -n antara scale "deploy/$name" --replicas=1 >/dev/null
  done
  for name in $STANDBYS; do
    kubectl -n antara scale "statefulset/$name" --replicas=0 >/dev/null
  done
  for name in $PAIRS; do
    kubectl -n antara patch statefulset "$name" --type merge -p '{"spec":{"replicas":1,"ordinals":{"start":1}}}' >/dev/null
  done
}
both_nodes() {
  for name in $PAIRS; do
    kubectl -n antara patch statefulset "$name" --type merge -p '{"spec":{"replicas":2,"ordinals":{"start":0}}}' >/dev/null
  done
  for name in $STANDBYS; do
    kubectl -n antara scale "statefulset/$name" --replicas=1 >/dev/null
  done
  for name in $SCALED; do
    kubectl -n antara patch scaledobject "$name" --type merge -p '{"spec":{"minReplicaCount":2}}' >/dev/null
  done
  kubectl -n antara patch scaledobject ingest-gpu --type merge -p '{"spec":{"maxReplicaCount":2}}' >/dev/null
  for name in $DEPLOYMENTS $SCALED; do
    kubectl -n antara scale "deploy/$name" --replicas=2 >/dev/null
  done
}
start_worker() {
  docker start antara-k3s-worker-1 >/dev/null
  prepare_node antara-k3s-worker-1
  until kubectl get node antara-worker --no-headers 2>/dev/null | grep -q ' Ready'; do sleep 3; done
  kubectl uncordon antara-worker >/dev/null
}
case "${1:-up}" in
  up)
    docker run --rm --privileged busybox:1.36.1@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662 \
      sysctl -w fs.inotify.max_user_instances=1024 fs.inotify.max_user_watches=524288 >/dev/null
    docker compose up -d k3s
    until docker exec antara-k3s-k3s-1 test -s /var/lib/rancher/k3s/server/token 2>/dev/null; do sleep 2; done
    printf 'K3S_TOKEN=%s\n' "$(docker exec antara-k3s-k3s-1 cat /var/lib/rancher/k3s/server/token)" > .env
    prepare_node antara-k3s-k3s-1
    if [ -f "$WORKER_OFF" ]; then
      wait_ready_nodes 1
      single_node
      echo "Node 2 is switched off; run '$0 worker-on' to bring it back"
    else
      docker compose up -d worker
      prepare_node antara-k3s-worker-1
      wait_ready_nodes 2
    fi
    kubectl get nodes
    ;;
  worker-off)
    touch "$WORKER_OFF"
    single_node
    if docker inspect -f '{{.State.Running}}' antara-k3s-worker-1 2>/dev/null | grep -q true; then
      kubectl drain antara-worker --ignore-daemonsets --delete-emptydir-data --timeout=180s >/dev/null
      docker stop antara-k3s-worker-1 >/dev/null
    fi
    echo "Node 2 off: one pod per service on node 1"
    ;;
  worker-on)
    rm -f "$WORKER_OFF"
    start_worker
    both_nodes
    echo "Node 2 on: two pods per service, one on each node"
    ;;
  single-node)
    single_node
    ;;
  stop-worker)
    docker stop antara-k3s-worker-1
    ;;
  start-worker)
    start_worker
    for name in api agent tools ingest-cpu ingest-gpu reconciler gateway temporal temporal-ui otel-collector prometheus grafana; do
      kubectl -n antara rollout restart "deploy/$name" >/dev/null
    done
    echo "Worker node back; request path rebalancing across both nodes"
    ;;
  *)
    echo "usage: $0 [up|worker-off|worker-on|stop-worker|start-worker]" >&2
    exit 1
    ;;
esac
