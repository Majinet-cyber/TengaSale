"""Website views — public-facing pages for /site/."""
import hashlib
import logging
import os

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import WebsiteEnquiryForm


logger = logging.getLogger(__name__)


def _get_landing_stats():
    """Return cached landing page stats dict. Cached for 1 hour. Safe if models are empty."""
    cached = cache.get("landing_stats")
    if cached is not None:
        return cached

    stats = {
        "total_contracts": 0,
        "total_disbursed": 0,
        "total_customers": 0,
        "total_disbursed_formatted": "MWK 0",
    }

    try:
        from portal.models import PaymentContract
        stats["total_contracts"] = PaymentContract.objects.filter(
            deposit_paid=True
        ).count()
    except Exception:
        pass

    try:
        from portal.models import PaymentTransaction
        from django.db.models import Sum
        total = PaymentTransaction.objects.filter(
            status="confirmed"
        ).aggregate(s=Sum("amount"))["s"] or 0
        stats["total_disbursed"] = int(total)
        if total >= 1_000_000:
            stats["total_disbursed_formatted"] = f"MWK {total / 1_000_000:.1f}M"
        elif total >= 1_000:
            stats["total_disbursed_formatted"] = f"MWK {total:,.0f}"
        else:
            stats["total_disbursed_formatted"] = "MWK 0"
    except Exception:
        pass

    try:
        from financing.models import Customer
        stats["total_customers"] = Customer.objects.count()
    except Exception:
        pass

    if stats["total_customers"] == 0:
        try:
            from portal.models import PaymentContract
            stats["total_customers"] = PaymentContract.objects.count()
        except Exception:
            pass

    cache.set("landing_stats", stats, 3600)
    return stats


def _landing_context(request, *, section="", support_form=None):
    initial_category = request.GET.get("category", "")
    valid_categories = {value for value, _label in WebsiteEnquiryForm.CATEGORY_CHOICES}
    if support_form is None:
        initial = {"category": initial_category} if initial_category in valid_categories else None
        support_form = WebsiteEnquiryForm(initial=initial)
    return {
        "section": section,
        "support_form": support_form,
        "support_email": settings.TENGA_SUPPORT_EMAIL,
        "support_delivery_available": settings.TENGA_SUPPORT_EMAIL_DELIVERY_ENABLED,
        "support_success": request.GET.get("support") == "sent",
    }


def _render_landing(request, *, section="", support_form=None):
    return render(
        request,
        "website/landing.html",
        _landing_context(request, section=section, support_form=support_form),
    )


def landing(request):
    return _render_landing(request)


def about(request):
    return _render_landing(request, section="why")


def how_it_works(request):
    return _render_landing(request, section="process")


def merchants(request):
    return _render_landing(request, section="support")


def customers(request):
    return _render_landing(request, section="company")


def faq(request):
    return _render_landing(request, section="faq")


def _support_rate_limited(request):
    remote_address = request.META.get("REMOTE_ADDR", "unknown")
    digest = hashlib.sha256(remote_address.encode("utf-8", errors="ignore")).hexdigest()[:24]
    key = f"website-support:{digest}"
    if cache.add(key, 1, timeout=settings.TENGA_SUPPORT_RATE_WINDOW):
        return False
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=settings.TENGA_SUPPORT_RATE_WINDOW)
        count = 1
    return count > settings.TENGA_SUPPORT_RATE_LIMIT


def _send_support_enquiry(request, form):
    category_label = form.category_label
    subject = (
        f"[Tenga Website][{category_label}] {form.cleaned_data['subject']}"
        f" — {form.cleaned_data['full_name']}"
    )
    originating_page = request.POST.get("originating_page", "").strip()[:500]
    if not originating_page:
        originating_page = request.META.get("HTTP_REFERER", "")[:500] or request.build_absolute_uri("/")
    body = "\n".join(
        [
            "Tenga public website enquiry",
            "",
            f"Full name: {form.cleaned_data['full_name']}",
            f"Email: {form.cleaned_data['email']}",
            f"Phone: {form.cleaned_data.get('phone') or 'Not provided'}",
            f"Category: {category_label}",
            f"Subject: {form.cleaned_data['subject']}",
            f"Consent: {'Yes' if form.cleaned_data['consent'] else 'No'}",
            f"Submitted at: {timezone.now().isoformat()}",
            f"Originating page: {originating_page}",
            "",
            "Message:",
            form.cleaned_data["message"],
        ]
    )
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[settings.TENGA_SUPPORT_EMAIL],
        reply_to=[form.cleaned_data["email"]],
    )
    return email.send(fail_silently=False)


