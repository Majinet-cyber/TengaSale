"""Canonical device brand names for display and deduplication."""

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage

BRAND_ALIASES = {
    "redmi": "Redmi/Xiaomi",
    "xiaomi": "Redmi/Xiaomi",
    "redmi/xiaomi": "Redmi/Xiaomi",
    "redmi / xiaomi": "Redmi/Xiaomi",
    "redmi-xiaomi": "Redmi/Xiaomi",
    "redmi by xiaomi": "Redmi/Xiaomi",
    "xiaomi redmi": "Redmi/Xiaomi",
    "tecno": "Tecno",
    "tecno mobile": "Tecno",
    "itel": "Itel",
    "samsung": "Samsung",
    "samsung mobile": "Samsung",
    "infinix": "Infinix",
    "airtel": "Airtel",
    "airtel money": "Airtel",
}

VISIBLE_BRAND_NAMES = {"Tecno", "Itel", "Samsung", "Redmi/Xiaomi"}
PREFERRED_BRAND_ORDER = ["Tecno", "Itel", "Samsung", "Redmi/Xiaomi"]

BRAND_STATIC_LOGOS = {
    "Tecno": "img/brands/tecno.svg",
    "Itel": "img/brands/itel.svg",
    "Redmi/Xiaomi": "img/brands/redmi.svg",
    "Samsung": "img/brands/samsung.svg",
    "Airtel": "img/brands/airtel.svg",
}


def brand_logo_path(name: str) -> str:
    """Return one safe canonical static path, or an empty string for unknown brands."""
    return BRAND_STATIC_LOGOS.get(canonical_brand(name), "")


def get_brand_logo(name: str) -> str:
    """Resolve a known manufacturer/provider logo through Django staticfiles."""
    return brand_logo_url(brand_logo_path(name))


def canonical_brand(name: str) -> str:
    if not name:
        return name
    key = name.strip().lower()
    return BRAND_ALIASES.get(key, name.strip())


def ordered_brand_names(raw_names):
    """Unique canonical brand names in preferred display order."""
    canonical = [
        name for name in dict.fromkeys(canonical_brand(n) for n in raw_names if n)
        if name in VISIBLE_BRAND_NAMES
    ]
    ordered = [n for n in PREFERRED_BRAND_ORDER if n in canonical]
    for name in canonical:
        if name not in ordered:
            ordered.append(name)
    return ordered


def brand_logo_url(static_path: str) -> str:
    """Resolve a static brand logo path without crashing on missing manifest entries."""
    if not static_path:
        return ""
    try:
        return staticfiles_storage.url(static_path)
    except (ValueError, OSError):
        base = settings.STATIC_URL.rstrip("/")
        return f"{base}/{static_path.lstrip('/')}"
