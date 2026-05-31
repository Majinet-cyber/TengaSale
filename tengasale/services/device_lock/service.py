"""
Device Lock Service Layer.

This is the single entry point for all device lock operations in TengaSale.
All external code should call these functions instead of touching providers
or models directly.

Key design rules:
- Every action creates a DeviceLockEvent audit record.
- Failures from providers do not crash the calling code.
- Secrets are never logged.
- All status transitions are persisted atomically where possible.
"""

import logging
from django.conf import settings
from django.utils import timezone

from device_lock.models import DeviceLockProfile, DeviceLockEvent, DeviceLockPolicy

logger = logging.getLogger("tengasale.device_lock")


# ──────────────────────────────────────────────────────────────────────────────
# Profile management
# ──────────────────────────────────────────────────────────────────────────────

def ensure_lock_profile_for_contract(contract_or_application):
    """
    Get or create a DeviceLockProfile for the given contract or application.

    Accepts either a `contracts.Contract` or `applications.FinancingApplication`.
    Returns (profile, created) tuple.
    """
    from contracts.models import Contract
    from applications.models import FinancingApplication

    if isinstance(contract_or_application, Contract):
        contract = contract_or_application
        application = contract.application
        imei = contract.imei_number or (application.imei_number if application else "")
        merchant = contract.merchant
        deal_name = contract.deal_name or ""
        customer_name = contract.customer_name or ""
        customer_phone = contract.customer_phone or ""

        profile, created = DeviceLockProfile.objects.get_or_create(
            contract=contract,
            defaults={
                "application": application,
                "imei": imei,
                "merchant": merchant,
                "deal_name": deal_name,
                "customer_name": customer_name,
                "customer_phone": customer_phone,
                "lock_provider": getattr(settings, "DEVICE_LOCK_PROVIDER", "mock")
                if getattr(settings, "DEVICE_LOCK_ENABLED", False)
                else "none",
            },
        )

        if not created and imei and not profile.imei:
            profile.imei = imei
            profile.save(update_fields=["imei", "updated_at"])

        return profile, created

    if isinstance(contract_or_application, FinancingApplication):
        application = contract_or_application
        imei = application.imei_number or ""
        merchant = application.created_by
        deal_name = str(application.deal) if application.deal_id else ""
        customer_name = application.customer_name or ""
        customer_phone = application.customer_phone or ""

        profile, created = DeviceLockProfile.objects.get_or_create(
            application=application,
            contract=None,
            defaults={
                "imei": imei,
                "merchant": merchant,
                "deal_name": deal_name,
                "customer_name": customer_name,
                "customer_phone": customer_phone,
                "lock_provider": getattr(settings, "DEVICE_LOCK_PROVIDER", "mock")
                if getattr(settings, "DEVICE_LOCK_ENABLED", False)
                else "none",
            },
        )
        return profile, created

    raise TypeError(f"Expected Contract or FinancingApplication, got {type(contract_or_application)}")


# ──────────────────────────────────────────────────────────────────────────────
# Core lock actions
# ──────────────────────────────────────────────────────────────────────────────

def enroll_device(lock_profile, triggered_by=None, trigger_source=None):
    """Enroll a device with the lock provider."""
    if not getattr(settings, "DEVICE_LOCK_ENABLED", False):
        return _disabled_result("enroll")

    source = trigger_source or (
        DeviceLockEvent.SOURCE_ADMIN if triggered_by else DeviceLockEvent.SOURCE_SYSTEM
    )
    _log_event(
        lock_profile,
        DeviceLockEvent.TYPE_ENROLL_REQUESTED,
        triggered_by=triggered_by,
        trigger_source=source,
    )

    provider = _get_provider(lock_profile)
    status_before = lock_profile.lock_status

    try:
        result = provider.enroll_device(lock_profile)
    except Exception as exc:
        logger.exception("enroll_device raised unexpectedly for profile %s", lock_profile.pk)
        result = {"success": False, "error": str(exc), "provider": lock_profile.lock_provider}

    _apply_result(lock_profile, result, "enroll")

    event_type = DeviceLockEvent.TYPE_ENROLL_SUCCESS if result.get("success") else DeviceLockEvent.TYPE_ENROLL_FAILED
    _log_event(
        lock_profile,
        event_type,
        status_before=status_before,
        status_after=lock_profile.lock_status,
        triggered_by=triggered_by,
        trigger_source=source,
        response_payload=_safe_payload(result),
        error_message=result.get("error"),
    )
    return result