def contact(request):
    if request.method != "POST":
        return _render_landing(request, section="support")

    form = WebsiteEnquiryForm(request.POST)
    if form.is_valid():
        if form.cleaned_data.get("website"):
            form.add_error(None, "We could not submit that enquiry. Please email support directly.")
        elif _support_rate_limited(request):
            form.add_error(
                None,
                "Too many enquiries were submitted from this connection. Please wait a few minutes or email support directly.",
            )
        elif not settings.TENGA_SUPPORT_EMAIL_DELIVERY_ENABLED:
            form.add_error(
                None,
                "Online email delivery is temporarily unavailable. Please use the email link below so your enquiry reaches support.",
            )
        else:
            try:
                delivered = _send_support_enquiry(request, form)
            except Exception:
                logger.exception("Public website support email delivery failed")
                delivered = 0
            if delivered == 1:
                return redirect(f"{reverse('public_home')}?support=sent#support")
            form.add_error(
                None,
                "We could not deliver your enquiry. Your details are still shown below; please email support directly.",
            )

    return _render_landing(request, section="support", support_form=form)


def terms(request):
    return render(request, "website/terms.html", {})


def privacy(request):
    return render(request, "website/privacy.html", {})


def payment_terms(request):
    return render(request, "website/payment_terms.html", {})


def merchant_terms(request):
    return render(request, "website/merchant_terms.html", {})


MALAWI_DISTRICTS = [
    "Balaka", "Blantyre", "Chikwawa", "Chiradzulu", "Chitipa",
    "Dedza", "Dowa", "Karonga", "Kasungu", "Likoma",
    "Lilongwe", "Machinga", "Mangochi", "Mchinji", "Mulanje",
    "Mwanza", "Mzimba", "Mzuzu", "Neno", "Nkhata Bay",
    "Nkhotakota", "Nsanje", "Ntcheu", "Ntchisi", "Phalombe",
    "Rumphi", "Salima", "Thyolo", "Zomba",
]


def merchant_signup(request):
    from .models import MerchantLead

    if request.method == "POST":
        data = request.POST
        errors = []

        business_name = data.get("business_name", "").strip()
        owner_full_name = data.get("owner_full_name", "").strip()
        phone = data.get("phone", "").strip()
        district = data.get("district", "").strip()
        business_type = data.get("business_type", "").strip()

        if not business_name:
            errors.append("Business name is required.")
        if not owner_full_name:
            errors.append("Owner full name is required.")
        if not phone:
            errors.append("Phone number is required.")
        if not district:
            errors.append("District is required.")
        if not business_type:
            errors.append("Business type is required.")

        if not errors:
            reg_raw = data.get("has_business_registration", "")
            has_reg = True if reg_raw == "yes" else (False if reg_raw == "no" else None)

            try:
                monthly_sales = int(data.get("estimated_monthly_phone_sales") or 0)
            except (ValueError, TypeError):
                monthly_sales = 0

            MerchantLead.objects.create(
                business_name=business_name,
                owner_full_name=owner_full_name,
                phone=phone,
                whatsapp_phone=data.get("whatsapp_phone", "").strip(),
                email=data.get("email", "").strip(),
                district=district,
                area=data.get("area", "").strip(),
                business_type=business_type,
                estimated_monthly_phone_sales=monthly_sales,
                has_business_registration=has_reg,
                preferred_payout_method=data.get("preferred_payout_method", "").strip(),
                message=data.get("message", "").strip(),
                source="public_site",
            )
            return redirect("website_merchant_signup_success")

        return render(request, "website/merchant_signup.html", {
            "errors": errors,
            "post": data,
            "districts": MALAWI_DISTRICTS,
        })

    return render(request, "website/merchant_signup.html", {"districts": MALAWI_DISTRICTS})


def merchant_signup_success(request):
    return render(request, "website/merchant_signup_success.html", {})


def careers(request):
    from .models import CareerLead

    if request.method == "POST":
        data = request.POST
        files = request.FILES
        errors = []

        full_name = data.get("full_name", "").strip()
        phone = data.get("phone", "").strip()
        district = data.get("district", "").strip()
        role_interested = data.get("role_interested", "").strip()

        if not full_name:
            errors.append("Full name is required.")
        if not phone:
            errors.append("Phone number is required.")
        if not district:
            errors.append("District is required.")
        if not role_interested:
            errors.append("Please select a role you are interested in.")

        cv = files.get("cv_file")
        if cv:
            ext = os.path.splitext(cv.name)[1].lower()
            if ext not in (".pdf", ".doc", ".docx"):
                errors.append("CV must be a PDF or Word document (.pdf, .doc, .docx).")
            elif cv.size > 5 * 1024 * 1024:
                errors.append("CV file must be under 5 MB.")

        if not errors:
            lead = CareerLead(
                full_name=full_name,
                phone=phone,
                email=data.get("email", "").strip(),
                district=district,
                role_interested=role_interested,
                note=data.get("note", "").strip(),
            )
            if cv:
                lead.cv_file = cv
            lead.save()
            return redirect("website_careers_success")

        return render(request, "website/careers.html", {
            "errors": errors,
            "post": data,
            "districts": MALAWI_DISTRICTS,
        })

    return render(request, "website/careers.html", {"districts": MALAWI_DISTRICTS})


def careers_success(request):
    return render(request, "website/careers_success.html", {})
