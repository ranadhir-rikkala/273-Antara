#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/.."
API="${ANTARA_URL:-http://localhost:8000}"
EVERYONE="employees,contractors,managers,security-leads"
INTERNAL="employees,managers,security-leads"
AUTH_USER="X-User-Id: seed"
AUTH_GROUPS="X-User-Groups: employees,contractors,managers,security-leads,operators"

audience() {
  case "$1" in
    open-vulnerabilities-embargoed.md | secrets-management.md) echo "security-leads" ;;
    hr-compensation-confidential.md) echo "employees,managers" ;;
    api-rate-limits.md | backup-and-disaster-recovery.md | customer-data-classification.md | infrastructure-and-environments.md | payments-service-architecture.md) echo "$INTERNAL" ;;
    printing.md | vpn-faq.md | wifi.md) echo "employees" ;;
    *) echo "$EVERYONE" ;;
  esac
}

existing=$(curl -fs -H "$AUTH_USER" -H "$AUTH_GROUPS" "$API/documents")
added=0
for file in demo/policies/*.md; do
  name=$(basename "$file")
  if printf '%s' "$existing" | grep -q "\"title\": *\"$name\""; then
    echo "already loaded  $name"
    continue
  fi
  curl -fs -o /dev/null -H "$AUTH_USER" -H "$AUTH_GROUPS" -F "file=@$file" -F "groups=$(audience "$name")" "$API/documents"
  echo "uploaded        $name  ->  $(audience "$name")"
  added=$((added + 1))
done

total=$(ls demo/policies/*.md | wc -l | tr -d ' ')
printf 'waiting for %s documents to be READY' "$total"
for _ in $(seq 1 120); do
  ready=$(curl -fs -H "$AUTH_USER" -H "$AUTH_GROUPS" "$API/documents" | grep -o '"status": *"READY"' | wc -l | tr -d ' ')
  [ "$ready" -ge "$total" ] && break
  printf '.'
  sleep 5
done
echo
echo "$added new, $ready READY"
