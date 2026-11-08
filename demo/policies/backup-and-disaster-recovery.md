# Backup and Disaster Recovery Plan (Internal Only)

This plan is internal to employees. It must not be shared with contractors, vendors or customers.

If a customer-facing database is lost, we lose at most 15 minutes of data (the RPO), and customer-facing services must be running again within 4 hours (the RTO).

Internal tools are less critical: they can lose up to 24 hours of data and must be running again within 2 business days.

Databases are backed up every 15 minutes to the primary backup vault in the us-west region, and a full copy is replicated nightly to the disaster recovery site in us-east.

Backups are kept for 35 days. Monthly snapshots are kept for 1 year for audit.

The disaster recovery site is tested with a full failover drill twice a year, in March and September. Only the infrastructure on-call engineer and the head of engineering may start a failover.
