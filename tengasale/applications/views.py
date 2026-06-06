import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.decorators import merchant_required
from core.business_hours import business_hours_context
from deals.models import DeviceDeal
from geography.models import Region

from deals.brand_utils import BRAND_STATIC_LOGOS, brand_logo_url, canonical_brand, ordered_brand_names

from .forms import (
    CustomerDetailsForm,
    KYCForm,
    LocationNextOfKinForm,
    SignatureCaptureForm,
    SignatureForm,
    WorkProofForm,
)
from .flow_helpers import (
    KYC_IMAGE_FIELDS,
    application_has_complete_deal,
    kyc_completion_flags,
    kyc_images_complete,
    kyc_missing_image_labels,
    location_step_complete,
    customer_details_complete,
    merchant_application_continue_url,
    merchant_next_step_after_deal_selection,
    redirect_if_deal_required,
    redirect_if_kyc_required,
    stored_file_exists,
    work_step_complete,
)
from .kyc_utils import process_kyc_upload
from .models import ApplicationCorrectionToken, ApplicationFieldReview, FinancingApplication

logger = logging.getLogger("tengasale.applications")

ACTIVE_STATUSES = [
    "draft",
    "started",
    "customer_details",
    "device_selection",
    "kyc",
    "kyc_capture",
    "location_details",
    "work_details",
    "signature",
    "correction_requested",
    "sent_back",
    "submitted",
    "pending_review",
    "resubmitted",
    "under_review",
    "approved",
    "approved_pending_device_lock",
    "device_locked",
    "contract_terms",
    "contract_signature",
    "imei_entry",
    "contract_creating",
    "warranty_check",
    "locking",
    "deposit_pending",
    "imei_required",
]


def merchant_application(request, app_id):
    return get_object_or_404(FinancingApplication, id=app_id, created_by=request.user)


def user_can_view_application(user, app):
    return app.created_by_id == user.id


def geography_json_data():
    data = {}
    regions = Region.objects.prefetch_related("districts__traditional_authorities").order_by("name")
    for region in regions:
        data[region.name] = {}
        for district in region.districts.all():
            data[region.name][district.name] = [ta.name for ta in district.traditional_authorities.all()]
    return data


@merchant_required
def new_application(request):
    # Block unsigned merchants from creating new applications
    from merchants.models import Merchant
    merchant = Merchant.objects.filter(owner=request.user).first()
    if merchant and not merchant.has_signed_agreement:
        return render(request, "merchants/agreement_required.html", {"merchant": merchant})

    app = FinancingApplication.objects.create(
        created_by=request.user,
        status="started",
    )
    return redirect("edit_customer_details", app_id=app.id)


@merchant_required
def edit_customer_details(request, app_id):
    app = merchant_application(request, app_id)
    bh = business_hours_context()

    if request.method == "POST":
        form = CustomerDetailsForm(request.POST, instance=app)
        if form.is_valid():
            app = form.save(commit=False)
            app.monthly_income = app.exact_monthly_income
            app.income_source = app.occupation
            app.status = "customer_details"
            # Auto-flag third-party phone user for risk review
            phone_user = form.cleaned_data.get("phone_user", "")
            app.third_party_phone_user_risk_flagged = bool(phone_user and phone_user != "customer_self")

            # Check for active contract using same National ID (duplicate prevention)
            national_id = form.cleaned_data.get("national_id", "").strip().upper()
            if national_id:
                duplicate = FinancingApplication.objects.filter(
                    national_id__iexact=national_id,
                    status__in=ACTIVE_STATUSES,
                ).exclude(pk=app.pk).first()
                if duplicate:
                    # Check if the duplicate is for the same person in a completed state
                    form.add_error(
                        "national_id",
                        "This National ID already has an active financing contract or application in progress. "
                        "A customer must complete and fully settle their current contract before applying for a new device."
                    )
                    return render(request, "applications/customer_details.html", {
                        "app": app,
                        "form": form,
                        "has_active_contract": True,
                        **bh,
                    })

            previous_phone = app.__class__.objects.filter(pk=app.pk).values_list("customer_phone", flat=True).first()
            phone_changed = bool(app.customer_phone and app.customer_phone != previous_phone)
            if phone_changed:
                app.phone_verified = False
                app.phone_verification_status = "not_sent"
            app.save()
            if app.customer_phone and app.phone_verification_status in ("not_sent", "failed") and not app.phone_verified:
                try:
                    from communications.services import generate_phone_otp
                    generate_phone_otp(app.customer_phone, application=app)
                except Exception:
                    logger.exception("Failed to generate OTP for application %s", app.pk)
                    app.phone_verification_status = "failed"
                    app.save(update_fields=["phone_verification_status"])
            messages.success(request, "Customer details saved.")
            return redirect("choose_device", app_id=app.id)
    else:
        form = CustomerDetailsForm(instance=app)

    from integrations.didit import KYC_STATUS_DISPLAY

    return render(request, "applications/customer_details.html", {
        "app": app,
        "form": form,
        "kyc_status_label": KYC_STATUS_DISPLAY.get(app.kyc_status, "Not Started"),
        **bh,
    })


