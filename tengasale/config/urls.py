from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic import TemplateView
from earnings.views import payments_home
from website import views as website_views
from config.health import healthz, readyz
from config.media_views import serve_media
from applications.didit_views import didit_callback_done, didit_webhook
from payments import api_views as payment_api_views

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    path("", website_views.landing, name="public_home"),
    path("admin/", admin.site.urls),

    # New clean app routing
    path("sales/", include("sales.urls")),
    path("pay/", include("portal.urls")),

    path("", include("dashboard.urls")),
    path("accounts/", include("accounts.urls")),
    path("applications/", include("applications.urls")),
    path("kyc/didit/done/", didit_callback_done, name="didit_callback_done"),
    path("api/ussd/", payment_api_views.ussd_callback, name="ussd_callback"),
    path("api/webhooks/didit/", didit_webhook, name="didit_webhook"),
    path("api/payments/", include("payments.api_urls")),
    path("deals/", include("deals.urls")),
    path("earnings/", include("earnings.urls")),
    path("payments/", payments_home, name="payments_home"),
    path("hq/payments/", include("payments.urls", namespace="payments")),
    path("tengasale/underwriter/", include("approvals.urls")),
    path("approvals/", include("approvals.legacy_urls")),
    path("contracts/", include("contracts.urls")),
    path("", include("financing.urls")),
    path("api/tengasale/", include("financing.api_urls")),

    # Merchant Administrator portal
    path("merchant/", include("merchants.urls")),
    path("tengasale/merchant-admin/", include("merchant_admin.urls")),

    # Device Lock Integration Layer
    path("", include("device_lock.urls")),

    # Notifications
    path("notifications/", include("notifications.urls")),

    # Private recommerce operations (authenticated and role checked).
    path("tengasale/recommerce/", include("recommerce.urls")),

    # Tech Support portal
    path("tengasale/support/", include("support.urls")),

    # Public website secondary URLs.
    path("site/", include("website.urls")),

    # PWA offline fallback
    path("offline/", TemplateView.as_view(template_name="offline.html"), name="offline"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # Production: authenticated media (KYC, signatures, contract PDFs on disk)
    urlpatterns += [
        re_path(r"^media/(?P<path>.+)$", serve_media, name="serve_media"),
    ]
