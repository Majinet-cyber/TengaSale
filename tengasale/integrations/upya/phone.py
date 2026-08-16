import re


class InvalidMalawiMobile(ValueError):
    pass


def normalize_malawi_mobile(value: str) -> str:
    """Return the Upya subscriber format: country code plus national number, digits only."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "265" + digits[1:]
    if not re.fullmatch(r"265[89]\d{8}", digits):
        raise InvalidMalawiMobile("A valid Malawi mobile number is required.")
    return digits
