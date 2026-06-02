from django.shortcuts import render
from accounts.decorators import merchant_required
from .brand_utils import BRAND_STATIC_LOGOS, PREFERRED_BRAND_ORDER, canonical_brand, ordered_brand_names
from .models import DeviceBrand, DeviceDeal

# Brand accent colours used in the UI
BRAND_ACCENTS = {
    "Tecno":        {"color": "#1d4ed8", "bg": "rgba(29,78,216,0.08)"},
    "Itel":         {"color": "#dc2626", "bg": "rgba(220,38,38,0.08)"},
    "Redmi/Xiaomi": {"color": "#ea580c", "bg": "rgba(234,88,12,0.08)"},
    "Samsung":      {"color": "#1d4ed8", "bg": "rgba(29,78,216,0.08)"},
    "Infinix":      {"color": "#16a34a", "bg": "rgba(22,163,74,0.08)"},
}


@merchant_required
def all_deals(request):
    deals = DeviceDeal.objects.filter(is_active=True, brand__is_active=True).select_related("brand").order_by(
        "brand__name",
        "model_name",
        "specs",
    )

    # Normalise brand name in each deal object for display (no DB write)
    for deal in deals:
        deal._canonical_brand = canonical_brand(deal.brand.name)

    deal_options = [
        {
            "id": deal.id,
            "brand": deal._canonical_brand,
            "brand_logo": deal.brand.logo.url if deal.brand.logo else "",
            "model_name": deal.model_name,
            "specs": deal.specs,
            "min_cash_price": str(deal.min_cash_price),
            "max_cash_price": str(deal.max_cash_price),
            "default_cash_price": str(deal.default_cash_price or deal.cash_price),
            "deposit_percent": str(deal.deposit_percent),
            "loan_multiplier": str(deal.loan_multiplier),
            "term_months": deal.term_months,
            "stock_status": deal.get_stock_status_display(),
            "is_lock_ready": deal.is_lock_ready,
            "condition": deal.get_condition_display(),
        }
        for deal in deals
    ]

    deal_brand_names = [deal._canonical_brand for deal in deals]
    brand_names = ordered_brand_names(deal_brand_names)

    # Build brand info dict: {canonical_name: {logo_url, deal_count, accent}}
    brand_info = {}
    for name in brand_names:
        brand_info[name] = {
            "logo_url": "",
            "deal_count": 0,
            **BRAND_ACCENTS.get(name, {"color": "#ff5a00", "bg": "rgba(255,90,0,0.08)"}),
        }

    # Attach logos from DB brands (match by canonical name)
    brand_objects = DeviceBrand.objects.filter(is_active=True)
    for b in brand_objects:
        canon = canonical_brand(b.name)
        if canon in brand_info and b.logo:
            brand_info[canon]["logo_url"] = b.logo.url

    # Count deals per canonical brand
    for deal in deals:
        cn = deal._canonical_brand
        if cn in brand_info:
            brand_info[cn]["deal_count"] += 1

    return render(
        request,
        "deals/all_deals.html",
        {
            "deals": deals,
            "deal_options": deal_options,
            "brand_names": brand_names,
            "brand_info": brand_info,
        },
    )
