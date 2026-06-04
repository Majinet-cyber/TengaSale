"""Canonical device brand names for display and deduplication."""

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage

BRAND_ALIASES = {
    "redmi": "Redmi/Xiaomi",
    "xiaomi": "Redmi/Xiaomi",
    "redmi/xiaomi": "Redmi/Xiaomi",
    "redmi / xiaomi": "Redmi/Xiaomi",
    "redmi-xiaomi": "Redmi/Xiaomi",
    "tecno": "Tecno",
    "itel": "Itel",
    "samsung": "Samsung",
    "infinix": "Infinix",
}

PREFERRED_BRAND_ORDER = ["Tecno", "Itel", "Infinix", "Samsung", "Redmi/Xiaomi"]

BRAND_STATIC_LOGOS = {
    "Tecno": "img/brands/tecno.svg",
    "Itel": "img/brands/itel.svg",
    "Infinix": "img/brands/infinix.svg",
    "Redmi/Xiaomi": "img/brands/redmi.svg",
    "Samsung": "img/brands/samsung.svg",
}


def canonical_brand(name: str) -> str:
    if not name:
        return name
    key = name.strip().lower()
    return BRAND_ALIASES.get(key, name.strip())


def ordered_brand_names(raw_names):
    """Unique canonical brand names in preferred display order."""
    canonical = list(dict.fromkeys(canonical_brand(n) for n in raw_names if n))
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