@merchant_required
@require_POST
def send_phone_otp(request, app_id):
    app = merchant_application(request, app_id)
    if not app.customer_phone:
        messages.warning(request, "Save the customer phone number before sending an OTP.")
        return redirect("edit_customer_details", app_id=app.id)
    try:
        from communications.services import generate_phone_otp
        generate_phone_otp(app.customer_phone, application=app)
        messages.success(request, "Phone OTP sent.")
    except Exception:
        logger.exception("Failed to send OTP for application %s", app.pk)
        app.phone_verification_status = "failed"
        app.save(update_fields=["phone_verification_status"])
        messages.warning(request, "OTP could not be sent right now. You can continue and retry later.")
    return redirect("edit_customer_details", app_id=app.id)


@merchant_required
@require_POST
def verify_phone_otp(request, app_id):
    app = merchant_application(request, app_id)
    otp_code = (request.POST.get("otp_code") or "").strip()
    if not otp_code:
        app.phone_verification_status = "skipped"
        app.phone_verified = False
        app.save(update_fields=["phone_verification_status", "phone_verified"])
        messages.info(request, "Phone OTP skipped. You can continue the application.")
        return redirect("edit_customer_details", app_id=app.id)
    try:
        from communications.services import verify_phone_otp as _verify_phone_otp
        if _verify_phone_otp(app.customer_phone, otp_code, application=app):
            messages.success(request, "Phone number verified.")
        else:
            messages.warning(request, "OTP did not match. You can continue or retry.")
    except Exception:
        logger.exception("Failed to verify OTP for application %s", app.pk)
        messages.warning(request, "OTP verification failed. You can continue the application.")
    return redirect("edit_customer_details", app_id=app.id)


