"""
TengaSale WhatsApp Support Chatbot — State Machine

Handles inbound WhatsApp messages and routes them through a menu-driven
flow, creating SupportTickets automatically.

States
------
welcome      → main menu
customer     → customer sub-menu
merchant     → merchant sub-menu
underwriter  → underwriter sub-menu
staff        → staff/employee sub-menu
fraud        → fraud report sub-menu
ticket_done  → ticket created, conversation at rest
human        → escalated to human agent

Each state handler returns the bot reply text and optionally mutates
conversation.current_state.
"""

import logging
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Menu text
# ─────────────────────────────────────────────────────────────────────────────

MAIN_MENU = """Welcome to TengaSale Support 🚀

Please select who you are:

1️⃣  Customer
2️⃣  Merchant
3️⃣  Underwriter
4️⃣  Staff / Employee
5️⃣  Report Fraud

0️⃣  Main Menu"""

CUSTOMER_MENU = """📱 *Customer Support*

Please select your issue:

1️⃣  I paid but phone is still locked
2️⃣  Check my balance
3️⃣  Check days remaining
4️⃣  My phone is locked
5️⃣  Lost / stolen phone
6️⃣  Change phone number
7️⃣  Application status
8️⃣  Speak to a support officer

0️⃣  Back"""

MERCHANT_MENU = """🏪 *Merchant Support*

Please select your issue:

1️⃣  Application & Approval
2️⃣  Customer Deposit
3️⃣  Device Locking
4️⃣  Merchant Commission
5️⃣  Customer Payment Issue
6️⃣  Customer Device Issue
7️⃣  My Sales / Earnings
8️⃣  Customer Documents
9️⃣  Other

0️⃣  Back"""

UNDERWRITER_MENU = """📋 *Underwriter Support*

Please select your issue:

1️⃣  Claiming issue
2️⃣  Call recording issue
3️⃣  KYC review issue
4️⃣  Penalty dispute
5️⃣  Earnings / Volts
6️⃣  Application stuck
7️⃣  Customer call issue
8️⃣  Speak to HQ

0️⃣  Back"""

STAFF_MENU = """👤 *TengaSale Staff Support*

Please select your issue:

1️⃣  Salary / Compensation
2️⃣  Monthly Volts Statement
3️⃣  KPI & Rank Review
4️⃣  Discipline or Penalty Dispute
5️⃣  Warning Letter Query
6️⃣  Leave / Absence
7️⃣  Contract / Document Request
8️⃣  Speak to HR / HQ

0️⃣  Back"""

FRAUD_MENU = """🚨 *Report Fraud*

Please select the fraud type:

1️⃣  Fake merchant
2️⃣  Fake customer
3️⃣  Stolen phone
4️⃣  Payment fraud
5️⃣  Underwriter misconduct
6️⃣  Staff misconduct
7️⃣  Document fraud
8️⃣  Other fraud

0️⃣  Back"""

HUMAN_TRANSFER = """✅ Your request has been noted.

A TengaSale support officer will contact you shortly.

Type *MENU* at any time to return to the main menu."""

UNRECOGNISED = """Sorry, I did not understand that. 🤔

Please reply with the *number* of your choice.

Type *MENU* to return to the main menu."""


# ─────────────────────────────────────────────────────────────────────────────
# Issue maps  (menu choice → ticket metadata)
# ─────────────────────────────────────────────────────────────────────────────

CUSTOMER_ISSUES = {
    "1": {"title": "Paid but phone still locked",     "category": "device_lock",      "priority": "high"},
    "2": {"title": "Balance inquiry",                 "category": "contract",         "priority": "low"},
    "3": {"title": "Days remaining inquiry",          "category": "contract",         "priority": "low"},
    "4": {"title": "Phone is locked",                 "category": "device_lock",      "priority": "high"},
    "5": {"title": "Lost / stolen phone report",      "category": "device_lock",      "priority": "critical"},
    "6": {"title": "Phone number change request",     "category": "data_correction",  "priority": "medium"},
    "7": {"title": "Application status inquiry",      "category": "contract",         "priority": "low"},
    "8": {"human": True},
}

