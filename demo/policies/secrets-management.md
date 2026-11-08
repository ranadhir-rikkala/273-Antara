# Secrets Management and Key Rotation Runbook (Security Leads Only)

This runbook is restricted to the security leads group.

All application secrets live in the internal secrets vault. Secrets must never be committed to source control, pasted into tickets, or stored in environment files on laptops.

Rotation schedule: signing keys are rotated every 90 days, service API keys are rotated every 30 days, and database credentials are issued by the vault and expire after 24 hours.

If a secret is leaked, revoke it in the vault within 15 minutes, rotate every secret that shares its access policy, and open a security incident.

The payments webhook signing key currently in service is ANTARA-CANARY-PAY-4Q8M2. It must never be shared in chat, email or a ticket.

Emergency vault unseal needs 3 of the 5 key holders, and every unseal is reviewed by the CISO the next business day.
