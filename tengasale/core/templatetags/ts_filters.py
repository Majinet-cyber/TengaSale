"""
TengaSale custom template filters for consistent number and currency formatting.

Usage in templates:
    {% load ts_filters %}
    {{ value|mwk }}          → MWK 1,250,000
    {{ value|mwk_plain }}    → 1,250,000
    {{ value|mwk_short }}    → MWK 1.25M
    {{ value|tsnum }}        → 1,250,000
    {{ value|pct }}          → 12.5%
    {{ value|pct0 }}         → 13%
"""

from django import template
from decimal import Decimal, InvalidOperation
from django.conf import settings

from core.formatting import format_mwk as _format_mwk
from core.formatting import format_mwk_plain as _format_mwk_plain
from core.formatting import format_mwk_signed as _format_mwk_signed

register = template.Library()


def _to_decimal(value):
    """Safely convert value to Decimal."""
    if value is None:
        return Decimal("0")


@register.filter(name="media_url")
def media_url(value):
    """Return a FieldFile URL without raising when the file is unset."""
    if not value:
        return ""
    try:
        return value.url
    except Exception:
        name = getattr(value, "name", "")
        if not name:
            return ""
        media_base = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
        return f"{media_base.rstrip('/')}/{str(name).lstrip('/')}"
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


@register.filter(name="mwk")
def format_mwk(value, arg=None):
    """Format a number as MWK currency with commas. E.g. 1250000 → MWK 1,250,000"""
    decimals = str(arg or "").lower() in {"1", "true", "yes", "decimals"}
    if value is None or value == "":
        return "—"
    d = _to_decimal(value)
    return _format_mwk(d, decimals=decimals) or "MWK 0"


@register.filter(name="mwk_plain")
def format_mwk_plain(value, arg=None):
    """Comma-formatted amount without MWK prefix."""
    decimals = str(arg or "").lower() in {"1", "true", "yes", "decimals"}
    return _format_mwk_plain(_to_decimal(value), decimals=decimals)


@register.filter(name="mwk_if")
def format_mwk_if(value, pending_label="Pending setup"):
    """Format MWK when value is set and > 0; otherwise show pending label."""
    if value is None:
        return pending_label
    d = _to_decimal(value)
    if d <= 0:
        return pending_label
    return _format_mwk(d) or pending_label


@register.filter(name="daily_mwk")
def format_daily_mwk(value):
    """Daily repayment display: MWK 2,566 / day"""
    d = _to_decimal(value)
    if d <= 0:
        return "Pending setup"
    return f"{_format_mwk(d)} / day"


@register.filter(name="monthly_mwk")
def format_monthly_mwk(value):
    """30-day repayment display: MWK 76,980 / 30 days"""
    d = _to_decimal(value)
    if d <= 0:
        return "Pending setup"
    return f"{_format_mwk(d)} / 30 days"


@register.filter(name="mwk_short")
def format_mwk_short(value):
    """Format large MWK amounts in short form. E.g. 1250000 → MWK 1.25M"""
    d = _to_decimal(value)
    abs_d = abs(float(d))
    neg = "-" if d < 0 else ""
    if abs_d >= 1_000_000_000:
        return f"{neg}MWK {abs_d / 1_000_000_000:.2f}B"
    if abs_d >= 1_000_000:
        return f"{neg}MWK {abs_d / 1_000_000:.2f}M"
    if abs_d >= 1_000:
        return f"{neg}MWK {abs_d / 1_000:.1f}K"
    return f"{neg}MWK {abs_d:,.0f}"


@register.filter(name="tsnum")
def format_number(value):
    """Format a number with commas. E.g. 1250000 → 1,250,000"""
    d = _to_decimal(value)
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{d:,.2f}"


@register.filter(name="pct")
def format_pct(value, places=1):
    """Format a number as a percentage with 1 decimal. E.g. 12.5 → 12.5%"""
    try:
        return f"{float(value):.1f}%"
    except (ValueError, TypeError):
        return "0.0%"


@register.filter(name="pct0")
def format_pct0(value):
    """Format a number as a percentage with no decimals. E.g. 12.5 → 13%"""
    try:
        return f"{round(float(value))}%"
    except (ValueError, TypeError):
        return "0%"


@register.filter(name="mwk_signed")
def format_mwk_signed(value):
    """Format MWK with + or - prefix for cashflow displays."""
    return _format_mwk_signed(_to_decimal(value)) or "MWK 0"


@register.filter(name="abs_mwk")
def format_abs_mwk(value):
    """Format absolute MWK value with commas."""
    d = abs(_to_decimal(value))
    if d == d.to_integral_value():
        return f"MWK {int(d):,}"
    return f"MWK {d:,.2f}"


@register.filter(name="get_item")
def get_item(dictionary, key):
    """Lookup a dict by dynamic key. Usage: {{ corrections|get_item:field_key }}"""
    if not dictionary:
        return None
    return dictionary.get(key)


@register.filter(name="sub")
def subtract(value, arg):
    """Subtract arg from value. Usage: {{ total|sub:paid }}"""
    try:
        return _to_decimal(value) - _to_decimal(arg)
    except (TypeError, ValueError):
        return value


PAYMENT_STATUS_BADGE_CLASSES = {
    "pending": "badge-pending",
    "tenga_processing": "badge-tenga-processing",
    "processing": "badge-tenga-processing",
    "external_processing": "badge-external-processing",
    "paid": "badge-paid",
    "failed": "badge-failed",
    "reversed": "badge-reversed",
    "cancelled": "badge-cancelled",
}

RISK_BADGE_CLASSES = {
    "low": "badge-risk-low",
    "watch": "badge-risk-watch",
    "medium": "badge-risk-medium",
    "high": "badge-risk-high",
}


@register.filter(name="payment_badge")
def payment_badge(value):
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return PAYMENT_STATUS_BADGE_CLASSES.get(key, "badge-pending")


@register.filter(name="risk_badge")
def risk_badge(value):
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return RISK_BADGE_CLASSES.get(key, "badge-risk-low")