def lock_device(lock_profile, reason=None, triggered_by=None, trigger_source=None):
    """Send a lock command for the device."""
    if not getattr(settings, "DEVICE_LOCK_ENABLED", False):
        return _disabled_result("lock")

    source = trigger_source or (
        DeviceLockEvent.SOURCE_ADMIN if triggered_by else DeviceLockEvent.SOURCE_SYSTEM
    )
    status_before = lock_profile.lock_status
    _log_event(
        lock_profile,
        DeviceLockEvent.TYPE_LOCK_REQUESTED,
        triggered_by=triggered_by,
        trigger_source=source,
        notes=reason or "",
    )

    lock_profile.last_lock_attempt_at = timezone.now()
    lock_profile.save(update_fields=["last_lock_attempt_at", "updated_at"])

    provider = _get_provider(lock_profile)
    try:
        result = provider.lock_device(lock_profile, reason=reason)
    except Exception as exc:
        logger.exception("lock_device raised unexpectedly for profile %s", lock_profile.pk)
        result = {"success": False, "error": str(exc), "provider": lock_profile.lock_provider}

    _apply_result(lock_profile, result, "lock")

    event_type = DeviceLockEvent.TYPE_LOCK_SUCCESS if result.get("success") else DeviceLockEvent.TYPE_LOCK_FAILED
    _log_event(
        lock_profile,
        event_type,
        status_before=status_before,
        status_after=lock_profile.lock_status,
        triggered_by=triggered_by,
        trigger_source=source,
        response_payload=_safe_payload(result),
        error_message=result.get("error"),
        notes=reason or "",
    )
    return result


def unlock_device(lock_profile, reason=None, triggered_by=None, trigger_source=None):
    """Send an unlock command for the device."""
    if not getattr(settings, "DEVICE_LOCK_ENABLED", False):
        return _disabled_result("unlock")

    source = trigger_source or (
        DeviceLockEvent.SOURCE_ADMIN if triggered_by else DeviceLockEvent.SOURCE_SYSTEM
    )
    status_before = lock_profile.lock_status
    _log_event(
        lock_profile,
        DeviceLockEvent.TYPE_UNLOCK_REQUESTED,
        triggered_by=triggered_by,
        trigger_source=source,
        notes=reason or "",
    )

    lock_profile.last_unlock_attempt_at = timezone.now()
    lock_profile.save(update_fields=["last_unlock_attempt_at", "updated_at"])

    provider = _get_provider(lock_profile)
    try:
        result = provider.unlock_device(lock_profile, reason=reason)
    except Exception as exc:
        logger.exception("unlock_device raised unexpectedly for profile %s", lock_profile.pk)
        result = {"success": False, "error": str(exc), "provider": lock_profile.lock_provider}

    _apply_result(lock_profile, result, "unlock")

    event_type = DeviceLockEvent.TYPE_UNLOCK_SUCCESS if result.get("success") else DeviceLockEvent.TYPE_UNLOCK_FAILED
    _log_event(
        lock_profile,
        event_type,
        status_before=status_before,
        status_after=lock_profile.lock_status,
        triggered_by=triggered_by,
        trigger_source=source,
        response_payload=_safe_payload(result),
        error_message=result.get("error"),
        notes=reason or "",
    )
    return result


def release_device(lock_profile, reason=None, triggered_by=None, trigger_source=None):
    """Permanently release a device from lock management."""
    if not getattr(settings, "DEVICE_LOCK_ENABLED", False):
        return _disabled_result("release")

    source = trigger_source or (
        DeviceLockEvent.SOURCE_ADMIN if triggered_by else DeviceLockEvent.SOURCE_SYSTEM
    )
    status_before = lock_profile.lock_status
    _log_event(
        lock_profile,
        DeviceLockEvent.TYPE_RELEASE_REQUESTED,
        triggered_by=triggered_by,
        trigger_source=source,
        notes=reason or "",
    )

    provider = _get_provider(lock_profile)
    try:
        result = provider.release_device(lock_profile, reason=reason)
    except Exception as exc:
        logger.exception("release_device raised unexpectedly for profile %s", lock_profile.pk)
        result = {"success": False, "error": str(exc), "provider": lock_profile.lock_provider}

    _apply_result(lock_profile, result, "release")

    if result.get("success"):
        lock_profile.is_released = True
        lock_profile.save(update_fields=["is_released", "updated_at"])

    event_type = DeviceLockEvent.TYPE_RELEASE_SUCCESS if result.get("success") else DeviceLockEvent.TYPE_RELEASE_FAILED
    _log_event(
        lock_profile,
        event_type,
        status_before=status_before,
        status_after=lock_profile.lock_status,
        triggered_by=triggered_by,
        trigger_source=source,
        response_payload=_safe_payload(result),
        error_message=result.get("error"),
        notes=reason or "",
    )
    return result


