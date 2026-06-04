# Didit KYC integration (TengaSale)

TengaSale uses [Didit](https://didit.me) as the **only** automated identity verification provider for financed smartphone applications in Malawi. Didit complements (does not replace) manual KYC photo capture, signatures, and location/work steps.

## What Didit does

After customer details are saved, merchants can start a hosted Didit session covering:

- Government ID verification
- Selfie / liveness
- Face match
- AML screening
- IP / device risk signals

Results are stored on `FinancingApplication` and shown to underwriters on the identity review screen. Status updates arrive via Didit webhooks (v3.0).

## Required environment variables

| Variable | Description |
|----------|-------------|
| `DIDIT_API_KEY` | API key from Didit dashboard (`x-api-key` header) — **never commit** |
| `DIDIT_WORKFLOW_ID` | Workflow UUID for Malawi PayGo KYC |
| `DIDIT_WEBHOOK_SECRET` | Secret for `X-Signature-V2` verification — **required in production** |
| `DIDIT_WEBHOOK_URL` | Public URL registered in Didit (informational) |
| `DIDIT_CALLBACK_URL` | User return URL after verification (HTTPS in production) |
| `DIDIT_ALLOW_UNSIGNED_WEBHOOKS` | `True` only for local dev without webhook secret |
| `REQUIRE_DIDIT_KYC_BEFORE_APPROVAL` | `True` blocks underwriter approval unless KYC is approved |

### `.env.example` (placeholders only)

```env
DIDIT_API_KEY=
DIDIT_WORKFLOW_ID=
DIDIT_WEBHOOK_SECRET=
DIDIT_WEBHOOK_URL=
DIDIT_CALLBACK_URL=
DIDIT_ALLOW_UNSIGNED_WEBHOOKS=False
REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=False
```

Real secrets belong only in:

- Local gitignored `tengasale/.env`
- Render environment variables
- Your machine environment

## Production hardening after test

1. **Create a fresh TengaSale API key** in the Didit dashboard (do not reuse keys pasted in chat or tickets).
2. Set **`DIDIT_API_KEY`** on Render to the new key.
3. **Delete/disable** any old test API key in Didit.
4. **Create or regenerate** the webhook destination (dashboard or management command below).
5. Save the returned signing secret as **`DIDIT_WEBHOOK_SECRET`** on Render immediately (it may not be shown again).
6. Set **`DIDIT_ALLOW_UNSIGNED_WEBHOOKS=False`**.
7. Set **`REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=True`** only when operations are ready to enforce KYC on approval (see staged rollout).
8. **Redeploy** TengaSale (`DJANGO_SETTINGS_MODULE=config.settings_production`).
9. Run **one real** customer verification end-to-end.
10. Confirm the **underwriter identity panel** shows Didit decision/summary.
11. Confirm **approval is blocked** when the gate is enabled and KYC is not approved; HQ override requires a written reason.

### Production settings enforced (`DEBUG=False`)

When using `config.settings_production`, Django will refuse to start if:

- `DIDIT_ALLOW_UNSIGNED_WEBHOOKS=True`
- `DIDIT_WEBHOOK_SECRET` is missing
- `DIDIT_API_KEY` or `DIDIT_WORKFLOW_ID` is missing
- `DIDIT_CALLBACK_URL` or `DIDIT_WEBHOOK_URL` is not HTTPS

Unsigned webhooks are also rejected at runtime when `DEBUG=False`, even if the unsigned flag were set by mistake.

## Staged rollout: approval gate

| Phase | `REQUIRE_DIDIT_KYC_BEFORE_APPROVAL` | Behaviour |
|-------|-------------------------------------|-----------|
| Pilot | `False` | Underwriters see a warning if Didit KYC is not approved; approval still allowed |
| Live  | `True`  | Approval blocked until `kyc_status=approved`; HQ/superuser override needs a reason |

Switch to `True` only after webhooks and at least one successful live verification are confirmed.

## Getting `DIDIT_WEBHOOK_SECRET`

### Option A — Management command (recommended)

```bash
python manage.py didit_create_webhook_destination \
  --url https://tengasale.onrender.com/api/webhooks/didit/ \
  --label "TengaSale Production Webhook"
```

If Didit returns a secret, the command prints it once with instructions to save it as `DIDIT_WEBHOOK_SECRET` on Render.

List existing destinations (no secrets):

```bash
python manage.py didit_list_webhook_destinations
```

If creation fails with a duplicate URL message, delete the existing destination in the Didit dashboard or use a different label/URL, then rerun.

### Option B — Didit dashboard

Create webhook destination:

- URL: `https://tengasale.onrender.com/api/webhooks/didit/`
- Version: v3.0
- Events: `status.updated`, `data.updated`

Copy the signing secret when shown. If the dashboard does not display it, use the management command or Didit support.

## Render setup

1. Set environment variables:

```env
DIDIT_API_KEY=<your-production-key>
DIDIT_WORKFLOW_ID=<your-workflow-uuid>
DIDIT_WEBHOOK_SECRET=<from-didit>
DIDIT_WEBHOOK_URL=https://tengasale.onrender.com/api/webhooks/didit/
DIDIT_CALLBACK_URL=https://tengasale.onrender.com/kyc/didit/done/
DIDIT_ALLOW_UNSIGNED_WEBHOOKS=False
REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=False
```

(Use `True` for the last variable when ready for enforced KYC.)

2. `DJANGO_SETTINGS_MODULE=config.settings_production`
3. `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` must include `https://tengasale.onrender.com`
4. After deploy:

```bash
python manage.py migrate
python manage.py check --deploy
python manage.py test applications.tests_didit
```

5. In Didit dashboard, use **Test Webhook** and confirm a row appears in Django admin → **Didit Webhook Events**.

## Before production deploy (checklist)

- [ ] Fresh `DIDIT_API_KEY` on Render (test key revoked)
- [ ] `DIDIT_WEBHOOK_SECRET` set; unsigned webhooks disabled
- [ ] `DIDIT_CALLBACK_URL` and `DIDIT_WEBHOOK_URL` use HTTPS
- [ ] Latest code deployed from GitHub
- [ ] Migrations applied
- [ ] `python manage.py check --deploy` passes
- [ ] `applications.tests_didit` passes
- [ ] Didit test webhook received (admin audit row)
- [ ] One live verification completed
- [ ] Underwriter panel shows status/summary
- [ ] Approval gate tested (pilot `False`, then `True` when ready)
- [ ] No secrets in git (only placeholders in `.env.example`)

## Application flow

1. Merchant saves **Customer Details**
2. **Didit KYC** step (`/applications/<id>/kyc/didit/`) — does not block later steps when gate is off
3. Phone deal, manual KYC photos, location, work, signature, review
4. Underwriter sees **Didit KYC** panel on identity check
5. Approval respects `REQUIRE_DIDIT_KYC_BEFORE_APPROVAL`

## API endpoints (TengaSale)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/applications/<id>/kyc/didit/start/` | Create Didit session (JSON) |
| POST | `/applications/<id>/kyc/didit/refresh/` | Pull latest decision (underwriter/HQ/admin only) |
| GET | `/kyc/didit/done/` | Customer callback page |
| POST | `/api/webhooks/didit/` | Didit webhooks (CSRF exempt, signed in production) |

## How to start KYC (merchant)

1. Open an application and complete customer details.
2. On the Didit card, click **Start KYC Verification**.
3. The hosted Didit URL opens in a new tab; customer completes verification.
4. Didit redirects to the callback page; webhooks update status in TengaSale.

## How to test the webhook locally

Only when `DEBUG=True` and `DIDIT_ALLOW_UNSIGNED_WEBHOOKS=True`:

```bash
curl -X POST http://127.0.0.1:8000/api/webhooks/didit/ \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "test-evt-001",
    "webhook_type": "status.updated",
    "session_id": "your-session-id",
    "status": "Approved",
    "metadata": {"application_id": "123", "source": "tengasale"},
    "vendor_data": "123"
  }'
```

Duplicate `event_id` values return HTTP 200 without reprocessing.

For signed local tests, set `DIDIT_WEBHOOK_SECRET` and send `X-Signature-V2` / `X-Timestamp` headers matching the canonical V2 algorithm in `integrations/didit.py`.

## How to test one customer verification

1. Set `DIDIT_API_KEY` and `DIDIT_WORKFLOW_ID` in gitignored `.env`.
2. `python manage.py migrate` and `python manage.py runserver`
3. Merchant: new application → customer details → start Didit KYC
4. Complete hosted verification
5. Check **Didit Webhook Events** in admin and underwriter identity panel

## HQ override (when gate enabled)

If `REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=True` and KYC is not approved, HQ or superuser may approve with a **required written reason**, stored as:

- `didit_manual_override`
- `didit_manual_override_reason`
- `didit_manual_override_by`
- `didit_manual_override_at`

## Security notes

- API keys and webhook secrets are read from environment only.
- Production never accepts unsigned webhooks.
- Webhook and API logs include `event_id`, `session_id`, `status`, `webhook_type`, and `application_id` — never secrets.
- Rotate any API key that was shared outside Render/env.