@merchant_required
def choose_device(request, app_id):
    app = merchant_application(request, app_id)
    if not customer_details_complete(app):
        messages.warning(request, "Complete customer details before choosing a deal.")
        return redirect("edit_customer_details", app_id=app.id)
    deals = DeviceDeal.objects.filter(is_active=True, brand__is_active=True).select_related("brand").order_by(
        "brand__name",
        "model_name",
        "specs",
    )
    deal_options = [
        {
            "id": deal.id,
            "brand": canonical_brand(deal.brand.name),
            "model_name": deal.model_name,
            "specs": deal.specs,
            "min_cash_price": str(deal.min_cash_price),
            "max_cash_price": str(deal.max_cash_price),
            "default_cash_price": str(deal.default_cash_price),
            "deposit_percent": str(deal.deposit_percent),
            "loan_multiplier": str(deal.loan_multiplier),
            "term_months": deal.term_months,
            "stock_status": deal.get_stock_status_display(),
            "is_lock_ready": deal.is_lock_ready,
            "condition": deal.get_condition_display(),
        }
        for deal in deals
    ]
    preferred_brand_names = ["Tecno", "Itel", "Samsung", "Redmi/Xiaomi"]
    deal_brand_names = [canonical_brand(deal.brand.name) for deal in deals]
    brand_names = ordered_brand_names([*preferred_brand_names, *deal_brand_names])
    if not brand_names:
        brand_names = ["Tecno", "Itel", "Redmi/Xiaomi"]

    brand_cards = []
    for name in brand_names:
        count = sum(1 for d in deals if canonical_brand(d.brand.name) == name)
        brand_cards.append({
            "name": name,
            "label": "Redmi" if name == "Redmi/Xiaomi" else name,
            "deal_count": count,
            "logo_url": brand_logo_url(BRAND_STATIC_LOGOS.get(name, "")),
        })
    form_error = ""

    if request.method == "POST":
        deal_id = request.POST.get("deal_id")
        selected_cash_price_raw = request.POST.get("selected_cash_price")
        term_months_raw = request.POST.get("term_months")
        try:
            deal_pk = int(deal_id or "")
        except (TypeError, ValueError):
            deal_pk = None
        deal = deals.filter(id=deal_pk).first() if deal_pk else None

        if not deal:
            form_error = "Select an available device deal before continuing."
        else:
            try:
                selected_cash_price = Decimal(selected_cash_price_raw or deal.default_cash_price)
            except (InvalidOperation, TypeError):
                selected_cash_price = None

            if selected_cash_price is None:
                form_error = "Enter a valid cash price."
            elif selected_cash_price < deal.min_cash_price or selected_cash_price > deal.max_cash_price:
                form_error = "Cash price must stay within the selected deal price range."
            else:
                from core.commercial import ALLOWED_CONTRACT_TERMS, normalize_term_months

                try:
                    term_months = int(term_months_raw or 12)
                except (TypeError, ValueError):
                    term_months = 12
                if term_months not in ALLOWED_CONTRACT_TERMS:
                    form_error = "Contract term must be 3, 6, or 12 months."
                else:
                    term_months = normalize_term_months(term_months, for_new_contract=True)
                    app.apply_deal_selection(deal, selected_cash_price, term_months=term_months)
                    app.status = "device_selection"
                    app.save(
                        update_fields=[
                            "deal",
                            "selected_cash_price",
                            "selected_deposit_percent",
                            "selected_loan_multiplier",
                            "term_months",
                            "calculated_total_loan",
                            "calculated_deposit_amount",
                            "calculated_monthly_payment",
                            "calculated_daily_payment",
                            "calculated_6_month_total",
                            "calculated_6_month_monthly",
                            "calculated_6_month_daily",
                            "calculated_3_month_total",
                            "calculated_3_month_monthly",
                            "calculated_3_month_daily",
                            "status",
                        ]
                    )
                    messages.success(request, "Phone deal selected.")
                    return redirect(merchant_next_step_after_deal_selection(app))

        messages.error(request, form_error)

    return render(
        request,
        "applications/choose_device.html",
        {
            "app": app,
            "deals": deals,
            "deal_options": deal_options,
            "brand_names": brand_names,
            "brand_cards": brand_cards,
            "form_error": form_error,
        },
    )


@merchant_required
def kyc_capture(request, app_id):
    app = merchant_application(request, app_id)
    blocked = redirect_if_deal_required(request, app)
    if blocked:
        return blocked

    if request.method == "POST":
        form = KYCForm(request.POST, request.FILES, instance=app)
        if form.is_valid():
            app = form.save(commit=False)
            for field_name in KYC_IMAGE_FIELDS:
                uploaded = request.FILES.get(field_name)
                if uploaded:
                    setattr(app, field_name, process_kyc_upload(uploaded, field_name))
            app.status = "kyc"
            app.save()
            app.refresh_from_db()
            if kyc_images_complete(app):
                next_url = (
                    merchant_application_continue_url(app)
                    if app.deal_id
                    else reverse("location_details", args=[app.id])
                )
                logger.info(
                    "KYC submit app_id=%s missing=[] submit_enabled=True next=%s",
                    app.id,
                    next_url,
                )
                messages.success(request, "KYC saved.")
                return redirect(next_url)
            missing = kyc_missing_image_labels(app)
            logger.info(
                "KYC submit incomplete app_id=%s missing=%s submit_enabled=False",
                app.id,
                missing,
            )
            messages.error(request, f"Missing: {', '.join(missing)}")
        else:
            app.refresh_from_db()
            missing = kyc_missing_image_labels(app)
            if missing:
                logger.info(
                    "KYC submit validation failed app_id=%s missing=%s",
                    app.id,
                    missing,
                )
                messages.error(request, f"Missing: {', '.join(missing)}")
    else:
        form = KYCForm(instance=app)

    app.refresh_from_db()
    kyc_complete = kyc_images_complete(app)
    flags = kyc_completion_flags(app)
    logger.info(
        "KYC review render app_id=%s has_selfie=%s has_id_front=%s has_id_back=%s submit_enabled=%s",
        app.id,
        flags["customer_face_image"],
        flags["id_front_image"],
        flags["id_back_image"],
        kyc_complete,
    )
    from integrations.didit import KYC_STATUS_DISPLAY

    return render(
        request,
        "applications/kyc.html",
        {
            "app": app,
            "form": form,
            "kyc_complete": kyc_complete,
            "kyc_missing_labels": kyc_missing_image_labels(app),
            "kyc_status_label": KYC_STATUS_DISPLAY.get(app.kyc_status, "Not Started"),
        },
    )


