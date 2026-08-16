# Upya integration

## Authority boundary

> TENGA OWNS THE EXPERIENCE. UPYA OWNS PAYGO TRUTH.

For financed PAYGo contracts, Upya is authoritative for balances, payments, schedules, contract state and device entitlement. Tenga may retain local projections and operational records, but customer-facing finance reads must not derive authoritative values from them or silently fall back to them.

Cash sales are outside this rule.

## API surfaces

- Manage API: `UPYA_MANAGE_BASE_URL` (default `https://data.upya.io`), with separate Manage credentials.
- Payment Gateway: `UPYA_PAYMENT_GATEWAY_BASE_URL` (default `https://mm.api.upya.io`), with separate Payment Gateway credentials and client identifier.

Implemented Payment Gateway reads:

- `POST /api/payment/options/{clientIdentifier}`
- `GET /api/customer/details`
- `GET /api/customer/lastPayment`
- `POST /api/payment/checkStatus` (generic documented payload only)

Payment validation and confirmation are intentionally not called. The current provider-to-Upya allocation behavior must be confirmed first to prevent duplicate reconciliation.

## Configuration

```text
UPYA_ENABLED=false
UPYA_MANAGE_BASE_URL=https://data.upya.io
UPYA_MANAGE_USERNAME=
UPYA_MANAGE_PASSWORD=
UPYA_PAYMENT_GATEWAY_BASE_URL=https://mm.api.upya.io
UPYA_PAYMENT_GATEWAY_USERNAME=
UPYA_PAYMENT_GATEWAY_PASSWORD=
UPYA_PAYMENT_GATEWAY_CLIENT_IDENTIFIER=
UPYA_WEBHOOK_API_KEY=
UPYA_WEBHOOK_API_KEY_HEADER=api-key
UPYA_CONNECT_TIMEOUT=5
UPYA_READ_TIMEOUT=10
```

Never expose credentials to browser JavaScript or logs. Run `python manage.py upya_check` for configuration status, or add `--reference` for a read-only live lookup.

## Current audit map

| Current component | Current authority | Required authority | Change |
|---|---|---|---|
| Legacy `/api/ussd/` balance | Local `PaymentContract` arithmetic | Upya Payment Gateway | Replaced with fresh Upya payment options; no fallback |
| Legacy USSD payment intent | Local pending record only | Proven provider initiation linked to Upya | Disabled fail-closed pending reference confirmation |
| Airtel callback posting | Local repayment ledger/device-day mutation | Upya for financed PAYGo | Production-sensitive legacy remains; must be migrated after callback allocation is verified |
| Web/merchant/support finance screens | Primarily local models | Shared Upya-backed account service | Shared service added; screen migration remains |
| `integrations/upya_provider.py` | Unconfigured device stub | Upya Manage lifecycle | Preserved; Manage implementation remains |

## Known blocker

The repository does not contain evidence of the exact production payment reference accepted by the working provider-to-Upya callback. Until it is confirmed, USSD payment initiation and `/api/payment/confirmation` remain disabled. This is deliberate fail-closed behavior.
