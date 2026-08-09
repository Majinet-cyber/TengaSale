# Tenga Protect database migration plan

1. Deploy code with `PROTECT_LOCATION_ENABLED=false` and apply `protect.0001_initial` plus the explicit-role seed migration.
2. Backfill `DeviceRegistryEntry` lazily from verified contract/application IMEIs. The registry references existing contracts and does not duplicate customer identity.
3. Assign Protect roles manually under least privilege. Existing admin, HQ, merchant, underwriter, support and collections accounts receive no implicit access.
4. Launch customer reporting, ownership certificates, audit and public verification. Monitor file storage, permission denials and case creation.
5. Configure encrypted production object storage and a malware-scanning hook before accepting production evidence at scale.
6. Obtain Malawi legal review, provider authorisation, DPIA/security review, MFA enforcement and retention approval before enabling location.
7. Enable location only with both the global flag and provider-specific flag, then validate maker-checker, expiry, alerting and deletion jobs in staging.
8. Backout is non-destructive: disable feature flags. Preserve Protect cases, consent, documents, certificates and immutable audits for the approved retention period.
