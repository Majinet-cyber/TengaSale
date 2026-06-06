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
            "Zalipira masiku pafupifupi {days_covered}.\n"
            "Zalipira mpaka: {paid_through_date}.\n"
            "Masiku otsala: {days_remaining}.\n"
            "Ngongole yotsala ndi MWK{balance_left}.\n"
            "TengaSale. Thandizo: {support_number}."
        ),
        "en": (
            "Thank you for your MWK{amount_paid} payment.\n"
            "It covers about {days_covered} day(s).\n"
            "Paid through: {paid_through_date}.\n"
            "Days remaining: {days_remaining}.\n"
            "Balance: MWK{balance_left}.\n"
            "TengaSale. Support: {support_number}."
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
    "approval": {
        "ny": (
            "Pempho lanu la TengaSale lavomerezedwa.\n"
            "Deposit yanu imatsegula foni kwa masiku 7 ikatsimikizidwa.\n"
            "Pitirizani kulipira nthawi isanakwane kuti foni isatsekedwe.\n"
            "TengaSale."
        ),
        "en": (
            "Your TengaSale application has been approved.\n"
            "Deposit unlocks the device for 7 days.\n"
            "Keep paying before your due date to continue using the phone.\n"
            "TengaSale."
        ),
    },
    "due_reminder": {
        "ny": (
            "Chikumbutso cha TengaSale:\n"
            "Mwalandira foni mpaka {due_date}.\n"
            "Masiku otsala: {days_until_due}.\n"
            "Ngongole yotsala: MWK{balance_left}.\n"
            "Lipirani tsiku, sabata, mwezi kapena ndalama zina kuti mupitirize kugwiritsa ntchito foni."
        ),
        "en": (
            "TengaSale reminder:\n"
            "Your phone access expires on {due_date}.\n"
            "Days remaining: {days_until_due}.\n"
            "Remaining balance: MWK{balance_left}.\n"
            "Pay for a day, week, month or custom amount to extend access."
        ),
    },
    "payment_due_today": {
        "ny": (
            "TengaSale:\n"
            "Mwalandira foni mpaka lero.\n"
            "Ngongole yotsala: MWK{balance_left}.\n"
            "Chonde lipilani lero kuti musatsekedwe."
        ),
        "en": (
            "TengaSale:\n"
            "Your phone access expires today.\n"
            "Remaining balance: MWK{balance_left}.\n"
            "Please pay today to avoid lock action."
        ),
    },
    "arrears_reminder": {
        "ny": (
            "TengaSale:\n"
            "Nthawi yanu ya foni idakwana pa {due_date}.\n"
            "Ngongole yotsala: MWK{balance_left}.\n"
            "Chonde lipilani mwachangu kuti mubwezeretse mwalandira foni."
        ),
        "en": (
            "TengaSale:\n"
            "Your phone access expired on {due_date}.\n"
            "Remaining balance: MWK{balance_left}.\n"
            "Please pay to restore access. Support: {support_number}."
        ),
    },
    "overdue": {
        "ny": (
            "TengaSale:\n"
            "Malipiro anu adakwana. Chonde lipilani kuti mubwezeretse kugwiritsa ntchito foni.\n"
            "Ngongole yotsala: MWK{balance_left}.\n"
            "Thandizo: {support_number}."
        ),
        "en": (
            "TengaSale:\n"
            "Your payment is overdue. Please pay to restore/continue access.\n"
            "Balance: MWK{balance_left}.\n"
            "Support: {support_number}."
        ),
    },
}


def normalize_language(language):
    return "en" if language == "en" else "ny"


def render_sms_template(purpose, language="ny", **context):
    templates = SMS_TEMPLATES.get(purpose) or SMS_TEMPLATES["payment_confirmation"]
    template = templates.get(normalize_language(language)) or templates["ny"]
    defaults = {
        "amount_paid": "0",
        "days_covered": "0",
        "paid_through_date": context.get("next_payment_date") or context.get("due_date") or "",
        "days_remaining": "0",
        "balance_left": "0",
        "support_number": "",
        "next_payment_due": context.get("next_payment_date") or context.get("due_date") or "",
        "due_date": context.get("next_payment_date") or "",
        "days_until_due": "0",
        "otp_code": "",
    }
    defaults.update(context)
    return template.format(**defaults)


def format_sms_date(value):
    if not value:
        return timezone.localdate().strftime("%d %b %Y")
    if hasattr(value, "date"):
        value = value.date()
    return value.strftime("%d %b %Y")