def sync_device_status(lock_profile, triggered_by=None, trigger_source=None):
    """Fetch the latest status from the provider and update the profile."""
    if not getattr(settings, "DEVICE_LOCK_ENABLED", False):
        return _disabled_result("sync")

    source = trigger_source or DeviceLockEvent.SOURCE_SYSTEM

    provider = _get_provider(lock_profile)
    status_before = lock_profile.lock_status
    try:
        result = provider.get_device_status(lock_profile)
    except Exception as exc:
        logger.exception("sync_device_status raised unexpectedly for profile %s", lock_profile.pk)
        result = {"success": False, "error": str(exc), "provider": lock_profile.lock_provider}

    if result.get("success") and result.get("status"):
        lock_profile.lock_status = result["status"]
        lock_profile.last_sync_at = timezone.now()
        if result.get("provider_device_id"):
            lock_profile.provider_device_id = result["provider_device_id"]
        lock_profile.save(update_fields=["lock_status", "last_sync_at", "provider_device_id", "updated_at"])
    else:
        if result.get("error"):
            lock_profile.last_error = result["error"]
            lock_profile.save(update_fields=["last_error", "updated_at"])

    _log_event(
        lock_profile,
        DeviceLockEvent.TYPE_STATUS_SYNC,
        status_before=status_before,
        status_after=lock_profile.lock_status,
        triggered_by=triggered_by,
        trigger_source=source,
        response_payload=_safe_payload(result),
        error_message=result.get("error"),
    )
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Policy evaluation
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_lock_policy(contract_or_application):
    """
    Evaluate the active lock policy against a contract/application.

    Returns a dict:
    {
        "action": "lock" | "unlock" | "release" | "none",
        "reason": "<human-readable explanation>",
        "profile": <DeviceLockProfile or None>,
        "policy": <DeviceLockPolicy or None>,
        "days_overdue": <int>,
    }
    """
    from contracts.models import Contract
    from applications.models import FinancingApplication

    profile = None
    try:
        if isinstance(contract_or_application, Contract):
            profile = DeviceLockProfile.objects.filter(contract=contract_or_application).first()
        else:
            profile = DeviceLockProfile.objects.filter(application=contract_or_application).first()
    except Exception:
        pass

    policy = DeviceLockPolicy.objects.filter(is_active=True).order_by("-created_at").first()
    if not policy:
        return {"action": "none", "reason": "No active lock policy found.", "profile": profile, "policy": None, "days_overdue": 0}

    if profile is None:
        return {"action": "none", "reason": "No lock profile linked to this contract.", "profile": None, "policy": policy, "days_overdue": 0}

    if profile.is_released:
        return {"action": "none", "reason": "Device has been permanently released.", "profile": profile, "policy": policy, "days_overdue": 0}

    days_overdue = _get_days_overdue(contract_or_application)

    # Already locked — check if should unlock
    if profile.lock_status == DeviceLockProfile.STATUS_LOCKED:
        if days_overdue == 0 and policy.unlock_when_paid_to_date:
            return {"action": "unlock", "reason": "Customer is paid to date; device should be unlocked.", "profile": profile, "policy": policy, "days_overdue": 0}
        return {"action": "none", "reason": f"Device is locked; {days_overdue} day(s) overdue.", "profile": profile, "policy": policy, "days_overdue": days_overdue}

    # Check for contract completion
    if _is_contract_completed(contract_or_application) and policy.release_when_contract_completed:
        return {"action": "release", "reason": "Contract fully repaid; device should be released.", "profile": profile, "policy": policy, "days_overdue": 0}

    # Overdue check
    if days_overdue > policy.lock_after_days_overdue:
        return {
            "action": "lock",
            "reason": f"Contract is {days_overdue} day(s) overdue (threshold: {policy.lock_after_days_overdue} days).",
            "profile": profile,
            "policy": policy,
            "days_overdue": days_overdue,
        }

    if days_overdue > policy.grace_period_days:
        return {
            "action": "lock_recommended",
            "reason": f"Contract is {days_overdue} day(s) overdue (within warning window before {policy.lock_after_days_overdue} days).",
            "profile": profile,
            "policy": policy,
            "days_overdue": days_overdue,
        }

    return {"action": "none", "reason": "Contract is current. No lock action required.", "profile": profile, "policy": policy, "days_overdue": days_overdue}


# ──────────────────────────────────────────────────────────────────────────────
# Lock readiness
# ──────────────────────────────────────────────────────────────────────────────

