# Airtel Money operations

## Lifecycle and identity

TengaSale creates a unique, alphanumeric `internal_reference` (maximum 64 characters). It is the merchant correlation and idempotency key, not an Airtel-generated ID. `provider_reference`, `airtel_transaction_id`, `airtel_reference_id`, and `airtel_money_id` are populated only from explicit provider-owned response fields. An echoed `transaction.id` is retained in the raw payload but is not promoted to a provider ID.

Initiation validates a Malawi Airtel number, stores `+265…`, sends Airtel the nine-digit national number, and creates the Airtel and portal transactions atomically. An accepted request remains `PENDING`; HTTP 200 or an initiation message of `SUCCESS` never posts money. Only a verified final callback or verified enquiry result calls the idempotent success-posting service.

Supported inputs such as `0992304851`, `992304851`, `265992304851`, `+265992304851`, spaces, hyphens and brackets all normalize to UI display `265992304851`, database value `+265992304851`, and Airtel payload `992304851`. Airtel starts with 9 and TNM with 8. Letters, repeated/foreign country codes and malformed lengths are rejected before the client is called.

Initiation uses a browser/server idempotency key plus a contract row lock. Refreshes and repeated submissions reuse the pending attempt. Customer status URLs contain a non-guessable token. Pending, confirmation-delayed and reconciliation-required all mean no money is posted and the customer must not pay again. Only a posted success is shown as confirmed.

Statuses normalize punctuation and casing. `TS`/success are final success, `TIP`/pending remain pending, `TF`/failed are final failure, and `TE`/expired are final expiry. Unknown callback or enquiry values preserve the prior transaction state and require reconciliation.

## Callbacks

`/api/payments/airtel/callback/` logs every attempt, sanitized headers, raw/parsed body, network metadata, authentication result, extracted values, matching trace, processing result, and returned response. Authorization, API-key, and cookie values are masked. Matching order is internal reference, confirmed provider reference, Airtel Money ID, confirmed Airtel transaction ID, then Airtel reference ID. Ambiguous and unmatched callbacks are retained and never post money. Amount mismatches are quarantined. A missing amount can be accepted only with a strong identifier match and final success.

Production requires HTTPS, explicit production approval, and callback authentication. `/api/payments/airtel/health/` is the non-money-moving reachability endpoint.

`/api/payments/airtel/callback-health/` accepts GET, HEAD or POST for connectivity testing, is rate-limited, returns a correlation ID and never reads a payload as payment evidence. The real callback accepts POST only; controlled 405 responses are recorded in the evidence ledger.

Every Django ingress record includes a body SHA-256 fingerprint, proxy/source addresses, request IDs, candidate identifiers, matching decision, response and duration. **No callback record means no matching request reached the Django callback application. It does not prove that Airtel made no delivery attempt, because a request could fail before reaching Django.**

## Enquiry and reconciliation

Enquiry prefers confirmed provider identifiers. The internal reference is used only when `AIRTEL_ENQUIRY_SUPPORTS_MERCHANT_REFERENCE=true`. Every attempt records its reference, path, response, status, and error. “Transaction Not Found” and unknown results preserve `PENDING`.

Commands:

```text
python manage.py airtel_reconcile --reference TENGA…
python manage.py airtel_reconcile --pending-older-than 5 --limit 100
python manage.py airtel_reconcile --pending-older-than 30 --limit 100 --apply
python manage.py airtel_show_transaction TENGA…
python manage.py airtel_trace TENGA…
python manage.py airtel_callbacks --reference TENGA…
python manage.py airtel_callbacks --unmatched --errors
python manage.py airtel_callback CALLBACK_ID
python manage.py airtel_backfill_callback_diagnostics --dry-run
python manage.py airtel_backfill_callback_diagnostics --apply
python manage.py airtel_reprocess_callback CALLBACK_ID --dry-run
python manage.py airtel_reprocess_callback CALLBACK_ID --apply --reason "incident reference" --confirm REPROCESS
python manage.py airtel_evidence TENGA… --output airtel-evidence.json
```

Reconciliation defaults to dry-run. `--apply` performs rate-limited enquiries; verified success still passes through the same row-locked, exactly-once posting path. Callback attempts and transactions are searchable in Django admin. There is deliberately no “mark paid” action.