MERCHANT_ISSUES = {
    "1": {"title": "Application & approval issue",   "category": "merchant_onboarding", "priority": "medium"},
    "2": {"title": "Customer deposit issue",          "category": "payment",             "priority": "high"},
    "3": {"title": "Device locking issue",            "category": "device_lock",         "priority": "high"},
    "4": {"title": "Merchant commission issue",       "category": "payout",              "priority": "high"},
    "5": {"title": "Customer payment issue",          "category": "payment",             "priority": "high"},
    "6": {"title": "Customer device issue",           "category": "device_lock",         "priority": "medium"},
    "7": {"title": "Sales / earnings inquiry",        "category": "payout",              "priority": "low"},
    "8": {"title": "Customer documents issue",        "category": "contract",            "priority": "medium"},
    "9": {"title": "Other merchant issue",            "category": "other",               "priority": "low"},
    "0": {"back": True},
}

UNDERWRITER_ISSUES = {
    "1": {"title": "Application claiming issue",      "category": "underwriter",   "priority": "high"},
    "2": {"title": "Call recording issue",            "category": "underwriter",   "priority": "medium"},
    "3": {"title": "KYC review issue",                "category": "underwriter",   "priority": "medium"},
    "4": {"title": "Penalty dispute",                 "category": "underwriter",   "priority": "high"},
    "5": {"title": "Earnings / Volts inquiry",        "category": "payout",        "priority": "medium"},
    "6": {"title": "Application stuck / not loading", "category": "app_error",     "priority": "high"},
    "7": {"title": "Customer call issue",             "category": "underwriter",   "priority": "medium"},
    "8": {"human": True},
}

STAFF_ISSUES = {
    "1": {"title": "Salary / compensation inquiry",   "category": "payout",          "priority": "high"},
    "2": {"title": "Monthly Volts Statement request", "category": "payout",          "priority": "medium"},
    "3": {"title": "KPI & rank review query",         "category": "other",           "priority": "medium"},
    "4": {"title": "Discipline / penalty dispute",    "category": "other",           "priority": "high"},
    "5": {"title": "Warning letter query",            "category": "other",           "priority": "medium"},
    "6": {"title": "Leave / absence request",         "category": "other",           "priority": "low"},
    "7": {"title": "Contract / document request",     "category": "data_correction", "priority": "medium"},
    "8": {"human": True},
}

FRAUD_ISSUES = {
    "1": {"title": "Fake merchant report",            "category": "other",        "priority": "critical"},
    "2": {"title": "Fake customer report",            "category": "other",        "priority": "critical"},
    "3": {"title": "Stolen phone report",             "category": "device_lock",  "priority": "critical"},
    "4": {"title": "Payment fraud report",            "category": "payment",      "priority": "critical"},
    "5": {"title": "Underwriter misconduct report",   "category": "underwriter",  "priority": "critical"},
    "6": {"title": "Staff misconduct report",         "category": "other",        "priority": "critical"},
    "7": {"title": "Document fraud report",           "category": "other",        "priority": "critical"},
    "8": {"title": "Other fraud report",              "category": "other",        "priority": "critical"},
}


# ─────────────────────────────────────────────────────────────────────────────
# State machine entry point
# ─────────────────────────────────────────────────────────────────────────────

def process_message(conversation, text_body: str) -> str:
    """
    Process an inbound message for the given SupportConversation.
    Mutates conversation.current_state and returns the reply string.
    """
    from support.models import SupportConversation, SupportMessage, SupportTicket

    text = (text_body or "").strip()
    lower = text.lower()

    # Global override: MENU / HELP / 0 always returns to main menu
    if lower in {"menu", "help", "start", "hi", "hello", "0"}:
        conversation.current_state = SupportConversation.STATE_WELCOME
        conversation.save(update_fields=["current_state", "last_message_at", "updated_at"])
        return MAIN_MENU

    state = conversation.current_state

    if state == SupportConversation.STATE_WELCOME:
        return _handle_main_menu(conversation, text)

    if state == SupportConversation.STATE_CUSTOMER:
        return _handle_sub_menu(
            conversation, text,
            CUSTOMER_ISSUES,
            SupportConversation.USER_TYPE_CUSTOMER,
            CUSTOMER_MENU,
        )

    if state == SupportConversation.STATE_MERCHANT:
        return _handle_sub_menu(
            conversation, text,
            MERCHANT_ISSUES,
            SupportConversation.USER_TYPE_MERCHANT,
            MERCHANT_MENU,
        )

    if state == SupportConversation.STATE_UNDERWRITER:
        return _handle_sub_menu(
            conversation, text,
            UNDERWRITER_ISSUES,
            SupportConversation.USER_TYPE_UNDERWRITER,
            UNDERWRITER_MENU,
        )

    if state == SupportConversation.STATE_STAFF:
        return _handle_sub_menu(
            conversation, text,
            STAFF_ISSUES,
            SupportConversation.USER_TYPE_STAFF,
            STAFF_MENU,
        )

    if state == SupportConversation.STATE_FRAUD:
        return _handle_sub_menu(
            conversation, text,
            FRAUD_ISSUES,
            SupportConversation.USER_TYPE_UNKNOWN,
            FRAUD_MENU,
        )

    if state in (SupportConversation.STATE_TICKET_DONE, SupportConversation.STATE_HUMAN):
        # Any new input after a ticket — restart
        conversation.current_state = SupportConversation.STATE_WELCOME
        conversation.save(update_fields=["current_state", "last_message_at", "updated_at"])
        return MAIN_MENU

    # Fallback
    return UNRECOGNISED


