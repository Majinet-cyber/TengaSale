ROLE_MODULES = {
    "recommerce_intake": {"recommerce.view_recommercelead", "recommerce.add_deviceintake", "recommerce.change_deviceintake"},
    "recommerce_assessor": {"recommerce.add_deviceinspection", "recommerce.change_deviceinspection"},
    "recommerce_technician": {"recommerce.add_refurbishmentworkorder", "recommerce.change_refurbishmentworkorder"},
    "recommerce_qa": {"recommerce.add_recommerceqa", "recommerce.change_recommerceqa"},
    "recommerce_inventory": {"recommerce.add_recommerceinventoryitem", "recommerce.change_recommerceinventoryitem"},
    "recommerce_supervisor": {"recommerce.approve_recommerce_valuation", "recommerce.override_recommerce_workflow"},
    "recommerce_hq": {"recommerce.view_recommerce_hq", "recommerce.override_recommerce_workflow"},
}


def recommerce_role(user):
    if not user or not user.is_authenticated:
        return ""
    if user.is_superuser:
        return "recommerce_hq"
    profile = getattr(user, "profile", None)
    role = getattr(profile, "staff_role", None)
    return getattr(role, "code", "") or ""


def can_access_recommerce(user):
    return bool(user and user.is_authenticated and (user.is_superuser or recommerce_role(user) in ROLE_MODULES or getattr(getattr(user, "profile", None), "role", None) == "hq"))


def is_recommerce_hq(user):
    return bool(user and user.is_authenticated and (user.is_superuser or recommerce_role(user) == "recommerce_hq" or getattr(getattr(user, "profile", None), "role", None) == "hq"))
