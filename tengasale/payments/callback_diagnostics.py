from __future__ import annotations

import ipaddress
from decimal import Decimal


def _valid_ip(value):
    try:
        return ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None


def extract_callback_source_ip(headers=None, remote_addr="") -> str | None:
    """Return the best diagnostic client IP; never use it as authentication."""
    normalized = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    for name in ("cf-connecting-ip", "true-client-ip", "x-real-ip"):
        address = _valid_ip(normalized.get(name))
        if address:
            return str(address)
    forwarded = normalized.get("x-forwarded-for", "")
    for value in forwarded.split(","):
        address = _valid_ip(value)
        if address and address.is_global:
            return str(address)
    address = _valid_ip(remote_addr)
    return str(address) if address else None


def candidate_suggestions(*, transactions, subscriber, amount, received_at, environment=""):
    """Rank investigation-only candidates. This function never mutates data."""
    suggestions = []
    for tx in transactions:
        reasons = []
        score = 0
        national = str(tx.customer_msisdn or "")[-9:]
        if subscriber and national == subscriber:
            score += 50
            reasons.append("same subscriber")
        difference = abs(Decimal(tx.amount) - Decimal(amount)) if amount is not None else None
        if difference == 0:
            score += 30
            reasons.append("same amount")
        elif difference is not None:
            reasons.append(f"amount differs by MWK {difference}")
            score += max(0, 20 - min(20, int(difference)))
        delta_minutes = abs((received_at - tx.created_at).total_seconds()) / 60 if received_at and tx.created_at else None
        if delta_minutes is not None:
            if delta_minutes <= 60: score += 15
            elif delta_minutes <= 1440: score += 5
            reasons.append(f"created {round(delta_minutes)} minutes from callback")
        if environment and tx.environment == environment:
            score += 5
            reasons.append("same environment")
        reasons.extend(["no merchant reference match", "no provider-ID match"])
        if score:
            suggestions.append({"internal_reference": tx.internal_reference, "score": score, "reasons": reasons, "status": tx.status, "purpose": tx.purpose, "contract_id": tx.contract_id})
    return sorted(suggestions, key=lambda item: (-item["score"], item["internal_reference"]))
