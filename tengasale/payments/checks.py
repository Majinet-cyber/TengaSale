from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def airtel_configuration_checks(app_configs, **kwargs):
    issues = []
    environment = str(getattr(settings, "AIRTEL_ENVIRONMENT", "staging")).lower()
    production = environment == "production"
    enabled = bool(getattr(settings, "AIRTEL_COLLECTIONS_ENABLED", False))
    auth_enabled = bool(getattr(settings, "AIRTEL_CALLBACK_AUTH_ENABLED", False))
    secret = getattr(settings, "AIRTEL_CALLBACK_HASH_KEY", "") or getattr(settings, "AIRTEL_PRIVATE_KEY", "")
    callback_url = str(getattr(settings, "AIRTEL_CALLBACK_URL", "") or "")
    enquiry_path = str(getattr(settings, "AIRTEL_ENQUIRY_PATH_TEMPLATE", "") or "")
    if enabled and not (getattr(settings, "AIRTEL_AUTH_TOKEN", "") or (getattr(settings, "AIRTEL_CLIENT_ID", "") and getattr(settings, "AIRTEL_CLIENT_SECRET", ""))):
        issues.append(Warning("Airtel collections are enabled without API credentials.", id="payments.W001"))
    if production and getattr(settings, "AIRTEL_DRY_RUN", False):
        issues.append(Error("AIRTEL_DRY_RUN must be disabled in production.", id="payments.E001"))
    if production and not getattr(settings, "AIRTEL_PRODUCTION_ENABLED", False):
        issues.append(Error("Production Airtel mode requires explicit AIRTEL_PRODUCTION_ENABLED.", id="payments.E002"))
    if production and not auth_enabled:
        issues.append(Error("Airtel callback authentication cannot be disabled in production.", id="payments.E003"))
    elif not production and not auth_enabled:
        issues.append(Warning("Airtel callback authentication is explicitly disabled for UAT; callbacks are not cryptographically verified.", id="payments.W004"))
    if auth_enabled and not secret:
        check_type = Error if production else Warning
        issues.append(check_type("Airtel callback authentication is enabled without a signing secret.", id="payments.E004" if production else "payments.W003"))
    if callback_url:
        parsed = urlparse(callback_url)
        if not parsed.scheme or not parsed.netloc:
            issues.append(Error("AIRTEL_CALLBACK_URL is malformed.", id="payments.E005"))
        elif production and parsed.scheme != "https":
            issues.append(Error("Production Airtel callback URL must use HTTPS.", id="payments.E006"))
    if environment == "staging" and enabled and not getattr(settings, "AIRTEL_ALLOWED_TEST_MSISDNS", ""):
        issues.append(Warning("Airtel staging allowlist is empty.", id="payments.W002"))
    if "{reference}" not in enquiry_path:
        issues.append(Error("AIRTEL_ENQUIRY_PATH_TEMPLATE must contain {reference}.", id="payments.E007"))
    return issues
