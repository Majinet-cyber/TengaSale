"""
IMEI verification service for TengaSale.

Verifies a submitted IMEI against the merchant's selected smartphone deal to
detect device substitution fraud (e.g. selecting a Tecno Spark 20 Pro but
providing the IMEI of a cheap Tecno Spark 10).
"""

import logging
import re
from typing import Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_TIMEOUT = 86400  # 24 hours

# ── Brand normalization ───────────────────────────────────────────────────────
# (regex pattern, canonical lowercase name)
_BRAND_ALIAS_MAP = [
    # Tecno variants including long corporate name
    (r"tecno\s*telecom(?:\s*\([^)]*\))?", "tecno"),
    (r"tecno\s*mobile", "tecno"),
    (r"tecno", "tecno"),
    # Itel
    (r"itel", "itel"),
    # Infinix
    (r"infinix", "infinix"),
    # Samsung
    (r"samsung\s*galaxy", "samsung"),
    (r"samsung", "samsung"),
    # Apple — "iPhone" alone implies Apple
    (r"apple|iphone", "apple"),
    # Xiaomi / Redmi / POCO group
    (r"xiaomi", "xiaomi"),
    (r"redmi", "xiaomi"),
    (r"poco", "xiaomi"),
    # Nokia / HMD
    (r"nokia|hmd\s*global", "nokia"),
    # Others
    (r"oppo", "oppo"),
    (r"vivo", "vivo"),
    (r"realme", "realme"),
    (r"honor", "honor"),
    (r"huawei", "huawei"),
]

# Brands that are interchangeable / same family
_BRAND_COMPATIBLE_GROUPS = {
    "xiaomi": {"xiaomi", "redmi", "poco"},
    "apple": {"apple"},
    "nokia": {"nokia", "hmd"},
}


# ── Normalisation helpers ────────────────────────────────────────────────────

def normalize_brand(text: str) -> str:
    """
    Map any brand string (incl. aliases and corporate suffixes)
    to a canonical lowercase name.
    """
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for pattern, canonical in _BRAND_ALIAS_MAP:
        if re.search(pattern, text, re.IGNORECASE):
            return canonical
    return text


