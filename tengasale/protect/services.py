import hashlib
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (DeviceCertificate, DeviceRegistryEntry, LocationAccessSession,
                     ProtectAuditEvent, ProtectCase, ProtectConsent, ProtectDocument)
from .permissions import can_approve_location, can_request_location, has_protect_role

CONSENT_TEXT = (
    "I request Tenga Protect to assist me in recovering this device. I understand "
    "that Tenga may temporarily request the location of the reported device solely "
    "for this recovery case. Tenga will not use location information for debt "
    "collection or ordinary account management."
)


def actor_role(user):
    if not user or not getattr(user, "is_authenticated", False):
        return "customer"
    return ",".join(user.groups.values_list("name", flat=True))[:60]


def audit(action, *, case=None, actor=None, request=None, metadata=None, before=None, after=None):
    return ProtectAuditEvent.objects.create(
        action=action, case=case, actor=actor if getattr(actor, "is_authenticated", False) else None,
        actor_role=actor_role(actor), object_type="ProtectCase" if case else "",
        object_reference=case.case_number if case else "",
        ip_address=(request.META.get("REMOTE_ADDR") if request else None),
        metadata=metadata or {}, previous_state=before or {}, new_state=after or {},
    )


def get_or_create_registry(contract):
    application = contract.application
    imei = contract.imei_number or application.imei_number
    if not imei:
        raise ValidationError("The contract must have a verified IMEI before Protect registration.")
    profile = getattr(contract, "device_lock_profile", None)
    return DeviceRegistryEntry.objects.get_or_create(
        contract=contract,
        defaults={
            "imei": imei, "imei_2": application.imei_number_2,
            "serial_number": application.device_serial_number,
            "provider": getattr(profile, "lock_provider", ""),
            "financing_status": "completed" if contract.completed_at else "active",
        },
    )[0]


