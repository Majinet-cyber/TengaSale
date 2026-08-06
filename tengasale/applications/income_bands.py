"""Canonical monthly-income bands shared by server validation and UI guidance."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError


INCOME_BAND_RULES = {
    "less_than_100k": {"label": "Less than MWK 100,000", "min": None, "max": Decimal("100000"), "max_inclusive": False},
    "100k_150k": {"label": "MWK 100,000 – MWK 150,000", "min": Decimal("100000"), "max": Decimal("150000")},
    "150k_200k": {"label": "MWK 150,000 – MWK 200,000", "min": Decimal("150000"), "max": Decimal("200000")},
    "200k_250k": {"label": "MWK 200,000 – MWK 250,000", "min": Decimal("200000"), "max": Decimal("250000")},
    "250k_300k": {"label": "MWK 250,000 – MWK 300,000", "min": Decimal("250000"), "max": Decimal("300000")},
    "300k_350k": {"label": "MWK 300,000 – MWK 350,000", "min": Decimal("300000"), "max": Decimal("350000")},
    "350k_400k": {"label": "MWK 350,000 – MWK 400,000", "min": Decimal("350000"), "max": Decimal("400000")},
    "400k_600k": {"label": "MWK 400,000 – MWK 600,000", "min": Decimal("400000"), "max": Decimal("600000")},
    "more_than_600k": {"label": "More than MWK 600,000", "min": Decimal("600000"), "min_inclusive": False, "max": None},
}

INCOME_BANDS = [("", "Select income band")] + [(key, rule["label"]) for key, rule in INCOME_BAND_RULES.items()]


def validate_income_band_amount(band, amount):
    rule = INCOME_BAND_RULES.get(band)
    if not rule:
        raise ValidationError("Select a valid monthly income band.")
    try:
        value = Decimal(str(amount).replace(",", ""))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid exact monthly income.")
    if value <= 0:
        raise ValidationError("Exact monthly income must be greater than zero.")
    minimum, maximum = rule.get("min"), rule.get("max")
    below = minimum is not None and (value < minimum or (value == minimum and not rule.get("min_inclusive", True)))
    above = maximum is not None and (value > maximum or (value == maximum and not rule.get("max_inclusive", True)))
    if below:
        if band == "more_than_600k":
            raise ValidationError("You selected ‘More than MWK 600,000’. Enter an amount above MWK 600,000 or change the income band.")
        raise ValidationError("Exact monthly income must match the selected income band.")
    if above:
        raise ValidationError("The exact income is above the selected range. Change the amount or select the correct income band.")
    return value


def client_income_band_rules():
    return {key: {"label": rule["label"], "min": str(rule["min"]) if rule.get("min") is not None else None, "max": str(rule["max"]) if rule.get("max") is not None else None, "minInclusive": rule.get("min_inclusive", True), "maxInclusive": rule.get("max_inclusive", True)} for key, rule in INCOME_BAND_RULES.items()}
