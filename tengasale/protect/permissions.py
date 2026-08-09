PROTECT_GROUPS = {
    "officer": "Tenga Protect Officer",
    "supervisor": "Tenga Protect Supervisor",
    "compliance": "Tenga Protect Compliance Officer",
    "administrator": "Tenga Protect Administrator",
}


def has_protect_role(user, *roles):
    """Explicit assignment only: staff/superuser flags never grant access."""
    if not user or not user.is_authenticated:
        return False
    names = {PROTECT_GROUPS[role] for role in roles}
    return user.groups.filter(name__in=names).exists()


def can_review(user):
    return has_protect_role(user, "officer", "supervisor", "compliance")


def can_request_location(user):
    return has_protect_role(user, "officer")


def can_approve_location(user):
    return has_protect_role(user, "supervisor", "compliance")