def normalize_model(text: str) -> str:
    """
    Strip parenthetical suffixes, lowercase, collapse whitespace.
    E.g. "iPhone 15 Pro (A3104)" → "iphone 15 pro"
    """
    if not text:
        return ""
    text = re.sub(r"\([^)]*\)", "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> set:
    """Return meaningful word tokens (length > 1)."""
    return {t for t in text.split() if len(t) > 1}


# ── Extraction helpers ───────────────────────────────────────────────────────

def extract_brand_model_from_api_result(raw_result: str, object_data: dict) -> Tuple[str, str]:
    """
    Parse (brand_canonical, model_raw) from an API response.

    Handles both formats:
      - object dict: {"model": "iPhone 15 Pro (A3104)", ...}
      - plain text:  "Brand: Tecno Telecom (HK) Limited\\nModel: TECNO SPARK 50\\n..."
    """
    brand = ""
    model = ""

    # 1. Object dict is the most reliable source
    if object_data and isinstance(object_data, dict):
        model_from_obj = (
            object_data.get("model")
            or object_data.get("deviceName")
            or object_data.get("ModelName")
            or ""
        )
        if model_from_obj:
            model = str(model_from_obj).strip()
            # Apple devices report as "iPhone …" with no separate brand key
            if re.match(r"^iphone", model, re.IGNORECASE):
                brand = "apple"
            else:
                for pattern, canonical in _BRAND_ALIAS_MAP:
                    if re.search(pattern, model, re.IGNORECASE):
                        brand = canonical
                        break

        # Some APIs expose brandName separately
        if not brand:
            brand_from_obj = object_data.get("brand") or object_data.get("Brand") or ""
            if brand_from_obj:
                brand = normalize_brand(str(brand_from_obj))

    # 2. Fall back to plain-text parsing
    if raw_result:
        if not brand:
            m = re.search(r"^Brand[:\s]+(.+)$", raw_result, re.MULTILINE | re.IGNORECASE)
            if m:
                brand = normalize_brand(m.group(1).strip())

        if not model:
            m = re.search(r"^Model[:\s]+(.+)$", raw_result, re.MULTILINE | re.IGNORECASE)
            if m:
                model = m.group(1).strip()
            else:
                # Some results embed brand/model on the first line
                first_line = raw_result.split("\n")[0].strip()
                if first_line and not first_line.lower().startswith("imei"):
                    model = first_line

    # 3. If we still have no brand, try to infer from model string
    if not brand and model:
        for pattern, canonical in _BRAND_ALIAS_MAP:
            if re.search(pattern, model, re.IGNORECASE):
                brand = canonical
                break

    return brand, model


def extract_selected_brand_model(deal) -> Tuple[str, str]:
    """
    Extract (brand_canonical, model_normalised) from a DeviceDeal instance.

    Handles messy deal names like "Samsung Galaxy A05" or "Redmi 13C".
    """
    if not deal:
        return ("", "")

    raw_brand = getattr(deal, "brand", None)
    brand_name = ""
    if raw_brand:
        brand_name = getattr(raw_brand, "name", str(raw_brand))

    model_name = getattr(deal, "model_name", "") or ""
    selected_brand = normalize_brand(brand_name)
    selected_model = normalize_model(model_name)
    return selected_brand, selected_model


# ── Matching / scoring ───────────────────────────────────────────────────────

def score_device_match(
    selected_brand: str,
    selected_model: str,
    api_brand: str,
    api_model: str,
    raw_result: str = "",
) -> Tuple[int, list, str]:
    """
    Score how well the API result matches the selected device.

    Returns (confidence 0–100, reasons list, match_status).

    match_status ∈ {"match", "possible_match", "mismatch", "unknown"}
    Thresholds: ≥85 → match, 60–84 → possible_match, <60 → mismatch.
    A confirmed brand conflict always returns "mismatch" regardless of score.
    """
    confidence = 0
    reasons: list = []

    sel_brand_norm = normalize_brand(selected_brand)
    api_brand_norm = normalize_brand(api_brand) if api_brand else ""
    sel_model_norm = normalize_model(selected_model)
    api_model_norm = normalize_model(api_model) if api_model else ""

    # Guard: no usable API data at all
    if not api_brand_norm and not api_model_norm:
        if raw_result:
            reasons.append("API returned limited text data only — cannot score reliably")
            return 30, reasons, "unknown"
        reasons.append("API returned no brand or model data")
        return 0, reasons, "unknown"

    # ── Brand comparison (worth 40 points) ──────────────────────────────────
    brand_conflict = False
    if sel_brand_norm and api_brand_norm:
        if sel_brand_norm == api_brand_norm:
            confidence += 40
            reasons.append(f"Brand confirmed: {api_brand_norm}")
        else:
            # Check compatible brand groups
            matched_group = False
            for _group, members in _BRAND_COMPATIBLE_GROUPS.items():
                if sel_brand_norm in members and api_brand_norm in members:
                    confidence += 30
                    matched_group = True
                    reasons.append(
                        f"Compatible brand group: selected '{sel_brand_norm}', API '{api_brand_norm}'"
                    )
                    break
            if not matched_group:
                brand_conflict = True
                reasons.append(
                    f"Brand conflict: selected '{sel_brand_norm}', "
                    f"API returned '{api_brand_norm}'"
                )
                # Immediate mismatch — model overlap is irrelevant
                return 0, reasons, "mismatch"
    elif not api_brand_norm:
        reasons.append("API did not return a brand name")
        confidence += 5  # minor credit for partial response
    elif not sel_brand_norm:
        reasons.append("Selected deal has no recognisable brand")

    # ── Model comparison (worth 50 points) ──────────────────────────────────
    if not api_model_norm:
        reasons.append("API did not return a model name")
        # Brand matched but model unknown → cannot confirm; treat as unknown
        return max(0, min(100, confidence)), reasons, "unknown"

    # Strip brand prefix from API model to avoid penalising "TECNO SPARK 50" vs "Spark 50"
    api_model_clean = api_model_norm
    if api_brand_norm and api_model_clean.startswith(api_brand_norm):
        api_model_clean = api_model_clean[len(api_brand_norm):].strip()

    if sel_model_norm and api_model_clean:
        if sel_model_norm == api_model_clean:
            confidence += 50
            reasons.append(f"Model exact match: {api_model_clean}")
        else:
            sel_tokens = _tokenize(sel_model_norm)
            api_tokens = _tokenize(api_model_clean)

            if sel_tokens and api_tokens:
                common = sel_tokens & api_tokens
                # Coverage: how much of the selected model is covered by API (primary)
                coverage = len(common) / len(sel_tokens) if sel_tokens else 0
                # Precision: how much of API model matched (secondary)
                precision = len(common) / len(api_tokens) if api_tokens else 0
                # Weighted: bias toward coverage so short selected models are not penalised
                combined = coverage * 0.72 + precision * 0.28
                token_score = int(combined * 45)
                confidence += token_score

                pct_coverage = int(coverage * 100)
                if coverage >= 0.9:
                    reasons.append(f"Very close model match ({pct_coverage}% coverage): '{api_model_clean}'")
                elif coverage >= 0.6:
                    reasons.append(
                        f"Partial model match ({pct_coverage}% coverage): "
                        f"selected '{sel_model_norm}', API '{api_model_clean}'"
                    )
                elif coverage > 0:
                    reasons.append(
                        f"Weak model overlap ({pct_coverage}%): "
                        f"selected '{sel_model_norm}', API '{api_model_clean}'"
                    )
                else:
                    reasons.append(
                        f"Model mismatch: selected '{sel_model_norm}', "
                        f"API returned '{api_model_clean}'"
                    )
            else:
                reasons.append("Unable to tokenise model names for comparison")
    elif not sel_model_norm:
        reasons.append("Selected deal has no model name")
        if confidence >= 40:
            confidence = min(confidence, 65)

    confidence = max(0, min(100, confidence))

    if confidence >= 85:
        match_status = "match"
    elif confidence >= 60:
        match_status = "possible_match"
    else:
        match_status = "mismatch"

    return confidence, reasons, match_status


# ── Main verification entry point ────────────────────────────────────────────

def verify_imei_against_selected_device(
    imei: str,
    selected_device,
    user=None,
    force_recheck: bool = False,
) -> dict:
    """
    Verify an IMEI against the merchant's selected smartphone deal.

    Returns::

        {
            "success": bool,
            "imei": str,
            "api_brand": str,
            "api_model": str,
            "api_raw_result": str,
            "selected_brand": str,
            "selected_model": str,
            "match_status": "match"|"possible_match"|"mismatch"|"unknown"|"api_error"|"pending",
            "confidence": int (0–100),
            "reasons": list[str],
            "should_block": bool,
            "order_id": str,
            "raw_response": dict,
        }
    """
    from applications.services.imei_client import ImeiCheckClient

    sel_brand, sel_model = extract_selected_brand_model(selected_device)

    base = {
        "success": False,
        "imei": imei,
        "api_brand": "",
        "api_model": "",
        "api_raw_result": "",
        "selected_brand": sel_brand,
        "selected_model": sel_model,
        "match_status": "unknown",
        "confidence": 0,
        "reasons": [],
        "should_block": False,
        "order_id": "",
        "raw_response": {},
    }

    if not getattr(settings, "IMEI_CHECK_ENABLED", True):
        return {**base, "match_status": "api_error", "reasons": ["IMEI verification disabled in settings"]}

    if not imei or len(imei.strip()) != 15 or not imei.strip().isdigit():
        return {**base, "match_status": "api_error", "reasons": ["IMEI must be exactly 15 digits"]}

    imei = imei.strip()

    # ── Cache check ──────────────────────────────────────────────────────────
    cache_key = f"imei_check:{imei}"
    if not force_recheck:
        cached = cache.get(cache_key)
        if cached:
            logger.debug("IMEI cache hit: %s", imei)
            api_brand = cached.get("api_brand", "")
            api_model = cached.get("api_model", "")
            api_raw = cached.get("api_raw_result", "")
            confidence, reasons, match_status = score_device_match(
                sel_brand, sel_model, api_brand, api_model, api_raw
            )
            strict = getattr(settings, "IMEI_CHECK_STRICT_MODE", True)
            return {
                **base,
                "success": True,
                "api_brand": api_brand,
                "api_model": api_model,
                "api_raw_result": api_raw,
                "match_status": match_status,
                "confidence": confidence,
                "reasons": reasons + ["Result from cache"],
                "should_block": match_status == "mismatch" and strict,
                "order_id": cached.get("order_id", ""),
                "raw_response": cached.get("raw_response", {}),
            }

    # ── Live API call ────────────────────────────────────────────────────────
    try:
        client = ImeiCheckClient()
        raw = client.check_imei(imei)
    except Exception as exc:
        logger.exception("IMEI API client error for IMEI %s", imei)
        return {**base, "match_status": "api_error", "reasons": [f"API client error: {exc}"]}

    if not raw.get("success") and raw.get("error") and not raw.get("raw_result") and not raw.get("object"):
        return {
            **base,
            "match_status": "api_error",
            "reasons": [f"API error: {raw.get('error', 'Unknown')}"],
            "raw_response": raw,
        }

    # ── Extract brand/model ──────────────────────────────────────────────────
    api_brand, api_model = extract_brand_model_from_api_result(
        raw.get("raw_result", ""),
        raw.get("object", {}),
    )

    # Persist API data in cache so future calls don't cost credits
    cache_data = {
        "api_brand": api_brand,
        "api_model": api_model,
        "api_raw_result": raw.get("raw_result", ""),
        "order_id": raw.get("order_id", ""),
        "raw_response": raw,
    }
    cache.set(cache_key, cache_data, CACHE_TIMEOUT)

    # ── Score ────────────────────────────────────────────────────────────────
    confidence, reasons, match_status = score_device_match(
        sel_brand, sel_model, api_brand, api_model, raw.get("raw_result", "")
    )

    strict = getattr(settings, "IMEI_CHECK_STRICT_MODE", True)
    should_block = match_status == "mismatch" and strict

    return {
        **base,
        "success": True,
        "api_brand": api_brand,
        "api_model": api_model,
        "api_raw_result": raw.get("raw_result", ""),
        "match_status": match_status,
        "confidence": confidence,
        "reasons": reasons,
        "should_block": should_block,
        "order_id": raw.get("order_id", ""),
        "raw_response": raw,
    }


def save_verification_result(application, result: dict, user=None) -> None:
    """
    Persist a verification result to the FinancingApplication model fields
    and write an audit log entry.
    """
    from django.utils import timezone as tz

    match_status = result.get("match_status", "unknown")

    # Map internal match_status → DB choices
    status_map = {
        "match": "matched",
        "possible_match": "possible_match",
        "mismatch": "mismatch",
        "unknown": "unknown",
        "api_error": "api_error",
        "pending": "pending",
    }
    db_status = status_map.get(match_status, "unknown")

    update_fields = [
        "imei_verified",
        "imei_verification_status",
        "imei_api_brand",
        "imei_api_model",
        "imei_match_confidence",
        "imei_verification_reason",
        "imei_verified_at",
        "imei_order_id",
        "imei_raw_response",
    ]

    application.imei_verified = match_status == "match"
    application.imei_verification_status = db_status
    application.imei_api_brand = result.get("api_brand", "")[:120]
    application.imei_api_model = result.get("api_model", "")[:180]
    application.imei_match_confidence = result.get("confidence", 0)
    application.imei_verification_reason = "\n".join(result.get("reasons", []))
    application.imei_verified_at = tz.now()
    application.imei_order_id = result.get("order_id", "")[:80]
    application.imei_raw_response = result.get("raw_response") or {}

    application.save(update_fields=update_fields)

    # Audit log
    try:
        from core.models import AuditLog
        AuditLog.objects.create(
            user=user,
            action="imei_verification",
            object_type="FinancingApplication",
            object_id=str(application.pk),
            detail={
                "application": application.application_number,
                "merchant": str(getattr(application.created_by, "username", "")),
                "imei": result.get("imei", ""),
                "selected_brand": result.get("selected_brand", ""),
                "selected_model": result.get("selected_model", ""),
                "api_brand": result.get("api_brand", ""),
                "api_model": result.get("api_model", ""),
                "match_status": match_status,
                "confidence": result.get("confidence", 0),
                "reasons": result.get("reasons", []),
                "order_id": result.get("order_id", ""),
            },
        )
    except Exception:
        logger.exception("Failed to write IMEI verification audit log for %s", application.pk)
