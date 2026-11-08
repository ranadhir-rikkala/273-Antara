# Payments Service Architecture (Confidential)

This document is confidential to employees. Contractors and vendors must not receive it.

The payments service runs in the restricted payments network segment. Its internal hostname is payments-core.internal on port 8443, and only the checkout and billing services may call it.

Card numbers are never stored by the payments service. They are exchanged for tokens by the card vault, and only the token is saved in the orders database.

The fraud engine blocks any transaction with a risk score of 85 or higher, and sends scores from 70 to 84 to manual review. These thresholds are confidential, because an attacker who knows them can tune transactions to stay below them.

Refunds above 5,000 USD need a second approver from the finance team.

The payments service is deployed only on Tuesdays and Thursdays, outside the 6 PM to 9 PM peak checkout window.
