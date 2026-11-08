# Public API Rate Limits (Internal Only)

This document is internal. It must never be shared with contractors, customers or partners, because knowing the exact limits helps attackers stay just under them.

The public REST API allows 100 requests per minute per API key on the Free tier, 1,000 requests per minute on the Pro tier, and 10,000 requests per minute on the Enterprise tier.

Bursts of up to 2 times the per-minute limit are allowed for 10 seconds. After that the gateway returns HTTP 429 with a Retry-After header.

The login endpoint is limited to 5 failed attempts per account per 15 minutes, then the account is locked for 30 minutes.

Internal services skip rate limiting by sending the X-Internal-Caller header from inside the service mesh. This header is stripped at the edge, and it must never be documented publicly.

To raise a customer's limit, open a ticket with the API platform team. Raises above 20,000 requests per minute need approval from the head of engineering.
