from __future__ import annotations

from dataclasses import dataclass


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
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("265"):
        digits = digits[3:]
    digits = digits.lstrip("0")

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
