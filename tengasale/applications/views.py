from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import merchant_required
from core.business_hours import business_hours_context
from deals.models import DeviceDeal
from geography.models import Region

from .forms import CustomerDetailsForm, KYCForm, LocationNextOfKinForm, SignatureForm, WorkProofForm
from .models import ApplicationCorrectionToken, ApplicationFieldReview, FinancingApplication


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

            app.save()
            messages.success(request, "Customer details saved.")
            return redirect("choose_device", app_id=app.id)
    else:
        form = CustomerDetailsForm(instance=app)

    return render(request, "applications/customer_details.html", {
        "app": app,
        "form": form,
        **bh,
    })


@merchant_required
def choose_device(request, app_id):
    app = merchant_application(request, app_id)
    deals = DeviceDeal.objects.filter(is_active=True, brand__is_active=True).select_related("brand").order_by(
        "brand__name",
        "model_name",
        "specs",
    )
    deal_options = [
        {
            "id": deal.id,
            "brand": deal.brand.name,
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
    preferred_order = ["Tecno", "Itel", "Redmi/Xiaomi", "Samsung"]
    deal_brand_names = list(dict.fromkeys(deal.brand.name for deal in deals))
    brand_names = [name for name in preferred_order if name in deal_brand_names]
    if not brand_names and not deal_brand_names:
        brand_names = ["TECNO", "itel", "Redmi"]
    for deal in deals:
        if deal.brand.name not in brand_names:
            brand_names.append(deal.brand.name)
    form_error = ""

    if request.method == "POST":
        deal_id = request.POST.get("deal_id")
        selected_cash_price_raw = request.POST.get("selected_cash_price")
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
                app.apply_deal_selection(deal, selected_cash_price)
                app.status = "device_selection"
                app.save(
                    update_fields=[
                        "deal",
                        "selected_cash_price",
                        "selected_deposit_percent",
                        "selected_loan_multiplier",
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
                return redirect("kyc_capture", app_id=app.id)

        messages.error(request, form_error)

    return render(
        request,
        "applications/choose_device.html",
        {
            "app": app,
            "deals": deals,
            "deal_options": deal_options,
            "brand_names": brand_names,
            "form_error": form_error,
        },
    )


@merchant_required
def kyc_capture(request, app_id):
    app = merchant_application(request, app_id)

    if request.method == "POST":
        form = KYCForm(request.POST, request.FILES, instance=app)
        if form.is_valid():
            app = form.save(commit=False)
            app.status = "kyc"
            app.save()
            messages.success(request, "KYC saved.")
            return redirect("location_details", app_id=app.id)
    else:
        form = KYCForm(instance=app)

    kyc_complete = bool(app.customer_face_image and app.id_front_image and app.id_back_image)
    return render(request, "applications/kyc.html", {"app": app, "form": form, "kyc_complete": kyc_complete})


@merchant_required
def location_details(request, app_id):
    app = merchant_application(request, app_id)

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

    if request.method == "POST":
        form = WorkProofForm(request.POST, request.FILES, instance=app)
        if form.is_valid():
            app = form.save(commit=False)
            app.status = "work_details"
            app.save()
            messages.success(request, "Work details saved.")
            return redirect("signature", app_id=app.id)
    else:
        form = WorkProofForm(instance=app)

    return render(request, "applications/work.html", {"app": app, "form": form})


@merchant_required
def signature(request, app_id):
    app = merchant_application(request, app_id)

    if request.method == "POST":
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

    return render(request, "applications/signature.html", {"app": app, "form": form})


@merchant_required
def capture_imei(request, app_id):
    return redirect("kyc_capture", app_id=app_id)


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

    incomplete_statuses = [
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
        "imei_required",
    ]

    return render(
        request,
        "applications/detail.html",
        {
            "app": app,
            "is_incomplete": app.status in incomplete_statuses,
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
        ["approved", "contract_terms", "contract_signature", "imei_entry", "contract_creating", "warranty_check", "locking", "deposit_pending"],
    )


@merchant_required
def completed_applications(request):
    return application_list(
        request,
        "Completed",
        "Completed TengaSale contracts and delivered devices.",
        ["contract_complete", "completed"],
    )


@merchant_required
def rejected_applications(request):
    return application_list(
        request,
        "Archived & Rejected",
        "Applications that were rejected or archived.",
        ["rejected"],
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
