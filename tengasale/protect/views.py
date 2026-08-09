from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.views.decorators.http import require_http_methods

from contracts.models import Contract
from .forms import ImeiCheckForm, ProtectReportForm
from .models import DeviceCertificate, DeviceRegistryEntry, ProtectAuditEvent, ProtectCase
from .permissions import can_review
from .services import audit, certificate_for, get_or_create_registry, open_case


def _feature():
    if not settings.TENGA_PROTECT_ENABLED: raise Http404


@require_http_methods(["GET", "POST"])
def customer_home(request, contract_number):
    _feature()
    contract = get_object_or_404(Contract.objects.select_related("application"), contract_number=contract_number)
    if request.method == "POST":
        entered = request.POST.get("customer_pin", "")
        digits = "".join(char for char in contract.customer_phone if char.isdigit())
        if len(digits) >= 4 and entered == digits[-4:]:
            verified = request.session.setdefault("protect_verified_contracts", [])
            if contract.contract_number not in verified:
                verified.append(contract.contract_number)
                request.session.modified = True
            messages.success(request, "Identity confirmed. Your Protect documents are ready.")
            return redirect("protect:customer_home", contract_number=contract.contract_number)
        messages.error(request, "Identity confirmation failed.")
    registry = None
    try: registry = get_or_create_registry(contract)
    except Exception: pass
    return render(request, "protect/customer_home.html", {"contract": contract, "registry": registry, "cases": registry.cases.all() if registry else [], "verified": contract.contract_number in request.session.get("protect_verified_contracts", [])})


@require_http_methods(["GET", "POST"])
def report_device(request, contract_number):
    _feature()
    contract = get_object_or_404(Contract.objects.select_related("application"), contract_number=contract_number)
    form = ProtectReportForm(request.POST or None, request.FILES or None, contract=contract)
    if request.method == "POST" and form.is_valid():
        case = open_case(contract=contract, cleaned_data=form.cleaned_data, uploaded_file=form.cleaned_data.get("police_report"), request=request)
        request.session.setdefault("protect_verified_contracts", []).append(contract.contract_number)
        messages.success(request, f"Protect case {case.case_number} is open.")
        return redirect("protect:case_detail", case_id=case.id)
    return render(request, "protect/report.html", {"contract": contract, "form": form})


def case_detail(request, case_id):
    _feature()
    case = get_object_or_404(ProtectCase.objects.select_related("registry_entry__contract"), id=case_id)
    verified = case.registry_entry.contract.contract_number in request.session.get("protect_verified_contracts", [])
    if not verified and not can_review(request.user): raise PermissionDenied("Verify the contract before viewing this Protect case.")
    return render(request, "protect/case_detail.html", {"case": case})


def ownership_certificate(request, contract_number):
    _feature()
    if contract_number not in request.session.get("protect_verified_contracts", []) and not can_review(request.user):
        raise PermissionDenied("Identity confirmation is required before downloading this certificate.")
    contract = get_object_or_404(Contract.objects.select_related("application"), contract_number=contract_number)
    registry = get_or_create_registry(contract); certificate = certificate_for(registry)
    html = get_template("protect/certificate_pdf.html").render({"certificate": certificate, "registry": registry, "contract": contract, "verify_url": request.build_absolute_uri(f"/protect/verify/{certificate.verification_token}/")})
    from xhtml2pdf import pisa
    output = BytesIO(); result = pisa.CreatePDF(html, dest=output)
    if result.err: return HttpResponse("Certificate generation unavailable.", status=503)
    audit("CERTIFICATE_DOWNLOADED", actor=request.user, request=request, metadata={"certificate_id": str(certificate.certificate_id)})
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Tenga-Ownership-{contract.contract_number}.pdf"'
    return response


def verify_certificate(request, token):
    certificate = get_object_or_404(DeviceCertificate.objects.select_related("registry_entry__contract"), verification_token=token)
    audit("CERTIFICATE_VERIFIED", request=request, metadata={"certificate_id": str(certificate.certificate_id)})
    return render(request, "protect/verify.html", {"certificate": certificate, "registry": certificate.registry_entry})


@require_http_methods(["GET", "POST"])
def imei_check(request):
    if not settings.PROTECT_PUBLIC_IMEI_CHECK: raise Http404
    form = ImeiCheckForm(request.POST or None); entry = None; result = None
    if request.method == "POST" and form.is_valid():
        imei = form.cleaned_data["imei"]
        entry = DeviceRegistryEntry.objects.filter(Q(imei=imei) | Q(imei_2=imei)).first()
        if not entry: result = "unknown"
        elif entry.protection_status == "reported_stolen": result = "reported_stolen"
        elif entry.financing_status == "completed": result = "financing_completed"
        elif entry.protection_status == "recovered": result = "recovered"
        elif entry.financing_status == "active": result = "financing_active"
        else: result = "clear"
        audit("IMEI_CHECKED", request=request, metadata={"result": result, "imei_ending": imei[-4:]})
    return render(request, "protect/imei_check.html", {"form": form, "entry": entry, "result": result})


@login_required
def officer_dashboard(request):
    _feature()
    if not can_review(request.user):
        audit("PERMISSION_DENIED", actor=request.user, request=request, metadata={"capability": "protect_portal"})
        raise PermissionDenied("Explicit Tenga Protect assignment is required.")
    cases = ProtectCase.objects.select_related("registry_entry__contract")
    context = {"cases": cases[:50], "open_count": cases.exclude(status__startswith="closed").count(),
               "awaiting_count": cases.filter(status__in=["new", "awaiting_police_report", "awaiting_approval"]).count(),
               "active_location_count": 0, "recovered_count": cases.filter(status="closed_recovered").count()}
    return render(request, "protect/officer_dashboard.html", context)


@login_required
def privacy_dashboard(request):
    events = ProtectAuditEvent.objects.filter(actor=request.user, action__in=["CUSTOMER_CONSENT_CAPTURED", "LOCATION_REQUESTED", "LOCATION_RETURNED"])
    return render(request, "protect/privacy.html", {"events": events, "policy_version": settings.PROTECT_POLICY_VERSION})
