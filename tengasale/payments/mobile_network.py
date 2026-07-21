from __future__ import annotations

from dataclasses import dataclass
import re


PROVIDER_AIRTEL = "airtel_money"
PROVIDER_TNM = "tnm_mpamba"
PROVIDER_UNKNOWN = "unknown"


@dataclass(frozen=True)
class MalawiMsisdn:
    raw: str
    local: str = ""
    international: str = ""
    provider: str = PROVIDER_UNKNOWN
    valid: bool = False
    reason: str = ""

    @property
    def national(self) -> str:
        return self.local

    @property
    def display(self) -> str:
        return f"265{self.local}" if self.valid else ""

    @property
    def error_code(self) -> str:
        return self.reason

    @property
    def error_message(self) -> str:
        return {
            "invalid_characters": "Enter a valid Malawi mobile number.",
            "invalid_length": "The phone number is incomplete.",
            "unsupported_country": "Only Malawi mobile numbers are supported.",
            "repeated_country_code": "Enter a valid Malawi mobile number.",
            "unsupported_prefix": "Enter a valid Malawi mobile number.",
        }.get(self.reason, "")

    @property
    def provider_label(self) -> str:
        if self.provider == PROVIDER_AIRTEL:
            return "Airtel Money"
        if self.provider == PROVIDER_TNM:
            return "TNM Mpamba"
        return "Unsupported network"

    @property
    def masked(self) -> str:
        if not self.valid:
            return "***"
        return f"+265{self.local[:2]}***{self.local[-4:]}"


def normalize_malawi_msisdn(value: str) -> MalawiMsisdn:
    raw = str(value or "").strip()
    if not raw:
        return MalawiMsisdn(raw=raw, reason="invalid_length")
    if raw.count("+") > 1 or ("+" in raw and not raw.startswith("+")):
        return MalawiMsisdn(raw=raw, reason="invalid_characters")
    cleaned = re.sub(r"[\s()\-]", "", raw)
    if not re.fullmatch(r"\+?\d+", cleaned):
        return MalawiMsisdn(raw=raw, reason="invalid_characters")
    digits = cleaned.lstrip("+")
    if digits.startswith("265265"):
        return MalawiMsisdn(raw=raw, reason="repeated_country_code")
    if digits.startswith("265"):
        digits = digits[3:]
    elif len(digits) > 10:
        return MalawiMsisdn(raw=raw, reason="unsupported_country")
    if digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != 9:
        return MalawiMsisdn(raw=raw, reason="invalid_length")

    first = digits[0]
    if first == "9":
        provider = PROVIDER_AIRTEL
    elif first == "8":
        provider = PROVIDER_TNM
    else:
        return MalawiMsisdn(
            raw=raw,
            local=digits,
            international=f"+265{digits}",
            provider=PROVIDER_UNKNOWN,
            valid=False,
            reason="unsupported_prefix",
        )

    return MalawiMsisdn(
        raw=raw,
        local=digits,
        international=f"+265{digits}",
        provider=provider,
        valid=True,
        reason="",
    )


def detect_mobile_network(value: str) -> MalawiMsisdn:
    return normalize_malawi_msisdn(value)
