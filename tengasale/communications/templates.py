from decimal import Decimal, InvalidOperation

from django.utils import timezone


def format_mwk(value):
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    return f"{int(round(amount)):,.0f}"


SMS_TEMPLATES = {
    "payment_confirmation": {
        "ny": (
            "Talandila ndalama MWK{amount_paid}.\n"
            "Ngongole yotsala ndi MWK{balance_left}.\n"
            "Masiku otsala ndi {days_left}.\n"
            "Lipilani pasanakwane pa {next_payment_date}.\n"
            "TengaSale: Mafoni apangongole mosavuta."
        ),
        "en": (
            "We have received MWK{amount_paid}.\n"
            "Your remaining balance is MWK{balance_left}.\n"
            "You have {days_left} days left.\n"
            "Please pay before {next_payment_date}.\n"
            "TengaSale: Simple smartphone financing."
        ),
    },
    "otp": {
        "ny": (
            "Khodi yanu ya TengaSale ndi {otp_code}.\n"
            "Gwiritsani ntchito khodiyi kutsimikizira nambala yanu.\n"
            "Osauza munthu wina aliyense."
        ),
        "en": (
            "Your TengaSale OTP is {otp_code}.\n"
            "Use this code to verify your phone number.\n"
            "Do not share it with anyone."
        ),
    },
    "due_reminder": {
        "ny": (
            "Chikumbutso cha TengaSale:\n"
            "Malipiro anu akuyenera kufika pa {due_date}.\n"
            "Masiku otsala: {days_until_due}.\n"
            "Ngongole yotsala: MWK{balance_left}.\n"
            "Chonde lipilani nthawi yake kuti musatsekedwe."
        ),
        "en": (
            "TengaSale reminder:\n"
            "Your payment is due on {due_date}.\n"
            "Days remaining: {days_until_due}.\n"
            "Remaining balance: MWK{balance_left}.\n"
            "Please pay on time to avoid lock action."
        ),
    },
    "payment_due_today": {
        "ny": (
            "TengaSale:\n"
            "Malipiro anu akuyenera kulipidwa lero.\n"
            "Ngongole yotsala: MWK{balance_left}.\n"
            "Chonde lipilani lero kuti musatsekedwe."
        ),
        "en": (
            "TengaSale:\n"
            "Your payment is due today.\n"
            "Remaining balance: MWK{balance_left}.\n"
            "Please pay today to avoid lock action."
        ),
    },
    "arrears_reminder": {
        "ny": (
            "TengaSale:\n"
            "Malipiro anu adadutsa pa {due_date}.\n"
            "Ngongole yotsala: MWK{balance_left}.\n"
            "Chonde lipilani mwachangu kuti mubwezeretse akaunti yanu bwino."
        ),
        "en": (
            "TengaSale:\n"
            "Your payment was due on {due_date}.\n"
            "Remaining balance: MWK{balance_left}.\n"
            "Please pay urgently to restore your account to good standing."
        ),
    },
}


def normalize_language(language):
    return "en" if language == "en" else "ny"


def render_sms_template(purpose, language="ny", **context):
    templates = SMS_TEMPLATES.get(purpose) or SMS_TEMPLATES["payment_confirmation"]
    template = templates.get(normalize_language(language)) or templates["ny"]
    return template.format(**context)


def format_sms_date(value):
    if not value:
        return timezone.localdate().strftime("%d %b %Y")
    if hasattr(value, "date"):
        value = value.date()
    return value.strftime("%d %b %Y")
