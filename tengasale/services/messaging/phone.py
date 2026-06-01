"""
services/messaging/phone.py

Malawi phone number normalization utilities.
"""

import re
import logging

logger = logging.getLogger("tengasale.messaging.phone")

# Malawi mobile prefixes
_MW_PREFIXES = {
    "099": "+265",
    "088": "+265",
    "098": "+265",
    "077": "+265",
    "031": "+265",
    "01": "+265",
}

_VALID_PATTERN = re.compile(r"^\+265[0-9]{9}$")


def normalize_malawi_phone(phone: str) -> str:
    """
    Normalize a Malawi phone number to E.164 format (+26599xxxxxxx).

    Accepted inputs:
      099xxxxxxx  → +26599xxxxxxx
      088xxxxxxx  → +26588xxxxxxx
      098xxxxxxx  → +26598xxxxxxx
      077xxxxxxx  → +26577xxxxxxx
      26599xxxxxxx → +26599xxxxxxx
      +26599xxxxxxx → unchanged

    Raises ValueError for invalid/unsupported numbers.
    """
    if not phone:
        raise ValueError("Phone number is empty")

    # Strip whitespace and common separators
    phone = re.sub(r"[\s\-\(\)\.]+", "", phone.strip())

    # Already in E.164 format
    if phone.startswith("+265") and len(phone) == 13:
        if _VALID_PATTERN.match(phone):
            return phone
        raise ValueError(f"Invalid Malawi E.164 number: {phone}")

    # 265xxxxxxxxx (without +)
    if phone.startswith("265") and len(phone) == 12:
        normalized = "+" + phone
        if _VALID_PATTERN.match(normalized):
            return normalized
        raise ValueError(f"Invalid Malawi number (265 prefix): {phone}")

    # Local format: 09xxxxxxxx, 08xxxxxxxx, 07xxxxxxxx (9 digits after removing leading 0)
    if phone.startswith("0") and len(phone) == 10:
        prefix3 = phone[:3]
        prefix2 = phone[:2]
        if prefix3 in _MW_PREFIXES:
            normalized = _MW_PREFIXES[prefix3] + phone[1:]
            if _VALID_PATTERN.match(normalized):
                return normalized
        if prefix2 in _MW_PREFIXES:
            normalized = _MW_PREFIXES[prefix2] + phone[1:]
            if _VALID_PATTERN.match(normalized):
                return normalized
        raise ValueError(f"Unrecognized Malawi prefix in: {phone}")

    # 9-digit format (no leading zero) — e.g. stored as 9 digits in the DB
    if re.match(r"^[0-9]{9}$", phone):
        # Try common prefixes
        prefix2 = phone[:2]
        normalized = "+265" + phone
        if _VALID_PATTERN.match(normalized):
            return normalized
        raise ValueError(f"Cannot normalize 9-digit number: {phone}")

    raise ValueError(f"Unrecognized phone number format: {phone}")


def try_normalize_malawi_phone(phone: str) -> tuple[str, str]:
    """
    Try to normalize a Malawi phone number.

    Returns (normalized_phone, error_message).
    If normalization succeeds, error_message is empty.
    If it fails, normalized_phone is empty.
    """
    try:
        return normalize_malawi_phone(phone), ""
    except ValueError as e:
        return "", str(e)
