# Open Vulnerabilities Under Embargo (Security Leads Only)

This document is restricted to the security leads group. Details stay internal until the fix ships and the disclosure date passes.

The only critical open vulnerability is SEC-2026-021: an outdated image processing library in the media service allows remote code execution. It must be patched within 72 hours, because it is being actively exploited elsewhere.

The high severity open vulnerability is SEC-2026-014: the legacy file export endpoint allows path traversal for authenticated users. It is blocked at the web firewall and will be fixed in the next release.

The medium severity open vulnerability is SEC-2026-017: the mobile app caches session tokens without encryption on older Android versions. It must be fixed within 30 days.

Disclosure to customers happens only after the fix is deployed and the security leads approve the advisory.
