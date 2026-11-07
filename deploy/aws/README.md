# Deploying Antara on AWS

Two EC2 nodes in one VPC run k3s: a `t3.xlarge` server node and a `g4dn.xlarge` GPU node. Models and images are preloaded while egress is open, then egress is locked so nothing leaves the VPC.

## Before you start

1. Upgrade the AWS account to the Paid plan (free-plan accounts can't launch GPU instances; credits still apply).
2. In Service Quotas, request at least 4 vCPUs for **Running On-Demand G and VT instances** in your region.
3. Create a budget alert. AWS Budgets lags by hours, so check the bill daily too.
4. Install the AWS CLI and run `aws configure`.

## 1. Create the network and nodes

```bash
export TEAM_CIDR="$(curl -s https://checkip.amazonaws.com)/32"
deploy/aws/create-infra.sh
```

This writes the instance and security group IDs to `deploy/aws/.state` and the SSH key to `antara-key.pem` (both git-ignored).

## 2. Bootstrap the server node

```bash
scp -i antara-key.pem -r deploy ubuntu@<server-public-ip>:
ssh -i antara-key.pem ubuntu@<server-public-ip> bash deploy/aws/bootstrap-server.sh
```

The last line it prints is the join token for the GPU node.

## 3. Bootstrap the GPU node

```bash
scp -i antara-key.pem deploy/aws/bootstrap-gpu.sh ubuntu@<gpu-public-ip>:
ssh -i antara-key.pem ubuntu@<gpu-public-ip> bash bootstrap-gpu.sh <server-private-ip> <join-token>
```

## 4. Deploy Antara

```bash
deploy/aws/deploy.sh
```

It builds the app image for `linux/amd64`, imports it on both nodes, and applies `deploy/k3s`.

## 5. Lock egress

```bash
deploy/aws/egress.sh lock
```

From here the nodes can only reach addresses inside the VPC, and the NetworkPolicies stop pods reaching anything outside the namespace. `deploy/aws/egress.sh unlock` reopens egress briefly for the before/after canary demo.

## Open the app and dashboards

Everything is reached through an SSH tunnel; nothing is exposed publicly.

```bash
ssh -i antara-key.pem -L 8000:localhost:80 ubuntu@<server-public-ip>
```

Then open http://localhost:8000. For the dashboards, port-forward on the server node:

```bash
kubectl -n antara port-forward svc/grafana 3000 & kubectl -n antara port-forward svc/jaeger 16686 & kubectl -n antara port-forward svc/temporal-ui 8233:8080
```

## Save credits

```bash
deploy/aws/power.sh stop-gpu
deploy/aws/power.sh stop
deploy/aws/power.sh start
```

Stopped instances still pay for their EBS volumes.

## Demo commands

| Demo | Command on the server node |
| --- | --- |
| Air gap | `kubectl -n antara exec deploy/api -- python -c "import httpx; httpx.get('https://api.openai.com', timeout=5)"` times out |
| Failure recovery | `kubectl -n antara delete pod -l app=agent` during a run |
| Lose the GPU | `kubectl -n antara scale deploy/tei-gpu deploy/ingest-gpu --replicas=0` |
| Model outage | `kubectl -n antara exec deploy/gateway -- python -c "import httpx; httpx.post('http://localhost:8080/admin/chaos', json={'fail_seconds': 30})"` |
| Burst | `python -m loadtest.burst --api http://localhost:8000 --docs 200 --questions 50` from your laptop through the tunnel |
| Scaling | `kubectl -n antara get hpa,pods -w` while the burst runs |
