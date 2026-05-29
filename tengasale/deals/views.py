from django.shortcuts import render
from accounts.decorators import merchant_required
from .models import DeviceDeal


@merchant_required
def all_deals(request):
    deals = DeviceDeal.objects.filter(is_active=True, brand__is_active=True).select_related("brand").order_by(
        "brand__name",
        "model_name",
        "specs",
    )
    deal_options = [
        {
            "id": deal.id,
            "brand": deal.brand.name,
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
    preferred_order = ["Tecno", "Itel", "Redmi/Xiaomi", "Samsung"]
    deal_brand_names = list(dict.fromkeys(deal.brand.name for deal in deals))
    brand_names = [name for name in preferred_order if name in deal_brand_names]
    if not brand_names and not deal_brand_names:
        brand_names = ["TECNO", "itel", "Redmi"]
    for deal in deals:
        if deal.brand.name not in brand_names:
            brand_names.append(deal.brand.name)

    return render(
        request,
        "deals/all_deals.html",
        {
            "deals": deals,
            "deal_options": deal_options,
            "brand_names": brand_names,
        },
    )