def get_lock_readiness(lock_profile):
    """
    Check whether a device lock profile is ready for lock operations.

    Returns:
    {
        "ready": True/False,
        "reasons": ["reason 1", "reason 2", ...]
    }
    """
    reasons = []

    if not lock_profile.imei:
        reasons.append("IMEI is missing.")
    elif len(lock_profile.imei) < 14:
        reasons.append("IMEI appears invalid (too short).")

    if not lock_profile.contract and not lock_profile.application:
        reasons.append("No linked contract or application.")

    if lock_profile.lock_provider == "none":
        reasons.append("Lock provider is set to 'none'. Configure DEVICE_LOCK_PROVIDER.")

    if not getattr(settings, "DEVICE_LOCK_ENABLED", False):
        reasons.append("DEVICE_LOCK_ENABLED is False in settings.")

    if lock_profile.is_released:
        reasons.append("Device has been permanently released from lock management.")

    if lock_profile.lock_provider == "mock":
        pass  # Mock is always available

    ready = len(reasons) == 0
    return {"ready": ready, "reasons": reasons}


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_provider(lock_profile):
    from services.device_lock.registry import get_lock_provider
    return get_lock_provider(lock_profile.lock_provider)


def _apply_result(lock_profile, result, action):
    """Map a provider result to lock_status and persist."""
    status_map = {
        "enroll": {True: DeviceLockProfile.STATUS_ENROLLED, False: DeviceLockProfile.STATUS_FAILED},
        "lock": {True: DeviceLockProfile.STATUS_LOCKED, False: DeviceLockProfile.STATUS_FAILED},
        "unlock": {True: DeviceLockProfile.STATUS_UNLOCKED, False: DeviceLockProfile.STATUS_FAILED},
        "release": {True: DeviceLockProfile.STATUS_RELEASED, False: DeviceLockProfile.STATUS_FAILED},
    }
    mapping = status_map.get(action, {})
    new_status = mapping.get(result.get("success", False))
    if new_status:
        lock_profile.lock_status = new_status

    if result.get("provider_device_id"):
        lock_profile.provider_device_id = result["provider_device_id"]

    if result.get("error"):
        lock_profile.last_error = result["error"]
    else:
        lock_profile.last_error = None

    lock_profile.last_sync_at = timezone.now()
    lock_profile.save(update_fields=["lock_status", "provider_device_id", "last_error", "last_sync_at", "updated_at"])


def _log_event(
    lock_profile,
    event_type,
    status_before="",
    status_after="",
    triggered_by=None,
    trigger_source=DeviceLockEvent.SOURCE_SYSTEM,
    request_payload=None,
    response_payload=None,
    error_message=None,
    notes="",
):
    try:
        DeviceLockEvent.objects.create(
            lock_profile=lock_profile,
            event_type=event_type,
            status_before=status_before or "",
            status_after=status_after or "",
            triggered_by=triggered_by,
            trigger_source=trigger_source or DeviceLockEvent.SOURCE_SYSTEM,
            request_payload=request_payload,
            response_payload=response_payload,
            error_message=error_message,
            notes=notes or "",
        )
    except Exception:
        logger.exception("Failed to create DeviceLockEvent for profile %s", lock_profile.pk)


def _safe_payload(result):
    """Return a sanitised copy of the result dict safe for JSON storage."""
    if not result:
        return {}
    safe = dict(result)
    # Ensure raw_response is a dict, not a complex object
    raw = safe.get("raw_response")
    if raw is not None and not isinstance(raw, (dict, list, str, int, float, bool)):
        safe["raw_response"] = str(raw)
    return safe


def _disabled_result(action):
    return {
        "success": False,
        "provider": "disabled",
        "message": "DEVICE_LOCK_ENABLED is False — no action taken.",
        "error": None,
        "raw_response": {},
    }


def _get_days_overdue(contract_or_application):
    """Return how many days overdue the contract is (0 if current)."""
    from contracts.models import Contract
    from django.utils import timezone

    if isinstance(contract_or_application, Contract):
        contract = contract_or_application
        # Check financing contract if linked
        try:
            financing_contract = contract.application.financing_contract
            if financing_contract.next_due_date:
                delta = (timezone.now().date() - financing_contract.next_due_date).days
                return max(delta, 0)
        except Exception:
            pass
    return 0


def _is_contract_completed(contract_or_application):
    """Return True if the underlying financing contract is completed."""
    from contracts.models import Contract

    if isinstance(contract_or_application, Contract):
        return contract_or_application.status == Contract.STATUS_COMPLETE
    return False
