# Software Release and Deployment Policy

This policy applies to everyone who ships code, including contractors.

Every change reaches the main branch through a pull request with at least one approving review. Changes to authentication, payments or infrastructure need two reviews.

The automated test suite and the security scan must pass before a pull request can merge.

Production deploys happen Monday to Thursday between 9 AM and 3 PM. There are no deploys on Fridays, weekends or company holidays unless it is an emergency fix.

A code freeze runs from December 15 to January 5 every year.

Every deploy must have a rollback plan. If error rates double after a deploy, roll back first and investigate afterwards.