Merchant-reference enquiry is disabled unless `AIRTEL_ALLOW_MERCHANT_REFERENCE_ENQUIRY=true`; do not enable it without written Airtel confirmation. Enquiries have a minimum interval and attempt limit. Historical backfill derives only facts present in retained payloads and leaves uncaptured evidence absent.

HTTP interpretation: 200 means TengaSale accepted/handled the callback, not necessarily that money posted; 400 means invalid payload or amount; 401 means authentication failed; 405 means wrong method; a timeout or 404 needs edge/Django routing evidence; ambiguous, amount-mismatch and unknown-status records never post. “Transaction Not Found” preserves pending.

UAT may explicitly disable callback authentication and will show that callbacks are not cryptographically verified. Production checks block missing/disabled signing configuration. The existing HMAC hook is configuration plumbing, not a claim that Airtel has confirmed its official algorithm; obtain the exact headers, canonicalization, algorithm and secret source from Airtel before launch. Source IP is diagnostic only, never proof of authenticity.

## Incident checklist

| Symptom | Check |
|---|---|
| Prompt not received / sender unavailable | Initiation response, normalized MSISDN, credentials, allowlist and provider status |
| Invalid MSISDN / reference | Confirm Airtel payload has nine national digits and an alphanumeric reference |
| IP not allowed | Compare deployment egress IP with Airtel’s allowlist |
| Callback missing | Check callback log, health endpoint, Airtel callback URL and edge/server logs |
| Callback unmatched | Inspect extracted candidates and matching trace; correct identifiers, then reconcile |
| Callback rejected | Inspect authentication mode/signature result and response status |
| Transaction Not Found | Confirm the enquiry reference/path; preserve pending and escalate with the diagnostic trace |
| Airtel SMS says successful but TengaSale is pending | Do not pay again; inspect callbacks, then run a provider-ID enquiry/reconciliation |
| Duplicate callback | Confirm it is logged `DUPLICATE`; contract and ledger must remain unchanged |
| Amount mismatch | Do not post; compare initiation and callback evidence and escalate to Airtel |

When escalating, send the internal reference, masked number, amount, timestamps, explicit provider identifiers, sanitized initiation response, callback log IDs, and enquiry trace. Never send secrets, authorization headers, cookies, PINs, or private keys.

Ask Airtel for timestamp plus timezone, exact destination URL and method, source IP, request/trace ID, payload transaction ID, HTTP response status/body and retry count.

Production launch checklist: apply migrations; resolve all deployment checks; configure HTTPS callback URL and official authentication; confirm enquiry identifier/path; configure reconciliation interval/limits; confirm Airtel IP/network routing; run health connectivity; perform a controlled real callback; retain evidence; never manually mark UAT money paid.

UAT incident checklist: capture internal reference; tell the customer not to retry; run `airtel_trace`; search callbacks by time, ID and source; export evidence; run reconciliation dry-run; apply only a verified final enquiry; escalate absent ingress using the wording above.

## Callback observability boundary and retention

The callback view commits an `AirtelCallbackLog` evidence row before method validation, JSON parsing, authentication, matching, or financial processing. If that insert fails, processing stops with HTTP 503 and a critical application log. Raw body, selected headers, request metadata, fingerprint, and ingress identifiers are immutable in normal application flows. Authorized reprocessing creates a separate replay callback plus an `AirtelCallbackProcessingAttempt`; it never rewrites the original ingress evidence.

Retain callback evidence for at least `AIRTEL_CALLBACK_EVIDENCE_RETENTION_DAYS` (default 2555 days/seven years), subject to the applicable legal retention schedule. Normal operator screens show only masked subscriber digits. Raw bodies, parsed payloads, headers, and response bodies are restricted to superusers in Django admin. Database backups must use the same access controls and retention schedule. Source IP is retained for incident investigation and is never authentication evidence.

Run `python manage.py airtel_observability_alerts --notify` on a scheduler to alert Django administrators about successful unresolved callbacks, processing failures, and transactions older than `AIRTEL_PENDING_CALLBACK_TIMEOUT_MINUTES` without a callback. Run `python manage.py airtel_reconciliation_report` to compare callback, enquiry, transaction, and repayment-posting evidence without changing financial state.

Django evidence proves only that a request reached Django. Render access logs help investigate requests that reached the service but failed before Django processing. No application can record a callback that Airtel never transmitted or that never reached the hosting infrastructure.