def _handle_main_menu(conversation, text: str) -> str:
    from support.models import SupportConversation
    mapping = {
        "1": SupportConversation.STATE_CUSTOMER,
        "2": SupportConversation.STATE_MERCHANT,
        "3": SupportConversation.STATE_UNDERWRITER,
        "4": SupportConversation.STATE_STAFF,
        "5": SupportConversation.STATE_FRAUD,
    }
    menus = {
        "1": CUSTOMER_MENU,
        "2": MERCHANT_MENU,
        "3": UNDERWRITER_MENU,
        "4": STAFF_MENU,
        "5": FRAUD_MENU,
    }
    user_types = {
        "1": SupportConversation.USER_TYPE_CUSTOMER,
        "2": SupportConversation.USER_TYPE_MERCHANT,
        "3": SupportConversation.USER_TYPE_UNDERWRITER,
        "4": SupportConversation.USER_TYPE_STAFF,
        "5": SupportConversation.USER_TYPE_UNKNOWN,
    }
    if text in mapping:
        conversation.current_state = mapping[text]
        conversation.user_type = user_types[text]
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=["current_state", "user_type", "last_message_at", "updated_at"])
        return menus[text]
    return UNRECOGNISED


def _handle_sub_menu(conversation, text: str, issues: dict, user_type: str, menu_text: str) -> str:
    from support.models import SupportConversation

    if text == "0":
        conversation.current_state = SupportConversation.STATE_WELCOME
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=["current_state", "last_message_at", "updated_at"])
        return MAIN_MENU

    issue = issues.get(text)
    if not issue:
        return menu_text  # re-show the menu on unrecognised input

    if issue.get("back"):
        conversation.current_state = SupportConversation.STATE_WELCOME
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=["current_state", "last_message_at", "updated_at"])
        return MAIN_MENU

    if issue.get("human"):
        conversation.current_state = SupportConversation.STATE_HUMAN
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=["current_state", "last_message_at", "updated_at"])
        return HUMAN_TRANSFER

    # Create a support ticket
    ticket = _create_ticket(conversation, issue)
    conversation.active_ticket = ticket
    conversation.current_state = SupportConversation.STATE_TICKET_DONE
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=[
        "active_ticket", "current_state", "last_message_at", "updated_at"
    ])
    return _ticket_created_reply(ticket)


def _create_ticket(conversation, issue: dict):
    from support.models import SupportTicket, SupportMessage
    import datetime

    year = timezone.now().year
    ticket = SupportTicket.objects.create(
        title=issue["title"],
        description=(
            f"Ticket raised via WhatsApp chatbot.\n"
            f"Phone: {conversation.phone_number}\n"
            f"Name: {conversation.whatsapp_name or 'Unknown'}\n"
            f"User type: {conversation.get_user_type_display()}"
        ),
        category=issue.get("category", SupportTicket.CAT_OTHER),
        priority=issue.get("priority", SupportTicket.PRI_MEDIUM),
        status=SupportTicket.STATUS_OPEN,
        created_by=None,
    )
    # Link message to ticket
    SupportMessage.objects.create(
        conversation=conversation,
        ticket=ticket,
        sender=SupportMessage.SENDER_SYSTEM,
        message=f"Ticket {ticket.ticket_number} created from WhatsApp chatbot.",
    )
    logger.info("WhatsApp ticket created: %s for %s", ticket.ticket_number, conversation.phone_number)
    return ticket


def _ticket_created_reply(ticket) -> str:
    year = timezone.now().year
    return (
        f"✅ Your support ticket has been created.\n\n"
        f"🎫 *Ticket:* TS-TKT-{year}-{ticket.pk:06d}\n"
        f"📂 *Category:* {ticket.get_category_display()}\n"
        f"🔴 *Priority:* {ticket.get_priority_display()}\n"
        f"📊 *Status:* Open\n\n"
        f"A TengaSale support officer will review it shortly.\n\n"
        f"Type *MENU* to raise another issue."
    )
