from django.conf import settings
from django.templatetags.static import static

from accounts.utils import get_tengasale_role


def tengasale_support(request):
    return {
        "tengasale_whatsapp_link": settings.TENGASALE_WHATSAPP_LINK,
        "current_tengasale_role": get_tengasale_role(request.user),
        "current_user_is_hq": get_tengasale_role(request.user) == "hq",
        "current_user_can_use_django_admin": request.user.is_authenticated
        and (request.user.is_staff or request.user.is_superuser),
        # Brand logo assets — always True since files are committed to static/
        "tengasale_logo_exists": True,
        # Full wordmark image — used in authenticated app topbars
        "tengasale_logo_full_url": static("images/TENGASALEe.png"),
        # Large TS brand/marketing image — public landing page brand showcase only
        "tengasale_brand_image_url": static("images/brand/tengasale-logo-icon.png"),
        # Small logo mark — compact logo for app headers, NOT the 13MB image
        "tengasale_logo_mark_url": static("images/Tenga.png"),
        # Small 40×40 compact app icon — used in sales/portal app headers
        "tengasale_app_icon_url": static("images/Tenga.png"),
    }