@transaction.atomic
def open_case(*, contract, cleaned_data, uploaded_file, request):
    registry = get_or_create_registry(contract)
    case_type = cleaned_data["case_type"]
    if case_type == ProtectCase.TYPE_STOLEN and not uploaded_file:
        raise ValidationError("A police report is required for a stolen-device report.")
    case = ProtectCase.objects.create(
        registry_entry=registry, case_type=case_type,
        status="under_investigation" if uploaded_file or case_type == "lost" else "awaiting_police_report",
        incident_date=cleaned_data["incident_date"], incident_time=cleaned_data.get("incident_time"),
        incident_location=cleaned_data["incident_location"], description=cleaned_data["description"],
        police_station=cleaned_data.get("police_station", ""), police_reference=cleaned_data.get("police_reference", ""),
        alternative_phone=cleaned_data["alternative_phone"], alternative_contact_name=cleaned_data.get("alternative_contact_name", ""),
        opened_by=request.user if request.user.is_authenticated else None,
    )
    ProtectConsent.objects.create(
        case=case, policy_version=settings.PROTECT_POLICY_VERSION, text_snapshot=CONSENT_TEXT,
        authentication_method="customer_pin", authenticated_reference="verified",
        customer_user=request.user if request.user.is_authenticated else None,
        ip_address=request.META.get("REMOTE_ADDR"), user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    audit("PROTECT_CASE_CREATED", case=case, actor=request.user, request=request, after={"status": case.status, "type": case.case_type})
    audit("CUSTOMER_CONSENT_CAPTURED", case=case, actor=request.user, request=request, metadata={"policy_version": settings.PROTECT_POLICY_VERSION})
    if uploaded_file:
        digest = hashlib.sha256()
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
        uploaded_file.seek(0)
        ProtectDocument.objects.create(case=case, document_type="police_report", file=uploaded_file,
            original_name=uploaded_file.name, content_type=uploaded_file.content_type or "", sha256=digest.hexdigest())
        audit("POLICE_REPORT_UPLOADED", case=case, actor=request.user, request=request)
    registry.protection_status = "reported_stolen" if case_type == "stolen" else "reported_lost"
    registry.save(update_fields=["protection_status", "updated_at"])
    return case


def certificate_for(registry, certificate_type="ownership", case=None):
    current = DeviceCertificate.objects.filter(registry_entry=registry, certificate_type=certificate_type, status="valid").first()
    return current or DeviceCertificate.objects.create(registry_entry=registry, certificate_type=certificate_type, case=case)


def deny_location(*, actor, case=None, reason, request=None):
    audit("PERMISSION_DENIED", case=case, actor=actor, request=request, metadata={"capability": "device_location", "reason": reason})
    raise PermissionDenied(reason)


def request_location_access(*, case, actor, reason, request=None):
    if not settings.PROTECT_LOCATION_ENABLED:
        deny_location(actor=actor, case=case, reason="Protect location is not enabled.", request=request)
    if not can_request_location(actor):
        deny_location(actor=actor, case=case, reason="Explicit Protect Officer assignment is required.", request=request)
    if case.case_type not in {"lost", "stolen"} or case.status not in {"under_investigation", "awaiting_approval", "recovery_active"}:
        deny_location(actor=actor, case=case, reason="An eligible Protect recovery case is required.", request=request)
    if not hasattr(case, "consent") or not case.consent.is_valid:
        deny_location(actor=actor, case=case, reason="Valid customer recovery consent is required.", request=request)
    session = LocationAccessSession.objects.create(case=case, requested_by=actor, reason=reason)
    audit("LOCATION_ACCESS_REQUESTED", case=case, actor=actor, request=request, metadata={"session": str(session.id)})
    return session


def approve_location_access(*, session, actor, request=None):
    if not settings.PROTECT_LOCATION_ENABLED or not can_approve_location(actor):
        deny_location(actor=actor, case=session.case, reason="Protect Supervisor or Compliance approval is required.", request=request)
    if actor.pk == session.requested_by_id:
        deny_location(actor=actor, case=session.case, reason="Maker-checker policy prevents self-approval.", request=request)
    now = timezone.now()
    session.approved_by, session.status, session.starts_at = actor, "authorised", now
    session.expires_at = now + timedelta(hours=settings.PROTECT_LOCATION_SESSION_HOURS)
    session.full_clean()
    session.save()
    audit("LOCATION_ACCESS_APPROVED", case=session.case, actor=actor, request=request, metadata={"session": str(session.id), "expires_at": session.expires_at.isoformat()})
    return session


def terminate_case(case, *, actor, recovered=False, request=None):
    before = {"status": case.status}
    case.status = "closed_recovered" if recovered else "closed_not_recovered"
    case.closed_at = timezone.now()
    case.save(update_fields=["status", "closed_at", "updated_at"])
    case.location_sessions.filter(status__in=["requested", "authorised"]).update(status="terminated")
    if hasattr(case, "consent") and not case.consent.revoked_at:
        case.consent.revoked_at = timezone.now(); case.consent.save(update_fields=["revoked_at"])
    entry = case.registry_entry
    entry.protection_status = "recovered" if recovered else entry.protection_status
    entry.save(update_fields=["protection_status", "updated_at"])
    audit("CASE_RECOVERED" if recovered else "CASE_CLOSED", case=case, actor=actor, request=request, before=before, after={"status": case.status})
    return case


def activate_theft_lock(case, *, actor, request=None):
    """Use the device provider while preserving a Protect-only lock reason."""
    if not has_protect_role(actor, "officer", "supervisor"):
        audit("PERMISSION_DENIED", case=case, actor=actor, request=request, metadata={"capability": "theft_lock"})
        raise PermissionDenied("Explicit Protect Officer assignment is required.")
    if case.case_type != "stolen" or case.status not in {"under_investigation", "awaiting_approval", "recovery_active"}:
        raise ValidationError("A verified stolen-device case is required.")
    profile = getattr(case.registry_entry.contract, "device_lock_profile", None)
    if not profile:
        raise ValidationError("No device-protection provider profile is registered.")
    from device_lock.models import DeviceLockEvent
    from services.device_lock.service import lock_device
    result = lock_device(profile, reason=f"THEFT_LOCK:{case.case_number}", triggered_by=actor, trigger_source=DeviceLockEvent.SOURCE_ADMIN)
    if result.get("success"):
        entry = case.registry_entry
        entry.device_status, entry.protection_status = "theft_locked", "under_recovery"
        entry.save(update_fields=["device_status", "protection_status", "updated_at"])
        audit("THEFT_LOCK_ACTIVATED", case=case, actor=actor, request=request, metadata={"provider": profile.lock_provider})
    return result
