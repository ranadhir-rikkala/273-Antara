#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/deploy/local/.kube/kubeconfig.yaml"
API="http://localhost:8000"
KEDA="https://github.com/kedacore/keda/releases/download/v2.17.2/keda-2.17.2.yaml"
MODELS="qwen2.5:1.5b nomic-embed-text"
OPERATOR="-H X-User-Id:setup -H X-User-Groups:operators"

step() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nsetup failed: %s\n' "$1" >&2; exit 1; }
has_models() { kubectl -n antara exec "$1" -- ollama list 2>/dev/null | grep -q "qwen2.5:1.5b"; }

step "Checking tools"
for tool in docker kubectl curl; do
  command -v "$tool" >/dev/null || fail "$tool is not installed"
done
docker info >/dev/null 2>&1 || fail "Docker is not running. On a Mac with Colima: colima start --cpu 8 --memory 16"
memory=$(docker info --format '{{.MemTotal}}')
if [ "$memory" -lt 15000000000 ]; then
  echo "warning: Docker has $((memory / 1073741824)) GB of memory; 16 GB is recommended for 34 pods"
fi

step "Starting the two-node cluster"
scripts/local-cluster.sh up

step "Installing KEDA"
kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1 || kubectl apply --server-side -f "$KEDA" >/dev/null
kubectl -n keda rollout status deploy/keda-operator --timeout=300s >/dev/null
echo "KEDA ready"

step "Building and deploying Antara (the first run waits for the models; a timeout here is expected)"
scripts/local-deploy.sh || echo "continuing to the model download"

step "Downloading the models into both Ollama copies"
for pod in ollama-0 ollama-1; do
  kubectl -n antara wait --for=jsonpath='{.status.phase}'=Running "pod/$pod" --timeout=600s >/dev/null
done
if has_models ollama-0 && has_models ollama-1; then
  echo "models already present"
else
  kubectl -n antara delete networkpolicy default-deny allow-egress-inside-cluster --ignore-not-found >/dev/null
  for pod in ollama-0 ollama-1; do
    for model in $MODELS; do
      kubectl -n antara exec "$pod" -- ollama pull "$model"
    done
  done
fi

step "Blocking internet access again and rolling out"
scripts/local-deploy.sh

step "Waiting for every pod"
for _ in $(seq 1 180); do
  pending=$(kubectl -n antara get pods --no-headers | grep -vc " Running ") || true
  [ "$pending" = 0 ] && break
  sleep 5
done
[ "$pending" = 0 ] || fail "some pods are not running: kubectl -n antara get pods"
until curl -fs -o /dev/null "$API/"; do sleep 3; done
echo "$(kubectl -n antara get pods --no-headers | wc -l | tr -d ' ') pods running"

step "Checking the model fingerprints"
if curl -fs $OPERATOR "$API/ops/state" | grep -Eq '"unverified": ?\[\]'; then
  echo "models verified"
else
  echo "warning: a model does not match its pinned digest; update PINNED_MODEL_DIGESTS in deploy/local/overlay/kustomization.yaml"
fi

step "Loading the demo documents"
scripts/seed.sh

step "Antara is running"
echo "Helpdesk:    $API"
echo "Ops console: $API/ops   (choose erin · Operator)"
