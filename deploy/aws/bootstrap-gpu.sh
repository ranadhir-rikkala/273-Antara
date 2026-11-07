#!/usr/bin/env bash
set -euo pipefail
SERVER_IP="${1:?usage: bootstrap-gpu.sh <server-private-ip> <join-token>}"
TOKEN="${2:?usage: bootstrap-gpu.sh <server-private-ip> <join-token>}"
K3S_VERSION="${K3S_VERSION:-v1.31.4+k3s1}"

nvidia-smi
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" K3S_URL="https://${SERVER_IP}:6443" K3S_TOKEN="$TOKEN" \
  sh -s - agent --node-label antara/gpu=true --node-taint antara/gpu=true:NoSchedule

pip install -q "huggingface_hub[cli]"
sudo mkdir -p /opt/antara/models && sudo chown "$USER" /opt/antara/models
~/.local/bin/huggingface-cli download Qwen/Qwen2.5-7B-Instruct-AWQ --local-dir /opt/antara/models/Qwen2.5-7B-Instruct-AWQ
~/.local/bin/huggingface-cli download nomic-ai/nomic-embed-text-v1.5 --local-dir /opt/antara/models/nomic-embed-text-v1.5

for ref in docker.io/vllm/vllm-openai:v0.6.4 \
  ghcr.io/huggingface/text-embeddings-inference:turing-1.5 \
  nvcr.io/nvidia/k8s-device-plugin:v0.17.0; do
  sudo k3s ctr images pull "$ref" >/dev/null && echo "preloaded $ref"
done