@merchant_required
@require_POST
def kyc_save_image(request, app_id):
    app = merchant_application(request, app_id)
    field_name = (request.POST.get("field") or "").strip()
    if field_name not in KYC_IMAGE_FIELDS:
        return JsonResponse({"error": "Invalid image field."}, status=400)

    uploaded_file = request.FILES.get("image")
    if not uploaded_file:
        return JsonResponse({"error": "No image uploaded."}, status=400)

    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    content_type = getattr(uploaded_file, "content_type", None)
    if content_type and content_type not in allowed_types:
        return JsonResponse({"error": "Upload a JPEG, PNG, or WebP image."}, status=400)

    processed = process_kyc_upload(uploaded_file, field_name)
    setattr(app, field_name, processed)
    app.save(update_fields=[field_name])
    image = getattr(app, field_name)
    if not stored_file_exists(image):
        logger.warning("KYC upload did not persist app_id=%s field=%s name=%s", app.id, field_name, getattr(image, "name", ""))
        return JsonResponse({"error": "Photo did not save. Please retake or upload it again."}, status=500)

    relative_url = image.url
    media_url = request.build_absolute_uri(relative_url)
    logger.info(
        "Photo uploaded app_id=%s field=%s url=%s name=%s",
        app.id,
        field_name,
        media_url,
        image.name,
    )
    return JsonResponse({
        "ok": True,
        "field": field_name,
        "url": media_url,
        "relative_url": relative_url,
        "absolute_url": media_url,
    })


@merchant_required
def location_details(request, app_id):
    app = merchant_application(request, app_id)
    blocked = redirect_if_kyc_required(request, app)
    if blocked:
        return blocked

    if request.method == "POST":
        form = LocationNextOfKinForm(request.POST, request.FILES, instance=app)
        if form.is_valid():
            app = form.save(commit=False)
            app.location = app.precise_location
            app.status = "location_details"
            app.save()
            messages.success(request, "Location saved.")
            return redirect("work_details", app_id=app.id)
    else:
        form = LocationNextOfKinForm(instance=app)

    return render(
        request,
        "applications/location.html",
        {
            "app": app,
            "form": form,
            "geography_data": geography_json_data(),
        },
    )


@merchant_required
def work_details(request, app_id):
    app = merchant_application(request, app_id)
    blocked = redirect_if_kyc_required(request, app)
    if blocked:
        return blocked

    if request.method == "POST":
        form = WorkProofForm(request.POST, request.FILES, instance=app)
        if form.is_valid():
            app = form.save(commit=False)
            app.status = "work_details"
            app.save()
            messages.success(request, "Work details saved.")
            return redirect("application_review", app_id=app.id)
    else:
        form = WorkProofForm(instance=app)

    return render(request, "applications/work.html", {"app": app, "form": form})


