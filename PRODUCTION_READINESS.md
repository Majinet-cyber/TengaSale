# TengaSale Production Readiness Guide

## Quick checklist

| Item | Status | Notes |
|---|---|---|
| `DEBUG=False` | ✅ In `settings_production.py` | Never deploy with DEBUG=True |
| `SECRET_KEY` from env | ✅ Required — no default | Set `DJANGO_SECRET_KEY` |
| `ALLOWED_HOSTS` | ✅ From env | Comma-separated |
| `CSRF_TRUSTED_ORIGINS` | ✅ From env | Include your domain(s) |
| HTTPS redirect | ✅ `SECURE_SSL_REDIRECT=True` | Requires valid TLS cert |
| Secure cookies | ✅ SESSION+CSRF cookies secure | HTTP-only enforced |
| HSTS | ✅ 1 year | Test before enabling preload |
| PostgreSQL | ✅ Via `DATABASE_URL` | `pip install dj-database-url psycopg2-binary` |
| Static files | ✅ `STATIC_ROOT` | Run `collectstatic` before deploy |
| Media files | ✅ `MEDIA_ROOT` | Serve via nginx — never expose raw |
| Email (SendGrid) | ✅ Auto-configured when key present | Falls back to console |
| SMS (Twilio) | ✅ Mock mode if no credentials | Set `MOCK_SMS=false` for live |
| PayChangu live | ✅ Keys from env | Set `MOCK_PAYMENTS=false` |
| KYC images | ✅ Protected media, no public URLs | Enforce in nginx config |
| Call recordings | ✅ Protected media | Staff/HQ access only |
| Audit logging | ✅ All sensitive actions | `core.AuditLog` |
| Fraud check | ✅ Before every approval | `risk.services.run_fraud_check` |

---

## Environment variables

```env
# Django
DJANGO_SECRET_KEY=your-secret-key-here
DJANGO_SETTINGS_MODULE=config.settings_production
ALLOWED_HOSTS=tengasale.com,www.tengasale.com
CSRF_TRUSTED_ORIGINS=https://tengasale.com,https://www.tengasale.com

# Database
DATABASE_URL=postgres://user:password@host:5432/tengasale

# File storage
STATIC_ROOT=/var/www/tengasale/staticfiles
MEDIA_ROOT=/var/www/tengasale/media

# Email
SENDGRID_API_KEY=SG.xxxxx
DEFAULT_FROM_EMAIL=noreply@tengasale.com
ADMIN_ALERT_EMAIL=admin@tengasale.com
MOCK_EMAIL=false

# PayChangu
PAYCHANGU_PUBLIC_KEY=pk_...
PAYCHANGU_SECRET_KEY=sk_...
PAYCHANGU_WEBHOOK_SECRET=wh_...
PAYCHANGU_API_BASE=https://api.paychangu.com
PAYCHANGU_CALLBACK_URL=https://tengasale.com/pay/webhooks/paychangu/
MOCK_PAYMENTS=false

# Twilio SMS
TWILIO_ACCOUNT_SID=ACxxx
TWILIO_AUTH_TOKEN=xxx
TWILIO_PHONE_NUMBER=+12025551234
MOCK_SMS=false

# Device locking (optional)
MOCK_DEVICE_LOCKING=true

# Support
TENGA_WHATSAPP_NUMBER=
```

---

## Deployment steps

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install dj-database-url psycopg2-binary gunicorn whitenoise
   ```

2. **Run migrations**
   ```bash
   python manage.py migrate
   ```

3. **Collect static files**
   ```bash
   python manage.py collectstatic --noinput
   ```

4. **Seed data (first deploy only)**
   ```bash
   python manage.py seed_review_questions
   ```

5. **Start Gunicorn**
   ```bash
   gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
   ```

6. **Configure nginx** to serve `STATIC_ROOT` and `MEDIA_ROOT` directly, and proxy `/` to Gunicorn.

---

## Media file security

Serve KYC images and call recordings through Django views (not nginx directly):

```nginx
# Do NOT add this — never serve /media/ directly in production:
# location /media/ { root /var/www/tengasale; }

# Instead, proxy all requests to Django/Gunicorn:
location / {
    proxy_pass http://127.0.0.1:8000;
}
```

Django views enforce authentication before serving protected media files.

---

## Security isolation

| Role | Access |
|---|---|
| Visitor / customer | `/pay/`, `/site/` only. Masked phone, no KYC, no commissions. |
| Merchant | Own applications and settlements only. No HQ. |
| Underwriter | Claimable/assigned applications. No merchant financial data. |
| HQ / admin | Full dashboard, reports, simulations, operations. |
| Superuser | Django admin + all above. |

---

## Known limitations before Phase 11

- **PayChangu webhooks**: Signature validation is implemented. Test with real keys before going live.
- **Twilio SMS**: Mock mode until credentials provided.
- **PostgreSQL**: Set `DATABASE_URL` — SQLite is dev-only.
- **Device lock**: Placeholder stubs in `integrations/device_lock_provider.py`. No live provider connected.
- **Emajinet ID**: Abstraction ready in `integrations/emajinet_id.py`. API keys not yet connected.
