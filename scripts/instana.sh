#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/deploy/local/.kube/kubeconfig.yaml"
CHART_VERSION="2.0.55"
HELM_IMAGE="alpine/helm:3.16.2@sha256:a19a2968fd672336d39771f6c899781424d725229148656dbc2a1e305003cdec"
SETTINGS="deploy/instana/agent.env"

enabled() {
  [ "$(kubectl get namespace instana-agent -o jsonpath='{.status.phase}' 2>/dev/null)" = "Active" ]
}

helm() {
  docker run --rm --network host -v "$KUBECONFIG:/root/.kube/config:ro" -v "$PWD/deploy/instana:/instana:ro" "$HELM_IMAGE" "$@"
}

case "${1:-status}" in
  enable)
    [ -f "$SETTINGS" ] && . "./$SETTINGS"
    : "${INSTANA_AGENT_KEY:?set INSTANA_AGENT_KEY in $SETTINGS (from Instana: More > Agents > Installing Instana Agents)}"
    : "${INSTANA_ENDPOINT_HOST:?set INSTANA_ENDPOINT_HOST in $SETTINGS, for example ingress-red-saas.instana.io}"
    helm upgrade --install instana-agent instana-agent --repo https://agents.instana.io/helm --version "$CHART_VERSION" \
      --namespace instana-agent --create-namespace -f /instana/values.yaml \
      --set agent.key="$INSTANA_AGENT_KEY" --set agent.endpointHost="$INSTANA_ENDPOINT_HOST" \
      --wait --timeout 10m >/dev/null
    kubectl apply -f deploy/instana/collector.yaml >/dev/null
    kubectl -n antara rollout restart deploy/otel-collector >/dev/null
    kubectl -n antara rollout status deploy/otel-collector --timeout=180s >/dev/null
    echo "Instana enabled: traces now go to both Jaegers and to Instana at $INSTANA_ENDPOINT_HOST"
    echo "Monitoring data leaves the network while Instana is enabled. Turn it off with: scripts/instana.sh disable"
    ;;
  disable)
    kubectl -n antara delete networkpolicy allow-egress-to-instana-agent --ignore-not-found >/dev/null
    kubectl kustomize --load-restrictor LoadRestrictionsNone deploy/local/overlay | kubectl apply -f - >/dev/null
    kubectl -n antara rollout restart deploy/otel-collector >/dev/null
    kubectl -n antara rollout status deploy/otel-collector --timeout=180s >/dev/null
    kubectl -n instana-agent delete instanaagent instana-agent --ignore-not-found --timeout=120s >/dev/null 2>&1 ||
      kubectl -n instana-agent patch instanaagent instana-agent --type=merge -p '{"metadata":{"finalizers":[]}}' >/dev/null 2>&1 || true
    helm uninstall instana-agent --namespace instana-agent --wait >/dev/null 2>&1 || true
    kubectl delete namespace instana-agent --ignore-not-found --timeout=120s >/dev/null
    echo "Instana disabled: traces go only to the two in-cluster Jaegers; nothing leaves the network"
    ;;
  status)
    if enabled; then
      echo "Instana: enabled"
      kubectl -n instana-agent get pods -o wide
    else
      echo "Instana: disabled (nothing leaves the network)"
    fi
    ;;
  *)
    echo "usage: $0 [enable|disable|status]" >&2
    exit 1
    ;;
esac