@merchant_required
def signature(request, app_id):
    app = merchant_application(request, app_id)

    if request.method == "POST" and request.POST.get("save_signature"):
        if request.POST.get("agreed_to_terms") != "on":
            messages.error(request, "Customer confirms and agrees to the TengaSale contract terms.")
            return redirect("signature", app_id=app.id)
        form = SignatureCaptureForm(request.POST)
        if form.is_valid():
            app.signature_image.save(form.signature_file.name, form.signature_file, save=False)
            app.agreed_to_terms = True
            app.status = "signature"
            app.save(update_fields=["signature_image", "agreed_to_terms", "status"])
            sig = app.signature_image
            logger.info(
                "Signature saved app_id=%s url=%s name=%s",
                app.id,
                sig.url if sig else "",
                sig.name if sig else "",
            )
            messages.success(request, "Signature saved.")
            return redirect("application_review", app_id=app.id)
        messages.error(request, "Could not save signature. Please try again.")

    return render(request, "applications/signature.html", {"app": app})


@merchant_required
def application_review(request, app_id):
    app = merchant_application(request, app_id)
    blocked = redirect_if_deal_required(request, app)
    if blocked:
        return blocked

    if request.method == "POST":
        if not application_has_complete_deal(app):
            messages.error(request, "Select a phone deal before submitting.")
            return redirect("choose_device", app_id=app.id)
        if not app.signature_image:
            messages.warning(request, "Capture the customer signature before submitting.")
            return redirect("signature", app_id=app.id)
        missing = []
        required_pairs = [
            ("customer_name", "Customer name"),
            ("customer_phone", "Phone number"),
            ("national_id", "National ID"),
            ("date_of_birth", "Date of birth"),
            ("gender", "Gender"),
            ("district", "District"),
            ("traditional_authority", "Traditional Authority / Area"),
            ("work_description", "Work / income answers"),
        ]
        for field_name, label in required_pairs:
            if not getattr(app, field_name, None):
                missing.append(label)
        if not kyc_images_complete(app):
            missing.extend(kyc_missing_image_labels(app))
        if not location_step_complete(app):
            missing.append("Location")
        if not work_step_complete(app):
            missing.append("Work / income answers")
        if missing:
            messages.error(request, "Complete before submission: " + ", ".join(dict.fromkeys(missing)) + ".")
            return redirect("application_review", app_id=app.id)
        form = SignatureForm(request.POST, instance=app)
        if form.is_valid():
            app = form.save(commit=False)
            if form.signature_file:
                app.signature_image.save(form.signature_file.name, form.signature_file, save=False)
            app.save(update_fields=["signature_image", "agreed_to_terms"])
            app.submit()
            messages.success(request, "Application submitted.")
            return redirect("application_submitted", app_id=app.id)
    else:
        form = SignatureForm(instance=app)

    return render(request, "applications/review.html", {"app": app, "form": form})


@merchant_required
def capture_imei(request, app_id):
    app = merchant_application(request, app_id)
    if app.status not in {"approved", "approved_pending_device_lock", "imei_required", "device_locked"}:
        messages.warning(request, "IMEI is only required after underwriter approval.")
        return redirect(app.get_continue_url())

    if request.method == "POST":
        from applications.services.imei_validation import (
            IMEI_DUPLICATE_MESSAGE,
            get_active_imei_conflict,
            is_valid_imei,
            normalize_imei,
        )
        from applications.services.device_matching import compare_deal_to_imei_result

        imei = normalize_imei(request.POST.get("imei_number"))
        imei_2 = normalize_imei(request.POST.get("imei_number_2"))
        serial_number = (request.POST.get("device_serial_number") or "").strip()
        locking_provider = (request.POST.get("locking_provider") or "").strip()
        lock_status = (request.POST.get("locking_confirmation_status") or "").strip()
        if not is_valid_imei(imei):
            messages.error(request, "Enter a valid 15-digit IMEI before continuing.")
        elif imei_2 and not is_valid_imei(imei_2):
            messages.error(request, "IMEI 2 must be 15 digits if provided.")
        elif get_active_imei_conflict(imei, exclude_application_id=app.id):
            messages.error(request, IMEI_DUPLICATE_MESSAGE)
        elif not locking_provider:
            messages.error(request, "Select the locking provider before continuing.")
        elif lock_status != FinancingApplication.LOCK_STATUS_CONFIRMED:
            messages.error(request, "Confirm the device lock status before generating the contract.")
        else:
            if app.imei_number == imei and (app.imei_api_brand or app.imei_api_model):
                device_match = compare_deal_to_imei_result(
                    app.deal,
                    api_brand=app.imei_api_brand,
                    api_model=app.imei_api_model,
                    imei=imei,
                )
                if not device_match["matches"] and not device_match["invalid_imei"]:
                    messages.error(request, "The IMEI device and deal device do not match.")
                    return render(request, "applications/capture_imei.html", {
                        "app": app,
                        "device_mismatch": device_match,
                        "current_imei": imei,
                    })
            app.imei_number = imei
            app.imei_number_2 = imei_2
            app.device_serial_number = serial_number
            app.locking_provider = locking_provider
            app.locking_confirmation_status = lock_status
            app.status = "device_locked"
            app.save(update_fields=[
                "imei_number",
                "imei_number_2",
                "device_serial_number",
                "locking_provider",
                "locking_confirmation_status",
                "status",
            ])
            messages.success(request, "Device IMEI and lock confirmation saved.")
            return redirect("contract_terms", app_id=app.id)

    return render(request, "applications/capture_imei.html", {"app": app})


