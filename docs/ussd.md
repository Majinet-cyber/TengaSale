# Tenga USSD

## Architecture

The provider posts form fields to `POST /api/ussd/`. A provider-neutral adapter validates `sessionId`, `phoneNumber`, optional `serviceCode`, and `text`. The Tenga state machine owns navigation; `CustomerFinanceAccountService` obtains financed contract truth directly from Upya.

The endpoint always returns plain text using `CON` or `END`. It never exposes exceptions, credentials, national IDs, IMEIs or full payment transaction identifiers.

## Menu

```text
Welcome to Tenga
1. Pay
2. Check balance
3. Last payment
4. Contract details
```

Multiple contracts require explicit selection. Balance, expected payment, minimum payment, status, contract details and last payment are Upya-derived. Missing fields are omitted or displayed as unknown; they are never calculated locally.

## Configuration

```text
USSD_ENABLED=false
USSD_PROVIDER=generic
USSD_SERVICE_CODE=
USSD_SESSION_TTL_MINUTES=10
USSD_LOOKUP_RATE_LIMIT=20
USSD_LOOKUP_RATE_WINDOW=60
```

Set `USSD_SERVICE_CODE` when a dedicated code is assigned. Changing codes is configuration-only. When disabled, the route remains available and returns a provider-compatible end response.

## Deployment sequence

1. Configure and verify the Upya Payment Gateway credentials and client identifier.
2. Configure the aggregator callback as `https://<production-host>/api/ussd/`.
3. Set the expected service code and provider name.
4. Enable read-only USSD for a controlled Upya customer.
5. Verify balance, status, details and last payment exactly match Upya.
6. Confirm the production provider-to-Upya payment reference and allocation behavior.
7. Only then implement and enable idempotent payment initiation; do not call Upya confirmation if the provider already does.

## Payment behavior and rollback

Payment initiation currently fails closed with a direction to the secure Tenga payment portal. No fake intent or local credit is created. Disable with `USSD_ENABLED=false`; the callback continues returning HTTP 200 and `END` text.

## Troubleshooting

- Temporary account message: check Upya credentials, client identifier, latency and logs.
- No account: verify the full normalized Malawi subscriber in Upya; partial matching is forbidden.
- Wrong service code: verify `USSD_SERVICE_CODE` and aggregator configuration.
- Never repair an Upya outage by displaying a locally calculated balance.
