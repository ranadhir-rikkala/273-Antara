#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
source "$STATE_FILE"

case "${1:-}" in
  lock)
    aws ec2 revoke-security-group-egress --group-id "$SG_ID" --protocol -1 --cidr 0.0.0.0/0 2>/dev/null || true
    aws ec2 authorize-security-group-egress --group-id "$SG_ID" --protocol -1 --cidr "$VPC_CIDR" 2>/dev/null || true
    echo "Egress locked: nodes can only reach $VPC_CIDR"
    ;;
  unlock)
    aws ec2 authorize-security-group-egress --group-id "$SG_ID" --protocol -1 --cidr 0.0.0.0/0 2>/dev/null || true
    echo "Egress OPEN (break-glass). Run '$0 lock' when done."
    ;;
  status)
    aws ec2 describe-security-groups --group-ids "$SG_ID" --query 'SecurityGroups[0].IpPermissionsEgress[].IpRanges[].CidrIp'
    ;;
  *)
    echo "usage: $0 lock|unlock|status" >&2
    exit 1
    ;;
esac