@merchant_required
def application_submitted(request, app_id):
    app = get_object_or_404(
        FinancingApplication.objects.select_related("created_by", "claimed_by"),
        id=app_id,
        created_by=request.user,
    )
    return render(
        request,
        "applications/submitted.html",
        {
            "app": app,
            **business_hours_context(),
        },
    )


@merchant_required
def application_corrections(request, app_id):
    app = get_object_or_404(
        FinancingApplication.objects.select_related("created_by", "claimed_by", "reviewed_by"),
        id=app_id,
        created_by=request.user,
    )
    corrections = [
        {
            "label": correction.label,
            "note": correction.note,
            "url": app.get_correction_url_for_field(correction.field_name),
        }
        for correction in app.active_corrections()
    ]
    return render(
        request,
        "applications/corrections.html",
        {
            "app": app,
            "correction_labels": app.correction_field_labels(),
            "corrections": corrections,
            "edit_url": app.get_correction_start_url(),
        },
    )


@merchant_required
def application_detail(request, app_id):
    app = get_object_or_404(
        FinancingApplication.objects.select_related("deal", "contract", "created_by", "claimed_by", "reviewed_by"),
        id=app_id,
    )

    if not user_can_view_application(request.user, app):
        raise PermissionDenied

    # Statuses where merchant has an action to take (show continue button)
    incomplete_statuses = {
        "draft",
        "started",
        "customer_details",
        "device_selection",
        "kyc",
        "kyc_capture",
        "location_details",
        "work_details",
        "signature",
        "correction_requested",
        "sent_back",
        "approved_pending_device_lock",
        "device_locked",
        "imei_required",
    }

    # Post-approval statuses also need a continue/next-step action
    post_approval_statuses = {
        "approved",
        "approved_pending_device_lock",
        "device_locked",
        "contract_terms",
        "contract_signature",
        "imei_entry",
        "contract_creating",
        "warranty_check",
        "locking",
        "deposit_pending",
    }

    # Label describing the next required action after approval
    _post_approval_labels = {
        "approved": "Enter IMEI & lock device",
        "approved_pending_device_lock": "Enter IMEI & lock device",
        "device_locked": "Generate final contract",
        "contract_terms": "Review and accept contract terms",
        "contract_signature": "Sign the contract",
        "imei_entry": "Enter device IMEI",
        "contract_creating": "Contract is being created",
        "warranty_check": "Warranty check in progress",
        "locking": "Device lock in progress",
        "deposit_pending": "Deposit payment pending",
    }

    show_continue = app.status in incomplete_statuses or app.status in post_approval_statuses
    is_post_approval = app.status in post_approval_statuses
    next_step_label = _post_approval_labels.get(app.status, "")

    return render(
        request,
        "applications/detail.html",
        {
            "app": app,
            "is_incomplete": app.status in incomplete_statuses,
            "show_continue": show_continue,
            "is_post_approval": is_post_approval,
            "next_step_label": next_step_label,
        },
    )


