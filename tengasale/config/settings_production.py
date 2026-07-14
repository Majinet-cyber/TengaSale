"""
config/settings_production.py

Production settings for TengaSale.
Import from base settings and override security-critical values.

Usage:
    Set environment variable:  DJANGO_SETTINGS_MODULE=config.settings_production
    Or in wsgi.py / manage.py:  os.environ.setdefault(...)

IMPORTANT:
- Never commit real secrets to version control.
- All sensitive values must come from environment variables.
- Set DEBUG=False and SECRET_KEY from env before deploying.
"""

from .settings import *  # noqa: F401, F403
import os

# ──────────────────────────────────────────────────────────────────────────────
# Core security
# ──────────────────────────────────────────────────────────────────────────────

DEBUG = False

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]  # Must be set — no default

def _csv_env(name, default):
    return [value.strip() for value in os.environ.get(name, default).split(",") if value.strip()]


ALLOWED_HOSTS = _csv_env("ALLOWED_HOSTS", "tengasale-api.onrender.com,tengasale.emajinet.africa,.onrender.com")
CSRF_TRUSTED_ORIGINS = _csv_env(
    "CSRF_TRUSTED_ORIGINS",
    "https://tengasale-api.onrender.com,https://tengasale.emajinet.africa,https://*.onrender.com",
)

# ──────────────────────────────────────────────────────────────────────────────
# HTTPS enforcement
# ──────────────────────────────────────────────────────────────────────────────

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# ──────────────────────────────────────────────────────────────────────────────
# Database (PostgreSQL-ready)
# ──────────────────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL:
    import dj_database_url  # pip install dj-database-url psycopg2-binary
    DATABASES = {"default": dj_database_url.config(default=DATABASE_URL, conn_max_age=600)}
# else falls back to SQLite from base settings (dev only — override in production)

# ──────────────────────────────────────────────────────────────────────────────
# Static and media files
# ──────────────────────────────────────────────────────────────────────────────

STATIC_ROOT = os.environ.get("STATIC_ROOT", str(BASE_DIR / "staticfiles"))  # noqa: F405
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media"))  # noqa: F405

# ──────────────────────────────────────────────────────────────────────────────
# Email (SendGrid in production)
# ──────────────────────────────────────────────────────────────────────────────

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
if SENDGRID_API_KEY:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.sendgrid.net"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = "apikey"
    EMAIL_HOST_PASSWORD = SENDGRID_API_KEY
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@tengasale.com")
ADMIN_ALERT_EMAIL = os.environ.get("ADMIN_ALERT_EMAIL", "admin@tengasale.com")

# ──────────────────────────────────────────────────────────────────────────────
# Payment providers
# ──────────────────────────────────────────────────────────────────────────────

MOCK_PAYMENTS = os.environ.get("MOCK_PAYMENTS", "false").lower() == "true"
PAYCHANGU_PUBLIC_KEY = os.environ.get("PAYCHANGU_PUBLIC_KEY", "")
PAYCHANGU_SECRET_KEY = os.environ.get("PAYCHANGU_SECRET_KEY", "")
PAYCHANGU_WEBHOOK_SECRET = os.environ.get("PAYCHANGU_WEBHOOK_SECRET", "")
PAYCHANGU_API_BASE = os.environ.get("PAYCHANGU_API_BASE", "https://api.paychangu.com")
PAYCHANGU_CALLBACK_URL = os.environ.get("PAYCHANGU_CALLBACK_URL", "")

# ──────────────────────────────────────────────────────────────────────────────
# SMS / Twilio
# ──────────────────────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")
MOCK_SMS = os.environ.get("MOCK_SMS", "false").lower() == "true"

# ── Didit KYC ───────────────────────────────────────────────────────────────────
DIDIT_API_KEY = os.environ.get("DIDIT_API_KEY", "")
DIDIT_WORKFLOW_ID = os.environ.get("DIDIT_WORKFLOW_ID", "")
DIDIT_WEBHOOK_SECRET = os.environ.get("DIDIT_WEBHOOK_SECRET", "")
DIDIT_WEBHOOK_URL = os.environ.get("DIDIT_WEBHOOK_URL", "")
DIDIT_CALLBACK_URL = os.environ.get("DIDIT_CALLBACK_URL", "")
_DIDIT_ALLOW_UNSIGNED_WEBHOOKS_ENV = os.environ.get("DIDIT_ALLOW_UNSIGNED_WEBHOOKS", "False").lower() in (
    "true",
    "1",
    "yes",
)
DIDIT_ALLOW_UNSIGNED_WEBHOOKS = DEBUG and _DIDIT_ALLOW_UNSIGNED_WEBHOOKS_ENV
REQUIRE_DIDIT_KYC_BEFORE_APPROVAL = os.environ.get("REQUIRE_DIDIT_KYC_BEFORE_APPROVAL", "False").lower() in (
    "true",
    "1",
    "yes",
)
DIDIT_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("DIDIT_REQUEST_TIMEOUT_SECONDS", "15"))
DIDIT_SEND_EXPECTED_DETAILS = os.environ.get("DIDIT_SEND_EXPECTED_DETAILS", "False").lower() in (
    "true",
    "1",
    "yes",
)

from config.didit_validation import validate_didit_production_settings  # noqa: E402

validate_didit_production_settings(
    debug=DEBUG,
    api_key=DIDIT_API_KEY,
    workflow_id=DIDIT_WORKFLOW_ID,
    webhook_secret=DIDIT_WEBHOOK_SECRET,
    webhook_url=DIDIT_WEBHOOK_URL,
    callback_url=DIDIT_CALLBACK_URL,
    allow_unsigned_webhooks=DIDIT_ALLOW_UNSIGNED_WEBHOOKS,
    strict_mode=REQUIRE_DIDIT_KYC_BEFORE_APPROVAL,
)

# ──────────────────────────────────────────────────────────────────────────────
# Logging (safe — no secrets, no full IDs)
# ──────────────────────────────────────────────────────────────────────────────

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING"},
        "integrations": {"handlers": ["console"], "level": "INFO"},
        "risk": {"handlers": ["console"], "level": "INFO"},
        "commissions": {"handlers": ["console"], "level": "INFO"},
        "portal": {"handlers": ["console"], "level": "INFO"},
        "tengasale.applications": {"handlers": ["console"], "level": "INFO"},
        "integrations.didit": {"handlers": ["console"], "level": "INFO"},
        "applications.didit_handlers": {"handlers": ["console"], "level": "INFO"},
        "applications.didit_views": {"handlers": ["console"], "level": "INFO"},
        "tengasale.contracts": {"handlers": ["console"], "level": "INFO"},
        "tengasale.contracts.pdf": {"handlers": ["console"], "level": "INFO"},
        "tengasale.media": {"handlers": ["console"], "level": "INFO"},
        "tengasale.views": {"handlers": ["console"], "level": "INFO"},
    },
}
