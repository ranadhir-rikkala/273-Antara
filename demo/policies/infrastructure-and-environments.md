# Infrastructure and Environments (Internal Only)

This document is internal to employees. Contractors and vendors must not receive internal hostnames or cluster names.

There are three environments: dev, staging and prod. Each runs in its own Kubernetes cluster: dev-k8s, staging-k8s and prod-k8s.

The prod cluster runs in the us-west region across three availability zones. Staging mirrors prod at one quarter of its size.

Engineers reach the prod cluster only through the bastion host bastion.prod.internal, with single sign-on and a hardware security key. Direct SSH into prod nodes is disabled.

Infrastructure changes are made only through Terraform in the infra repository. Changes made by hand in the cloud console are reverted automatically every night.

The monthly cloud budget is 180,000 USD. Any new service expected to cost more than 5,000 USD per month needs approval from the head of engineering.