@merchant_required
def filtered_application_queryset(request, statuses):
    apps = FinancingApplication.objects.select_related("claimed_by", "contract").filter(
        created_by=request.user,
        status__in=statuses,
    )
    query = (request.GET.get("q") or "").strip()
    if query:
        apps = apps.filter(
            Q(customer_name__icontains=query)
            | Q(national_id__icontains=query)
            | Q(customer_phone__icontains=query)
            | Q(application_number__icontains=query)
            | Q(imei_number__icontains=query)
            | Q(contract__contract_number__icontains=query)
        )
    return apps.order_by("-created_at"), query


@merchant_required
def application_list(request, title, explanation, statuses):
    apps, query = filtered_application_queryset(request, statuses)
    return render(
        request,
        "applications/list.html",
        {"apps": apps, "title": title, "explanation": explanation, "query": query},
    )


@merchant_required
def active_applications(request):
    return application_list(
        request,
        "Active",
        "Applications that can still move through the merchant workflow.",
        ACTIVE_STATUSES,
    )


@merchant_required
def pending_applications(request):
    return application_list(
        request,
        "Pending Review",
        "Submitted applications waiting for underwriter review.",
        ["submitted", "pending_review", "resubmitted", "under_review"],
    )


@merchant_required
def needs_edit_applications(request):
    return application_list(
        request,
        "Needs Edit",
        "Applications returned by underwriting for correction.",
        ["correction_requested", "sent_back"],
    )


@merchant_required
def approved_applications(request):
    return application_list(
        request,
        "Approved",
        "Approved applications ready for contract completion.",
        [
            "approved",
            "approved_pending_device_lock",
            "device_locked",
            "contract_terms",
            "contract_signature",
            "imei_entry",
            "contract_creating",
            "warranty_check",
            "locking",
            "deposit_pending",
        ],
    )


@merchant_required
def completed_applications(request):
    return application_list(
        request,
        "Completed",
        "Completed TengaSale contracts and delivered devices.",
        ["contract_complete", "completed", "active_contract"],
    )


@merchant_required
def rejected_applications(request):
    return application_list(
        request,
        "Archived & Rejected",
        "Applications that were rejected or archived.",
        ["rejected", "cancelled"],
    )


# ---------------------------------------------------------------------------
# Secure customer correction portal (Part B)
# ---------------------------------------------------------------------------

EDITABLE_FIELD_MAP = {
    "full_name": ("customer_name", "Customer Full Name"),
    "national_id": ("national_id", "National ID"),
    "primary_phone": ("customer_phone", "Primary Phone"),
    "occupation": ("occupation", "Occupation"),
    "income_band": ("income_band", "Income Band"),
    "income_source": ("income_source", "Income Source"),
    "region": ("region", "Region"),
    "district": ("district", "District"),
    "traditional_authority": ("traditional_authority", "Traditional Authority"),
    "gps_location": ("gps_coordinates", "GPS Location"),
    "guarantor_name": ("next_of_kin_1_name", "Guarantor / NOK Name"),
    "guarantor_phone": ("next_of_kin_1_phone", "Guarantor / NOK Phone"),
    "neighbour_name": ("next_of_kin_2_name", "Neighbour / NOK Name"),
    "neighbour_phone": ("next_of_kin_2_phone", "Neighbour / NOK Phone"),
    "company_contact_name": ("proof_contact_name", "Company Contact Name"),
    "company_contact_phone": ("proof_contact_phone", "Company Contact Phone"),
    "income_source_description": ("work_description", "Income Description"),
}


