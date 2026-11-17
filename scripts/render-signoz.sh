#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/.."
VERSION=0.149.0
helm repo add signoz https://charts.signoz.io >/dev/null 2>&1 || true
helm repo update signoz >/dev/null
helm template signoz signoz/signoz --version "$VERSION" --namespace antara --include-crds \
  -f deploy/signoz/values.yaml \
  | python3 -c '
import sys
docs = [d for d in sys.stdin.read().split("\n---") if d.strip() and "helm.sh/hook" not in d]
crds = [d for d in docs if "kind: CustomResourceDefinition" in d]
rest = [d for d in docs if d not in crds]
open("deploy/signoz/crds.yaml", "w").write("\n---".join(crds) + "\n")
open("deploy/signoz/signoz.yaml", "w").write("\n---".join(rest) + "\n")
'
echo "Rendered SigNoz $VERSION into deploy/signoz/signoz.yaml"
