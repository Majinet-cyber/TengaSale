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
from django.utils.safestring import mark_safe

from core.formatting import format_mwk as _format_mwk
from core.formatting import format_mwk_plain as _format_mwk_plain
from core.formatting import format_mwk_signed as _format_mwk_signed

register = template.Library()


def _to_decimal(value):
    """Safely convert value to Decimal."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


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


@register.filter(name="mwk")
def format_mwk(value, arg=None):
    """Format a number as MWK currency with commas."""
    decimals = str(arg or "").lower() in {"1", "true", "yes", "decimals"}
    if value is None or value == "":
        return "Not provided"
    d = _to_decimal(value)
    if d is None:
        return "Not provided"
    if d == 0:
        return "MWK 0"
    return _format_mwk(d, decimals=decimals) or "Not provided"

@register.filter(name="mwk_plain")
def format_mwk_plain(value, arg=None):
    """Comma-formatted amount without MWK prefix."""
    decimals = str(arg or "").lower() in {"1", "true", "yes", "decimals"}
    d = _to_decimal(value)
    if d is None:
        return "Not provided"
    if d == 0:
        return "0"
    return _format_mwk_plain(d, decimals=decimals) or "Not provided"

@register.filter(name="mwk_if")
def format_mwk_if(value, pending_label="Pending setup"):
    """Format MWK when value is set and > 0; otherwise show pending label."""
    if value is None or value == "":
        return pending_label
    d = _to_decimal(value)
    if d is None or d <= 0:
        return pending_label
    return _format_mwk(d) or pending_label

@register.filter(name="daily_mwk")
def format_daily_mwk(value):
    """Daily repayment display: MWK 2,566 / day."""
    d = _to_decimal(value)
    if d is None:
        return "Not provided"
    if d <= 0:
        return "Pending setup"
    return f"{_format_mwk(d)} / day"

@register.filter(name="monthly_mwk")
def format_monthly_mwk(value):
    """30-day repayment display: MWK 76,980 / 30 days."""
    d = _to_decimal(value)
    if d is None:
        return "Not provided"
    if d <= 0:
        return "Pending setup"
    return f"{_format_mwk(d)} / 30 days"

@register.filter(name="mwk_short")
def format_mwk_short(value):
    """Format large MWK amounts in short form."""
    d = _to_decimal(value)
    if d is None:
        return "Not provided"
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
    """Format a number with commas."""
    d = _to_decimal(value)
    if d is None:
        return "Not provided"
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
    d = _to_decimal(value)
    if d is None:
        return "Not provided"
    if d == 0:
        return "+MWK 0"
    return _format_mwk_signed(d) or "Not provided"

@register.filter(name="abs_mwk")
def format_abs_mwk(value):
    """Format absolute MWK value with commas."""
    d = _to_decimal(value)
    if d is None:
        return "Not provided"
    d = abs(d)
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
    "expired": "badge-cancelled",
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


# Shared restrained line-icon set for the portal premium components
# (hero/chip/notice/band/actionbar/tech-details/progress-steps). Implemented
# as a simple_tag rather than an {% include %} partial on purpose: an include
# compiles and renders a whole extra Template/Context per call, which stacks
# additional Python call-stack frames for every icon on the page. On pages
# that already render many nested templates (base -> blocks -> sections),
# rendering several icons via nested includes was enough to occasionally tip
# Django's test-client template-rendering instrumentation over Python's
# recursion limit. A simple_tag is a plain function call — no extra
# Template.render() nesting — so it renders identical markup at effectively
# zero stack cost. Usage: {% load ts_filters %}{% ts_icon "check" %}
ICONS = {
    "check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    "check-circle": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
    "clock": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    "alert": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    "x-circle": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    "shield": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/></svg>',
    "lock": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
    "unlock": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>',
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    "info": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    "refresh": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6v5h-5M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 9m2 6a7 7 0 0 0 12 2.5L20 15"/></svg>',
    "phone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92Z"/></svg>',
    "chevron-right": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>',
    "chevron-down": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>',
    "back": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>',
    "receipt": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 2h16v20l-3-2-3 2-3-2-3 2-3-2-1 2V2Z"/><line x1="8" y1="7" x2="16" y2="7"/><line x1="8" y1="11" x2="16" y2="11"/></svg>',
    "support": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z"/><path d="M12 16v-1a3 3 0 1 0-3-3"/><line x1="12" y1="19" x2="12.01" y2="19"/></svg>',
}


@register.simple_tag(name="ts_icon")
def ts_icon(icon_name):
    """Render a shared restrained line-icon by name. See ICONS above for the set."""
    return mark_safe(ICONS.get(icon_name, ""))
