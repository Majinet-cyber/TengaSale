from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from earnings.views import payments_home
from website import views as website_views

urlpatterns = [
    path("", website_views.landing, name="public_home"),
    path("admin/", admin.site.urls),

    # New clean app routing
    path("sales/", include("sales.urls")),
    path("pay/", include("portal.urls")),

    path("", include("dashboard.urls")),
    path("accounts/", include("accounts.urls")),
    path("applications/", include("applications.urls")),
    path("deals/", include("deals.urls")),
    path("earnings/", include("earnings.urls")),
    path("payments/", payments_home, name="payments_home"),
    path("tengasale/underwriter/", include("approvals.urls")),
    path("approvals/", include("approvals.legacy_urls")),
    path("contracts/", include("contracts.urls")),
    path("", include("financing.urls")),
    path("api/tengasale/", include("financing.api_urls")),

    # Merchant Administrator portal
    path("tengasale/merchant-admin/", include("merchant_admin.urls")),

    # Device Lock Integration Layer
    path("", include("device_lock.urls")),

    # Notifications
    path("notifications/", include("notifications.urls")),

    # Tech Support portal
    path("tengasale/support/", include("support.urls")),

    # Public website secondary URLs.
    path("site/", include("website.urls")),

    # PWA offline fallback
    path("offline/", TemplateView.as_view(template_name="offline.html"), name="offline"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
