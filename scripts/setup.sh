#!/usr/bin/env sh
set -e
REPO_URL="https://github.com/ranadhir-rikkala/273-Antara.git"
CLONE_DIR="$HOME/273-Antara"

step() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nsetup failed: %s\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || fail "this setup is for macOS"
ram_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
[ "$ram_gb" -ge 24 ] || echo "warning: this Mac has ${ram_gb} GB of RAM; 24 GB or more is recommended"

step "Installing Homebrew (asks for your Mac password once)"
for brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$brew" ] && eval "$("$brew" shellenv)"
done
if ! command -v brew >/dev/null 2>&1; then
  sudo -v
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$brew" ] && eval "$("$brew" shellenv)"
  done
  command -v brew >/dev/null 2>&1 || fail "Homebrew did not install"
  grep -q 'brew shellenv' "$HOME/.zprofile" 2>/dev/null || echo "eval \"\$($(command -v brew) shellenv)\"" >> "$HOME/.zprofile"
fi
echo "Homebrew ready"

step "Installing Colima, Docker, kubectl, git and Python"
missing=""
for tool in colima docker kubectl git; do
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
python3 -c "import sys" >/dev/null 2>&1 || missing="$missing python"
[ -z "$missing" ] || brew install $missing
echo "tools ready"

step "Starting Docker (Colima with 8 CPUs, 16 GB of memory, 60 GB of disk)"
if ! docker info >/dev/null 2>&1; then
  colima start --cpu 8 --memory 16 --disk 60
fi
docker info >/dev/null 2>&1 || fail "Docker is not running: colima start --cpu 8 --memory 16"
memory=$(docker info --format '{{.MemTotal}}')
if [ "$memory" -lt 15000000000 ]; then
  echo "warning: Docker has $((memory / 1073741824)) GB of memory; run 'colima stop && colima start --cpu 8 --memory 16'"
fi

here=$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)
if [ ! -f "$here/local-cluster.sh" ]; then
  step "Getting the code into $CLONE_DIR"
  if [ -d "$CLONE_DIR/.git" ]; then
    git -C "$CLONE_DIR" pull --ff-only
  else
    git clone "$REPO_URL" "$CLONE_DIR"
  fi
  exec sh "$CLONE_DIR/scripts/setup.sh"
fi
cd "$here/.."
export KUBECONFIG="$PWD/deploy/local/.kube/kubeconfig.yaml"
API="http://localhost:8000"
KEDA="https://github.com/kedacore/keda/releases/download/v2.17.2/keda-2.17.2.yaml"
MODELS="qwen2.5:1.5b nomic-embed-text"
OPERATOR="-H X-User-Id:setup -H X-User-Groups:operators"

has_models() { kubectl -n antara exec "$1" -- ollama list 2>/dev/null | grep -q "qwen2.5:1.5b"; }

step "Starting the two-node cluster"
scripts/local-cluster.sh up

step "Installing KEDA"
kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1 || kubectl apply --server-side -f "$KEDA" >/dev/null
kubectl -n keda rollout status deploy/keda-operator --timeout=300s >/dev/null
echo "KEDA ready"

step "Building and deploying Antara (the first run waits for the models; a timeout here is expected)"
scripts/local-deploy.sh || echo "continuing to the model download"

step "Downloading the models into both Ollama copies"
until [ -n "$(kubectl -n antara get pods -l app=ollama -o name 2>/dev/null)" ]; do sleep 5; done
kubectl -n antara wait --for=jsonpath='{.status.phase}'=Running pod -l app=ollama --timeout=600s >/dev/null
ollama_pods=$(kubectl -n antara get pods -l app=ollama -o jsonpath='{.items[*].metadata.name}')
all_have_models=true
for pod in $ollama_pods; do
  has_models "$pod" || all_have_models=false
done
if $all_have_models; then
  echo "models already present"
else
  kubectl -n antara delete networkpolicy default-deny allow-egress-inside-cluster --ignore-not-found >/dev/null
  for pod in $ollama_pods; do
    for model in $MODELS; do
      kubectl -n antara exec "$pod" -- ollama pull "$model"
    done
  done
fi

step "Blocking internet access again and rolling out"
scripts/local-deploy.sh

step "Waiting for every pod"
for _ in $(seq 1 180); do
  pending=$(kubectl -n antara get pods --no-headers | grep -vcE " (Running|Completed) ") || true
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
