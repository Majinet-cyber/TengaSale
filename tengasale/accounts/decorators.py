from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect, render

from .utils import (
    is_hq,
    is_merchant,
    is_merchant_admin,
    is_tech_support,
    is_underwriter,
    is_hq_or_merchant_admin,
    is_hq_or_tech_support,
    role_redirect_url,
)

# URLs that a merchant can access without a signed agreement
_AGREEMENT_EXEMPT_NAMES = frozenset([
    "merchant_agreement",
    "merchant_agreement_sign",
    "merchant_agreement_status",
    "merchant_agreement_pdf",
    "merchant_agreement_pdf_view",
    "merchant_dashboard",
    "home",
    "home_redirect",
    "logout",
    "login",
    "public_home",
    "offline",
])


def role_required(test_func, sensitive=False):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if test_func(request.user):
                return view_func(request, *args, **kwargs)
            return render(
                request,
                "accounts/role_forbidden.html",
                {"dashboard_url": role_redirect_url(request.user)},
                status=403,
            )

        return wrapped

    return decorator


def merchant_agreement_required(view_func):
    """
    Decorator that blocks merchant access until the Merchant Participation
    Agreement has been signed. Wraps views that require a signed agreement.
    """
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_merchant(request.user):
            return view_func(request, *args, **kwargs)

        # Lazy import to avoid circular dependencies
        from merchants.models import Merchant, MerchantAgreement
        merchant = Merchant.objects.filter(owner=request.user).first()
        if merchant and not merchant.has_signed_agreement:
            return render(
                request,
                "merchants/agreement_required.html",
                {"merchant": merchant},
            )
        return view_func(request, *args, **kwargs)

    return wrapped


def merchant_required(view_func=None, *, sensitive=False, require_agreement=False):
    decorator = role_required(is_merchant, sensitive=sensitive)
    if view_func:
        wrapped = decorator(view_func)
        return merchant_agreement_required(wrapped) if require_agreement else wrapped
    def combined(fn):
        wrapped = decorator(fn)
        return merchant_agreement_required(wrapped) if require_agreement else wrapped
    return combined


def underwriter_required(view_func=None, *, sensitive=False):
    decorator = role_required(is_underwriter, sensitive=sensitive)
    return decorator(view_func) if view_func else decorator


def hq_required(view_func=None, *, sensitive=False):
    decorator = role_required(is_hq, sensitive=sensitive)
    return decorator(view_func) if view_func else decorator


def merchant_admin_required(view_func=None, *, sensitive=False):
    decorator = role_required(is_hq_or_merchant_admin, sensitive=sensitive)
    return decorator(view_func) if view_func else decorator


def tech_support_required(view_func=None, *, sensitive=False):
    decorator = role_required(is_hq_or_tech_support, sensitive=sensitive)
    return decorator(view_func) if view_func else decorator
