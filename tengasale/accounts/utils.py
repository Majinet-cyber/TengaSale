from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse

from .models import UserProfile


ROLE_GROUPS = {
    "merchant":       "Merchant",
    "merchant_admin": "Merchant Administrator",
    "underwriter":    "Underwriter",
    "tech_support":   "Tech Support",
    "hq":             "HQ",
}

VALID_PORTAL_ROLES = set(ROLE_GROUPS)

ROLE_DISPLAY = {
    "merchant":       "Merchant",
    "merchant_admin": "Merchant Administrator",
    "underwriter":    "Underwriter",
    "tech_support":   "Tech Support",
    "hq":             "HQ",
}


def profile_role(user):
    if not user.is_authenticated:
        return None
    try:
        role = user.profile.role
    except ObjectDoesNotExist:
        return None
    if role == "manager":
        return "underwriter"
    return role or None


def get_tengasale_role(user):
    if not user or not user.is_authenticated:
        return None

    try:
        profile = getattr(user, "profile", None)
        role = getattr(profile, "role", None)
    except ObjectDoesNotExist:
        role = None
    if role == "manager":
        role = "underwriter"
    if role in VALID_PORTAL_ROLES:
        return role

    if user.is_superuser or user.is_staff:
        return "hq"

    return None


def get_user_portal_role(user):
    return get_tengasale_role(user)


def has_role_group(user, role):
    group_name = ROLE_GROUPS.get(role, "")
    return user.groups.filter(name=group_name).exists()


def is_hq(user):
    return get_user_portal_role(user) == "hq"


def is_underwriter(user):
    return get_user_portal_role(user) == "underwriter"


def is_merchant(user):
    return get_user_portal_role(user) == "merchant"


def is_merchant_admin(user):
    return get_user_portal_role(user) == "merchant_admin"


def is_tech_support(user):
    return get_user_portal_role(user) == "tech_support"


def is_hq_or_merchant_admin(user):
    return get_user_portal_role(user) in ("hq", "merchant_admin")


def is_hq_or_tech_support(user):
    return get_user_portal_role(user) in ("hq", "tech_support")


def primary_role(user):
    return get_user_portal_role(user)


def role_redirect_url(user):
    try:
        recommerce_code = getattr(getattr(user.profile, "staff_role", None), "code", "")
    except ObjectDoesNotExist:
        recommerce_code = ""
    recommerce_routes = {
        "recommerce_intake": "recommerce:intake_workspace",
        "recommerce_assessor": "recommerce:assessment_workspace",
        "recommerce_technician": "recommerce:refurbishment_workspace",
        "recommerce_qa": "recommerce:qa_workspace",
        "recommerce_inventory": "recommerce:inventory_workspace",
        "recommerce_supervisor": "recommerce:operations_workspace",
        "recommerce_hq": "recommerce:hq_overview",
    }
    if recommerce_code in recommerce_routes:
        return reverse(recommerce_routes[recommerce_code])
    role = primary_role(user)
    try:
        profile = user.profile
        if profile.user_type in {"founder", "staff", "executive"}:
            return reverse("staff_dashboard")
    except ObjectDoesNotExist:
        pass
    if role == "hq":
        return reverse("hq_dashboard")
    if role == "underwriter":
        return reverse("underwriter_dashboard")
    if role == "merchant":
        return reverse("merchant_dashboard")
    if role == "merchant_admin":
        return reverse("ma_dashboard")
    if role == "tech_support":
        return reverse("support_dashboard")
    return reverse("no_role")


def assign_role(user, role):
    if role not in ROLE_GROUPS:
        raise ValueError(f"Unknown role: {role}")

    for group_name in ROLE_GROUPS.values():
        user.groups.remove(*user.groups.filter(name=group_name))

    from django.contrib.auth.models import Group

    group, _ = Group.objects.get_or_create(name=ROLE_GROUPS[role])
    user.groups.add(group)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.save(update_fields=["role"])
    user._state.fields_cache["profile"] = profile
