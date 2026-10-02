import os
from pathlib import Path
from types import SimpleNamespace
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.template.loader import render_to_string
from django.test import RequestFactory
from website.forms import WebsiteEnquiryForm
from website.views import _demo_public_phone_offers
from django.contrib.auth.models import AnonymousUser
base = Path(__file__).parent
out = base / "ui-preview"
out.mkdir(exist_ok=True)
request = RequestFactory().get("/", HTTP_HOST="localhost")
request.user = AnonymousUser()
request.resolver_match = SimpleNamespace(url_name="public_home")
common = {"request": request, "tenga_motto": "Why wait.", "support_email": "support@tenga.africa", "tenga_support_email": "support@tenga.africa", "support_form": WebsiteEnquiryForm(), "public_phone_offers": _demo_public_phone_offers(), "public_phone_offers_are_demo": True}
(out / "landing.html").write_text(render_to_string("website/landing.html", common), encoding="utf-8")
request.user = SimpleNamespace(is_authenticated=True, first_name="", username="Preview", is_superuser=True)
request.resolver_match = SimpleNamespace(url_name="hq_dashboard")
common.update({"current_tengasale_role": "hq", "tengasale_whatsapp_link": "https://wa.me/265999000000", "staff_module_keys": [], "hq_payment_comparison": {"labels": [], "current": [], "previous": [], "trend": []}, "total_portfolio_value": 0, "financed_contracts_count": 0, "active_contracts_count": 0, "overdue_contracts_count": 0, "collections_today": 0})
(out / "hq.html").write_text(render_to_string("dashboard/hq.html", common), encoding="utf-8")
print("UI preview templates rendered", flush=True)
