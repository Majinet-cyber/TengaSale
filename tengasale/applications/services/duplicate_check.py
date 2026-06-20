"""
Duplicate / fraud detection for FinancingApplication.

Used in:
  - Sales review views (to flag duplicates during review)
  - HQ fraud checks page
  - Auto-approval eligibility
"""
from django.db.models import Q


def _match_summary(match):
    contract = getattr(match, "contract", None)
    payment_contract = getattr(match, "payment_contract", None)
    created_at = match.submitted_at or match.reviewed_at or match.created_at
    return {
        "application_number": match.application_number,
        "contract_number": getattr(contract, "contract_number", "") or getattr(payment_contract, "contract_number", ""),
        "customer_name": match.customer_name,
        "status": match.status,
        "created_at": created_at,
    }


def _summaries(queryset, limit=5):
    return [_match_summary(match) for match in queryset[:limit]]


def check_duplicate_customer(application):
    """
    Return a dict with detected duplicate/risk signals for the given application.

    Result dict:
        {
            "has_duplicates": bool,
            "flags": [
                {
                    "type": str,   # e.g. "national_id", "phone", "guarantor_phone", "imei"
                    "label": str,  # Human-readable description
                    "matching_apps": QuerySet,  # related applications
                }
            ],
            "active_contract_risk": bool,  # True if customer has active/unpaid contracts
        }
    """
    from applications.models import FinancingApplication

    flags = []
    exclude_pk = application.pk if application.pk else None

    qs_base = FinancingApplication.objects.all()
    if exclude_pk:
        qs_base = qs_base.exclude(pk=exclude_pk)

    # 1. Duplicate national ID
    if application.national_id:
        matching = qs_base.filter(national_id=application.national_id).select_related("created_by", "contract", "payment_contract")
        if matching.exists():
            flags.append({
                "type": "national_id",
                "label": f"National ID {application.national_id} used in {matching.count()} other application(s)",
                "matching_apps": _summaries(matching),
            })

    # 2. Duplicate primary phone
    if application.customer_phone:
        matching = qs_base.filter(customer_phone=application.customer_phone).select_related("created_by", "contract", "payment_contract")
        if matching.exists():
            flags.append({
                "type": "phone",
                "label": f"Phone {application.customer_phone} used in {matching.count()} other application(s)",
                "matching_apps": _summaries(matching),
            })

    # 3. Duplicate guarantor phone (used as primary phone elsewhere)
    if application.next_of_kin_1_phone:
        matching = qs_base.filter(
            Q(customer_phone=application.next_of_kin_1_phone) |
            Q(next_of_kin_1_phone=application.next_of_kin_1_phone) |
            Q(next_of_kin_2_phone=application.next_of_kin_1_phone)
        ).select_related("created_by", "contract", "payment_contract")
        if matching.exists():
            flags.append({
                "type": "guarantor_phone",
                "label": f"Guarantor phone {application.next_of_kin_1_phone} appears in {matching.count()} other application(s)",
                "matching_apps": _summaries(matching),
            })

    # 4. Duplicate IMEI
    if application.imei_number:
        matching = qs_base.filter(imei_number=application.imei_number).select_related("created_by", "contract", "payment_contract")
        if matching.exists():
            flags.append({
                "type": "imei",
                "label": f"IMEI {application.imei_number} used in {matching.count()} other application(s)",
                "matching_apps": _summaries(matching),
            })

    # 5. Check for active/unpaid contracts on same national ID
    active_contract_risk = False
    if application.national_id:
        active_apps = qs_base.filter(
            national_id=application.national_id,
            status__in=["approved", "contract_creating", "locking", "deposit_pending",
                        "contract_complete", "contract_terms", "contract_signature"],
        )
        if active_apps.exists():
            active_apps = active_apps.select_related("created_by", "contract", "payment_contract")
            active_contract_risk = True
            flags.append({
                "type": "active_contract",
                "label": f"Customer with ID {application.national_id} has {active_apps.count()} active/pending contract(s)",
                "matching_apps": _summaries(active_apps, limit=3),
            })

    return {
        "has_duplicates": bool(flags),
        "flags": flags,
        "active_contract_risk": active_contract_risk,
    }


def get_duplicate_summary(application):
    """Return a simple list of flag labels for display in review UI."""
    result = check_duplicate_customer(application)
    return [f["label"] for f in result["flags"]]
