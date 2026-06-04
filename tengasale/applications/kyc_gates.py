"""Approval gates for Didit KYC."""
from __future__ import annotations

from django.conf import settings

from accounts.utils import is_hq


def didit_kyc_required_for_approval() -> bool:
    return bool(getattr(settings, "REQUIRE_DIDIT_KYC_BEFORE_APPROVAL", False))


def is_didit_kyc_approved(application) -> bool:
    if (application.kyc_status or "").strip() == "approved":
        return True
    return bool(application.didit_manual_override and (application.kyc_status or "").strip() == "approved")


def check_didit_approval_gate(application, user) -> dict:
    """
    Returns context for approval UI:
    - blocked: approval must not proceed without override
    - warning: non-blocking warning message
    - can_override: HQ/superuser may override with reason
    """
    required = didit_kyc_required_for_approval()
    approved = is_didit_kyc_approved(application)
    can_override = bool(user.is_superuser or is_hq(user))

    if approved:
        return {
            "blocked": False,
            "warning": "",
            "can_override": can_override,
            "kyc_required": required,
            "kyc_approved": True,
            "requires_override_reason": False,
        }

    if required:
        return {
            "blocked": True,
            "warning": "Didit KYC must be approved before this application can be approved.",
            "can_override": can_override,
            "kyc_required": True,
            "kyc_approved": False,
            "requires_override_reason": can_override,
        }

    return {
        "blocked": False,
        "warning": (
            "Didit KYC is not approved yet. Approval is allowed because "
            "REQUIRE_DIDIT_KYC_BEFORE_APPROVAL is currently False."
        ),
        "can_override": can_override,
        "kyc_required": False,
        "kyc_approved": False,
        "requires_override_reason": False,
    }


def apply_manual_kyc_override(application, reason: str, user) -> None:
    from django.utils import timezone

    reason = (reason or "").strip()
    if not reason:
        raise ValueError("KYC override reason is required.")

    application.didit_manual_override = True
    application.didit_manual_override_reason = reason
    application.didit_manual_override_by = user
    application.didit_manual_override_at = timezone.now()
    application.kyc_status = "approved"
    application.didit_status = application.didit_status or "Approved"
    application.didit_verified_at = application.didit_verified_at or timezone.now()
