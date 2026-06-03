"""Shared MWK currency formatting for Python code and template filters."""

from decimal import Decimal, InvalidOperation


def _to_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def format_mwk(value, decimals=False):
    """
    Format a number as MWK currency with commas.

    Whole values: MWK 1,000
    With decimals: MWK 16,234.40
    """
    d = _to_decimal(value)
    if d is None:
        return ""
    if d <= 0 and value in (None, "", 0, "0", "0.00"):
        return ""
    if not decimals and d == d.to_integral_value():
        return f"MWK {int(d):,}"
    return f"MWK {d:,.2f}"


def format_mwk_plain(value, decimals=False):
    """Comma-formatted amount without the MWK prefix."""
    d = _to_decimal(value)
    if d is None:
        return ""
    if not decimals and d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{d:,.2f}"


def format_mwk_signed(value, decimals=False):
    """Signed MWK display: +MWK 2,500 / -MWK 4,059"""
    d = _to_decimal(value)
    if d is None:
        return ""
    prefix = "+" if d >= 0 else "-"
    abs_d = abs(d)
    if not decimals and abs_d == abs_d.to_integral_value():
        return f"{prefix}MWK {int(abs_d):,}"
    return f"{prefix}MWK {abs_d:,.2f}"