def customer_field_correction(request, token):
    """
    Secure customer self-correction portal.
    Token must be non-expired. Only marked fields are shown/editable.
    No internal data (scoring, audit logs, commissions) is exposed.
    """
    from django.utils import timezone
    from core.models import AuditLog

    try:
        token_obj = ApplicationCorrectionToken.objects.select_related("application").get(token=token)
    except ApplicationCorrectionToken.DoesNotExist:
        return render(request, "applications/correction_invalid.html", {"reason": "invalid"}, status=404)

    if not token_obj.is_valid:
        return render(request, "applications/correction_invalid.html", {"reason": "expired"}, status=410)

    app = token_obj.application

    marked_reviews = ApplicationFieldReview.objects.filter(
        application=app,
        status=ApplicationFieldReview.STATUS_MARKED,
    ).order_by("section", "field_key")

    if not marked_reviews.exists():
        return render(request, "applications/correction_invalid.html", {"reason": "no_fields"})

    # Build list of editable fields (from marked reviews only, filtered to known safe field map)
    editable = []
    for review in marked_reviews:
        field_info = EDITABLE_FIELD_MAP.get(review.field_key)
        if field_info:
            model_field, label = field_info
            editable.append({
                "review": review,
                "field_key": review.field_key,
                "model_field": model_field,
                "label": label,
                "reason": review.get_reason_display(),
                "comment": review.comment,
                "current_value": getattr(app, model_field, "") or "",
            })

    if request.method == "POST":
        updated_fields = []
        for item in editable:
            new_val = request.POST.get(item["field_key"], "").strip()
            if new_val and new_val != str(item["current_value"]):
                setattr(app, item["model_field"], new_val)
                updated_fields.append(item["field_key"])
                item["review"].status = ApplicationFieldReview.STATUS_CUSTOMER_UPDATED
                item["review"].save(update_fields=["status", "updated_at"])

        if updated_fields:
            app.save(update_fields=[EDITABLE_FIELD_MAP[f][0] for f in updated_fields])
            token_obj.mark_used()

            AuditLog.objects.create(
                user=None,
                action=AuditLog.ACTION_KYC_CHANGE,
                object_type="FinancingApplication",
                object_id=str(app.id),
                detail={"fields_updated": updated_fields, "token": token[:8] + "..."},
            )

        return render(request, "applications/correction_success.html", {
            "app_number": app.application_number,
            "updated_count": len(updated_fields),
        })

    # Safe context — never expose scoring, commissions, internal notes
    return render(request, "applications/correction_form.html", {
        "app_number": app.application_number,
        "editable_fields": editable,
        "token": token,
        "expires_at": token_obj.expires_at,
    })


@merchant_required
@require_POST
def verify_imei_ajax(request, app_id):
    """
    AJAX endpoint: verify an IMEI against the application's selected deal.
    Called by the merchant IMEI entry form before submission.
    The API key is never included in the response.
    """
    from django.conf import settings as django_settings
    from applications.services.imei_verification import (
        verify_imei_against_selected_device,
        save_verification_result,
    )
    from applications.services.device_matching import compare_deal_to_imei_result

    app = get_object_or_404(FinancingApplication, id=app_id, created_by=request.user)

    imei = request.POST.get("imei", "").strip()
    force_recheck = request.POST.get("force_recheck", "false").lower() == "true"

    if not imei:
        return JsonResponse({"error": "No IMEI provided"}, status=400)

    if not app.deal_id:
        return JsonResponse(
            {"error": "No smartphone deal selected — cannot verify IMEI"},
            status=400,
        )

    if not getattr(django_settings, "IMEI_CHECK_ENABLED", True):
        return JsonResponse({"match_status": "api_error", "reasons": ["IMEI verification disabled"]})

    result = verify_imei_against_selected_device(
        imei=imei,
        selected_device=app.deal,
        user=request.user,
        force_recheck=force_recheck,
    )
    device_match = compare_deal_to_imei_result(
        app.deal,
        api_brand=result.get("api_brand", ""),
        api_model=result.get("api_model", ""),
        imei=imei,
    )

    # Save result to the application
    if result.get("match_status") not in ("api_error",) or result.get("api_brand") or result.get("api_model"):
        app.imei_number = imei
        app.save(update_fields=["imei_number"])
        save_verification_result(app, result, user=request.user)

    # Return safe response — never expose raw API key or internal config
    return JsonResponse({
        "match_status": result.get("match_status", "unknown"),
        "confidence": result.get("confidence", 0),
        "reasons": result.get("reasons", []),
        "api_brand": result.get("api_brand", ""),
        "api_model": result.get("api_model", ""),
        "selected_brand": result.get("selected_brand", ""),
        "selected_model": result.get("selected_model", ""),
        "should_block": result.get("should_block", False),
        "device_match": device_match,
        "order_id": result.get("order_id", ""),
        "api_raw_result": result.get("api_raw_result", ""),
        "success": result.get("success", False),
    })
