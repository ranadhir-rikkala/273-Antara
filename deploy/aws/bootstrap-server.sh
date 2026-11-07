#!/usr/bin/env bash
set -euo pipefail
K3S_VERSION="${K3S_VERSION:-v1.31.4+k3s1}"
KEDA_VERSION="${KEDA_VERSION:-2.17.2}"

curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" sh -s - server --write-kubeconfig-mode 644
until kubectl get nodes >/dev/null 2>&1; do sleep 2; done
kubectl apply --server-side -f "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}.yaml"

sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip >/dev/null
pip install -q "huggingface_hub[cli]"
sudo mkdir -p /opt/antara/models && sudo chown "$USER" /opt/antara/models
~/.local/bin/huggingface-cli download nomic-ai/nomic-embed-text-v1.5 --local-dir /opt/antara/models/nomic-embed-text-v1.5

for image in $(grep -h 'image:' ~/deploy/k3s/*.yaml | awk '{print $2}' | grep -v -E 'antara-app|vllm|turing|k8s-device-plugin' | sort -u); do
  case "$image" in */*/*) ref="$image" ;; */*) ref="docker.io/$image" ;; *) ref="docker.io/library/$image" ;; esac
  sudo k3s ctr images pull "$ref" >/dev/null && echo "preloaded $ref"
done

echo "Join token for the GPU node:"
sudo cat /var/lib/rancher/k3s/server/node-token
