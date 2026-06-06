import logging
from functools import wraps

from django.shortcuts import render


logger = logging.getLogger("tengasale.views")


def safe_page(section, template_name="base/safe_page_error.html"):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            try:
                return view_func(request, *args, **kwargs)
            except Exception as exc:
                logger.exception("%s failed for path %s", section, request.path)
                return render(
                    request,
                    template_name,
                    {
                        "section": section,
                        "error_message": (
                            "This area is temporarily unavailable. "
                            "Please refresh or try again shortly."
                        ),
                    },
                    status=200,
                )

        return wrapped

    return decorator
