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
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponseForbidden

logger = logging.getLogger("tengasale.media")


@login_required
def serve_media(request, path: str):
    """Stream a file from MEDIA_ROOT; path must stay under MEDIA_ROOT."""
    if not path or ".." in path.replace("\\", "/"):
        raise Http404("Invalid media path")

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
    return FileResponse(full_path.open("rb"), content_type=content_type)
