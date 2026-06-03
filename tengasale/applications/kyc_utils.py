"""KYC image processing helpers."""

from __future__ import annotations

import io
import logging

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

KYC_ID_FIELDS = frozenset({"id_front_image", "id_back_image"})
KYC_SELFIE_FIELDS = frozenset({"customer_face_image"})


def process_kyc_upload(uploaded_file, field_name: str):
    """
    Apply EXIF orientation and return a Django-friendly file.
    Preserves aspect ratio; ID images saved as JPEG quality 92.
    """
    if not uploaded_file:
        return uploaded_file

    try:
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
    except Exception:
        logger.exception("KYC EXIF transpose failed for field=%s", field_name)
        uploaded_file.seek(0)
        return uploaded_file

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    buffer = io.BytesIO()
    if field_name in KYC_ID_FIELDS:
        image.save(buffer, format="JPEG", quality=92, optimize=True)
        ext = "jpg"
    else:
        image.save(buffer, format="JPEG", quality=88, optimize=True)
        ext = "jpg"

    buffer.seek(0)
    base_name = getattr(uploaded_file, "name", "upload") or "upload"
    if "." in base_name:
        base_name = base_name.rsplit(".", 1)[0]
    return ContentFile(buffer.read(), name=f"{base_name}.{ext}")
