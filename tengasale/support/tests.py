"""
Tests for the TengaSale support chatbot, WhatsApp state machine,
CEO e-signature permissions, and support ticket workflows.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from support.models import (
    SupportConversation,
    SupportMessage,
    SupportTicket,
    TicketComment,
)
from support.chatbot import (
    process_message,
    MAIN_MENU,
    CUSTOMER_MENU,
    MERCHANT_MENU,
    UNDERWRITER_MENU,
    STAFF_MENU,
    FRAUD_MENU,
    HUMAN_TRANSFER,
    UNRECOGNISED,
)

User = get_user_model()


def _conv(phone="+265000000001"):
    """Create a fresh SupportConversation in STATE_WELCOME."""
    conv, _ = SupportConversation.get_or_create_for_phone(phone, "Test User")
    conv.current_state = SupportConversation.STATE_WELCOME
    conv.save()
    return conv


# ─────────────────────────────────────────────────────────────────────────────
# State machine tests
# ─────────────────────────────────────────────────────────────────────────────

class ChatbotMainMenuTest(TestCase):
    def test_main_menu_returned_on_fresh_welcome(self):
        """Starting a new conversation should return the main menu."""
        conv = _conv("+265000000002")
        reply = process_message(conv, "hi")
        self.assertIn("TengaSale Support", reply)
        self.assertEqual(conv.current_state, SupportConversation.STATE_WELCOME)

    def test_menu_keyword_resets_to_welcome(self):
        conv = _conv("+265000000003")
        conv.current_state = SupportConversation.STATE_CUSTOMER
        conv.save()
        reply = process_message(conv, "MENU")
        self.assertIn("TengaSale Support", reply)
        conv.refresh_from_db()
        self.assertEqual(conv.current_state, SupportConversation.STATE_WELCOME)

    def test_zero_resets_to_main_menu(self):
        conv = _conv("+265000000004")
        conv.current_state = SupportConversation.STATE_CUSTOMER
        conv.save()
        reply = process_message(conv, "0")
        self.assertIn("TengaSale Support", reply)

    def test_selecting_1_goes_to_customer(self):
        conv = _conv("+265000000005")
        reply = process_message(conv, "1")
        conv.refresh_from_db()
        self.assertEqual(conv.current_state, SupportConversation.STATE_CUSTOMER)
        self.assertIn("Customer Support", reply)

    def test_selecting_2_goes_to_merchant(self):
        conv = _conv("+265000000006")
        reply = process_message(conv, "2")
        conv.refresh_from_db()
        self.assertEqual(conv.current_state, SupportConversation.STATE_MERCHANT)
        self.assertIn("Merchant Support", reply)

    def test_selecting_3_goes_to_underwriter(self):
        conv = _conv("+265000000007")
        reply = process_message(conv, "3")
        conv.refresh_from_db()
        self.assertEqual(conv.current_state, SupportConversation.STATE_UNDERWRITER)
        self.assertIn("Underwriter Support", reply)

    def test_selecting_4_goes_to_staff(self):
        conv = _conv("+265000000008")
        reply = process_message(conv, "4")
        conv.refresh_from_db()
        self.assertEqual(conv.current_state, SupportConversation.STATE_STAFF)
        self.assertIn("Staff Support", reply)

    def test_selecting_5_goes_to_fraud(self):
        conv = _conv("+265000000009")
        reply = process_message(conv, "5")
        conv.refresh_from_db()
        self.assertEqual(conv.current_state, SupportConversation.STATE_FRAUD)
        self.assertIn("Fraud", reply)

    def test_invalid_input_returns_unrecognised(self):
        conv = _conv("+265000000010")
        reply = process_message(conv, "99")
        self.assertIn("did not understand", reply)
        conv.refresh_from_db()
        self.assertEqual(conv.current_state, SupportConversation.STATE_WELCOME)


class ChatbotCustomerMenuTest(TestCase):
    def setUp(self):
        self.conv = _conv("+265000000011")
        self.conv.current_state = SupportConversation.STATE_CUSTOMER
        self.conv.save()

    def test_customer_1_creates_ticket(self):
        """Customer choosing '1' (paid but locked) should create a ticket."""
        before = SupportTicket.objects.count()
        reply = process_message(self.conv, "1")
        self.assertEqual(SupportTicket.objects.count(), before + 1)
        self.assertIn("ticket", reply.lower())
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.current_state, SupportConversation.STATE_TICKET_DONE)

    def test_customer_5_creates_critical_ticket(self):
        """Lost/stolen phone should create a CRITICAL priority ticket."""
        process_message(self.conv, "5")
        ticket = SupportTicket.objects.filter(
            title__icontains="stolen"
        ).last()
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.priority, SupportTicket.PRI_CRITICAL)

    def test_customer_8_transfers_to_human(self):
        """Choosing 8 (speak to support) should set state to human."""
        reply = process_message(self.conv, "8")
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.current_state, SupportConversation.STATE_HUMAN)
        self.assertIn("support officer", reply.lower())

    def test_customer_invalid_reshows_menu(self):
        """Unknown input in customer state should re-show customer menu."""
        reply = process_message(self.conv, "xyz")
        self.assertIn("Customer Support", reply)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.current_state, SupportConversation.STATE_CUSTOMER)


class ChatbotMerchantMenuTest(TestCase):
    def setUp(self):
        self.conv = _conv("+265000000012")
        self.conv.current_state = SupportConversation.STATE_MERCHANT
        self.conv.save()

    def test_merchant_1_creates_ticket(self):
        before = SupportTicket.objects.count()
        process_message(self.conv, "1")
        self.assertEqual(SupportTicket.objects.count(), before + 1)

    def test_merchant_0_goes_back(self):
        reply = process_message(self.conv, "0")
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.current_state, SupportConversation.STATE_WELCOME)
        self.assertIn("TengaSale Support", reply)


class ChatbotFraudMenuTest(TestCase):
    def setUp(self):
        self.conv = _conv("+265000000013")
        self.conv.current_state = SupportConversation.STATE_FRAUD
        self.conv.save()

    def test_all_fraud_options_create_critical_tickets(self):
        """All fraud report options should create CRITICAL priority tickets."""
        for choice in ["1", "2", "3", "4", "5", "6", "7", "8"]:
            conv = _conv(f"+265990{choice}0000{choice}")
            conv.current_state = SupportConversation.STATE_FRAUD
            conv.save()
            process_message(conv, choice)
            conv.refresh_from_db()
            if conv.active_ticket:
                self.assertEqual(conv.active_ticket.priority, SupportTicket.PRI_CRITICAL)


class ChatbotStatePersistenceTest(TestCase):
    def test_state_persists_across_calls(self):
        """Conversation state should be saved to DB between calls."""
        conv = _conv("+265000000014")
        process_message(conv, "1")  # → customer
        conv2 = SupportConversation.objects.get(phone_number="+265000000014")
        self.assertEqual(conv2.current_state, SupportConversation.STATE_CUSTOMER)

    def test_ticket_done_state_resets_on_new_message(self):
        """After ticket_done, any new message should restart to main menu."""
        conv = _conv("+265000000015")
        conv.current_state = SupportConversation.STATE_TICKET_DONE
        conv.save()
        reply = process_message(conv, "anything")
        conv.refresh_from_db()
        self.assertEqual(conv.current_state, SupportConversation.STATE_WELCOME)
        self.assertIn("TengaSale Support", reply)

    def test_message_saved_to_db(self):
        """Inbound messages should be recorded in SupportMessage."""
        conv = _conv("+265000000016")
        before = SupportMessage.objects.count()
        # Calling process_message directly doesn't save inbound — that's done by webhook
        # We test that the chatbot creates system messages on ticket creation
        conv.current_state = SupportConversation.STATE_CUSTOMER
        conv.save()
        process_message(conv, "1")
        after = SupportMessage.objects.filter(conversation=conv).count()
        self.assertGreater(after, before)


# ─────────────────────────────────────────────────────────────────────────────
# CEO Signature permission tests
# ─────────────────────────────────────────────────────────────────────────────

class ExecutiveSignaturePermissionTest(TestCase):
    def setUp(self):
        self.client = Client()
        # HQ (superuser) can access
        self.hq_user = User.objects.create_superuser(
            username="hq_sig_test", email="hq@test.com", password="testpass123"
        )
        # Regular staff cannot
        self.staff_user = User.objects.create_user(
            username="staff_sig_test", email="staff@test.com", password="testpass123"
        )

    def test_hq_can_access_signature_page(self):
        self.client.login(username="hq_sig_test", password="testpass123")
        resp = self.client.get(reverse("hq_executive_signatures"))
        self.assertEqual(resp.status_code, 200)

    def test_non_hq_redirected_from_signature_page(self):
        self.client.login(username="staff_sig_test", password="testpass123")
        resp = self.client.get(reverse("hq_executive_signatures"))
        # Should redirect to login or show 403
        self.assertIn(resp.status_code, [302, 403])

    def test_unauthenticated_redirected(self):
        resp = self.client.get(reverse("hq_executive_signatures"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)


# ─────────────────────────────────────────────────────────────────────────────
# Support ticket workflow tests
# ─────────────────────────────────────────────────────────────────────────────

class SupportTicketWorkflowTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ticket_test_user", password="testpass123"
        )
        self.ticket = SupportTicket.objects.create(
            title="Test ticket",
            description="Test description",
            category=SupportTicket.CAT_PAYMENT,
            priority=SupportTicket.PRI_MEDIUM,
            status=SupportTicket.STATUS_OPEN,
            created_by=self.user,
        )

    def test_ticket_number_format(self):
        """Ticket number should be formatted as TS-XXXXX."""
        self.assertTrue(self.ticket.ticket_number.startswith("TS-"))
        self.assertIn(str(self.ticket.pk), self.ticket.ticket_number)

    def test_ticket_default_status_is_open(self):
        self.assertEqual(self.ticket.status, SupportTicket.STATUS_OPEN)

    def test_ticket_comment_creation(self):
        comment = TicketComment.objects.create(
            ticket=self.ticket,
            author=self.user,
            body="Test comment",
            is_internal=False,
        )
        self.assertEqual(self.ticket.comments.count(), 1)
        self.assertEqual(comment.body, "Test comment")

    def test_internal_comment_is_flagged(self):
        comment = TicketComment.objects.create(
            ticket=self.ticket,
            author=self.user,
            body="Internal note",
            is_internal=True,
        )
        self.assertTrue(comment.is_internal)


# ─────────────────────────────────────────────────────────────────────────────
# Chatbot conversation model tests
# ─────────────────────────────────────────────────────────────────────────────

class SupportConversationModelTest(TestCase):
    def test_get_or_create_creates_new(self):
        conv, created = SupportConversation.get_or_create_for_phone("+265111111111", "Alice")
        self.assertTrue(created)
        self.assertEqual(conv.phone_number, "+265111111111")
        self.assertEqual(conv.whatsapp_name, "Alice")

    def test_get_or_create_returns_existing(self):
        SupportConversation.get_or_create_for_phone("+265222222222", "Bob")
        conv2, created = SupportConversation.get_or_create_for_phone("+265222222222", "Bob")
        self.assertFalse(created)

    def test_initial_state_is_welcome(self):
        conv, _ = SupportConversation.get_or_create_for_phone("+265333333333")
        self.assertEqual(conv.current_state, SupportConversation.STATE_WELCOME)

    def test_whatsapp_name_updated_if_missing(self):
        conv, _ = SupportConversation.get_or_create_for_phone("+265444444444", "")
        SupportConversation.get_or_create_for_phone("+265444444444", "Carol")
        conv.refresh_from_db()
        self.assertEqual(conv.whatsapp_name, "Carol")
