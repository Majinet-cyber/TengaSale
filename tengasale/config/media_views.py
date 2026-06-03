"""
Serve uploaded media files in production (DEBUG=False).

Django's static() helper only mounts /media/ when DEBUG=True. This view serves
files from MEDIA_ROOT for authenticated users so KYC photos and signatures
render on review screens and application lists.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.http import FileResponse, Http404

from accounts.utils import (
    get_user_portal_role,
    is_hq,
    is_merchant,
    is_merchant_admin,
    is_underwriter,
)

logger = logging.getLogger("tengasale.media")

_KYC_IMAGE_FIELDS = (
    "customer_face_image",
    "id_front_image",
    "id_back_image",
    "customer_phone_image",
)
def _staff_can_view_all_media(user) -> bool:
    role = get_user_portal_role(user)
    return bool(
        user.is_superuser
        or is_hq(user)
        or is_underwriter(user)
        or is_merchant_admin(user)
        or role == "tech_support"
    )


def _merchant_owns_media_path(user, path: str) -> bool:
    if not is_merchant(user):
        return False
    from applications.models import FinancingApplication

    filters = Q()
    for field in _KYC_IMAGE_FIELDS + ("signature_image",):
        filters |= Q(**{field: path})
    if FinancingApplication.objects.filter(created_by=user).filter(filters).exists():
        return True

    if path.startswith("contract_signatures/"):
        from contracts.models import Contract

        return Contract.objects.filter(
            merchant=user,
            customer_contract_signature=path,
        ).exists()
    return False


def user_can_access_media(user, path: str) -> bool:
    if not user.is_authenticated:
        return False
    if _staff_can_view_all_media(user):
        return True
    return _merchant_owns_media_path(user, path)


def serve_media(request, path: str):
    """Stream a file from MEDIA_ROOT; path must stay under MEDIA_ROOT."""
    if not path or ".." in path.replace("\\", "/"):
        raise Http404("Invalid media path")

    if not user_can_access_media(request.user, path):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        logger.warning("Media access denied: %s (user=%s)", path, request.user.pk)
        raise Http404("Media file not found")

    media_root = Path(settings.MEDIA_ROOT).resolve()
    full_path = (media_root / path).resolve()
    try:
        full_path.relative_to(media_root)
    except ValueError:
        raise Http404("Invalid media path") from None

    if not full_path.is_file():
        logger.warning("Media file not found: %s (user=%s)", path, request.user.pk)
        raise Http404("Media file not found")

    content_type, _ = mimetypes.guess_type(str(full_path))
    if not content_type:
        content_type = "application/octet-stream"

    logger.debug("Serving media %s to user=%s", path, request.user.pk)
    response = FileResponse(full_path.open("rb"), content_type=content_type)
    response["Cache-Control"] = "private, max-age=3600"
    return response
