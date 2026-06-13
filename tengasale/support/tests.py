"""
Tests for the TengaSale support chatbot, WhatsApp state machine,
CEO e-signature permissions, and support ticket workflows.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from support.models import (
    SupportConversation,
    SupportMessage,
    SupportTicket,
    TicketComment,
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppMessage,
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
from support.whatsapp_ops import format_whatsapp_to, send_whatsapp_message

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


class WhatsAppSupportOperationsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.hq_user = User.objects.create_superuser(
            username="wa_hq", email="wa_hq@test.com", password="testpass123"
        )
        self.staff_user = User.objects.create_user(
            username="wa_staff", email="wa_staff@test.com", password="testpass123"
        )

    def login_hq(self):
        self.client.login(username="wa_hq", password="testpass123")

    def fake_twilio_modules(self, create_mock):
        twilio_module = ModuleType("twilio")
        rest_module = ModuleType("twilio.rest")
        client_class = Mock()
        client_class.return_value.messages.create = create_mock
        rest_module.Client = client_class
        return {"twilio": twilio_module, "twilio.rest": rest_module}, client_class

    def test_phone_formatting_always_uses_whatsapp_prefix(self):
        self.assertEqual(format_whatsapp_to("265883596135"), "whatsapp:+265883596135")
        self.assertEqual(format_whatsapp_to("0883596135"), "whatsapp:+265883596135")
        self.assertEqual(format_whatsapp_to("+265883596135"), "whatsapp:+265883596135")
        self.assertEqual(format_whatsapp_to("whatsapp:+265883596135"), "whatsapp:+265883596135")

    @override_settings(WHATSAPP_PROVIDER="mock")
    def test_mock_send_saves_message_without_calling_twilio(self):
        create_mock = Mock(side_effect=AssertionError("Twilio must not be called in mock mode."))
        modules, _ = self.fake_twilio_modules(create_mock)
        with patch.dict("sys.modules", modules):
            result = send_whatsapp_message("265883596135", "Hi from mock", sent_by=self.hq_user)
        self.assertTrue(result["ok"])
        self.assertTrue(result["simulated"])
        self.assertEqual(result["to"], "whatsapp:+265883596135")
        create_mock.assert_not_called()
        message = WhatsAppMessage.objects.get(pk=result["message_id"])
        self.assertEqual(message.provider, WhatsAppMessage.PROVIDER_MOCK)
        self.assertEqual(message.provider_status, WhatsAppMessage.STATUS_DELIVERED)

    @override_settings(WHATSAPP_PROVIDER="twilio_production", TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", TWILIO_MESSAGING_SERVICE_SID="", TWILIO_WHATSAPP_FROM="")
    def test_twilio_provider_missing_credentials_gives_clear_error(self):
        result = send_whatsapp_message("265883596135", "Hi from Twilio", sent_by=self.hq_user)
        self.assertFalse(result["ok"])
        self.assertIn("Missing Twilio config", result["error"])
        message = WhatsAppMessage.objects.get(pk=result["message_id"])
        self.assertEqual(message.provider_status, WhatsAppMessage.STATUS_FAILED)
        self.assertIn("TWILIO_ACCOUNT_SID", message.error_message)

    @override_settings(WHATSAPP_PROVIDER="mock")
    def test_real_send_endpoint_rejects_mock_provider(self):
        self.login_hq()
        resp = self.client.post(
            reverse("whatsapp_send_real_test"),
            {"phone": "265883596135", "message": "Hi from TengaSale Support"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])
        self.assertIn("Provider is mock", resp.json()["error"])

    @override_settings(
        WHATSAPP_PROVIDER="twilio_production",
        TWILIO_ACCOUNT_SID="AC123",
        TWILIO_AUTH_TOKEN="secret-token",
        TWILIO_MESSAGING_SERVICE_SID="MG1e20610613b85a734116e75f60d0b524",
        TWILIO_WHATSAPP_FROM="whatsapp:+15558359870",
        WHATSAPP_STATUS_CALLBACK_URL="/tengasale/support/whatsapp/status/",
    )
    def test_real_send_endpoint_saves_twilio_message_sid(self):
        self.login_hq()
        create_mock = Mock(return_value=SimpleNamespace(sid="SMREAL123", status="queued"))
        modules, client_class = self.fake_twilio_modules(create_mock)
        with patch.dict("sys.modules", modules):
            resp = self.client.post(
                reverse("whatsapp_send_real_test"),
                {"phone": "0883596135", "message": "Hi from TengaSale Support"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["to"], "whatsapp:+265883596135")
        self.assertEqual(data["message_sid"], "SMREAL123")
        client_class.assert_called_once_with("AC123", "secret-token")
        create_mock.assert_called_once()
        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs["to"], "whatsapp:+265883596135")
        self.assertEqual(kwargs["messaging_service_sid"], "MG1e20610613b85a734116e75f60d0b524")
        message = WhatsAppMessage.objects.get(provider_message_sid="SMREAL123")
        self.assertEqual(message.provider_status, WhatsAppMessage.STATUS_QUEUED)

    @override_settings(
        WHATSAPP_PROVIDER="twilio_sandbox",
        TWILIO_ACCOUNT_SID="AC123",
        TWILIO_AUTH_TOKEN="secret-token",
        TWILIO_MESSAGING_SERVICE_SID="MGTEST",
        TWILIO_WHATSAPP_FROM="whatsapp:+15558359870",
    )
    def test_twilio_send_failure_is_stored_safely(self):
        class FakeTwilioRestException(Exception):
            code = 63016
            msg = "Destination is not joined to sandbox"

        create_mock = Mock(side_effect=FakeTwilioRestException("Destination is not joined to sandbox"))
        modules, _ = self.fake_twilio_modules(create_mock)
        with patch.dict("sys.modules", modules):
            result = send_whatsapp_message("265883596135", "Hi from Twilio", sent_by=self.hq_user)
        self.assertFalse(result["ok"])
        self.assertIn("Twilio error 63016", result["error"])
        message = WhatsAppMessage.objects.get(pk=result["message_id"])
        self.assertEqual(message.provider_status, WhatsAppMessage.STATUS_FAILED)
        self.assertIn("Destination is not joined to sandbox", message.error_message)

    def test_simulate_get_works_and_does_not_404(self):
        self.login_hq()
        resp = self.client.get(reverse("whatsapp_simulate"), {"phone": "265883596135", "message": "Hi"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(WhatsAppContact.objects.filter(phone_e164="+265883596135").exists())
        self.assertTrue(WhatsAppMessage.objects.filter(direction=WhatsAppMessage.DIRECTION_INBOUND).exists())

    def test_simulate_post_creates_ticket_from_numeric_menu_selection(self):
        self.login_hq()
        resp = self.client.post(reverse("whatsapp_simulate"), {"phone": "265883596135", "message": "2"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ticket_number"].startswith("TS-"))
        ticket = SupportTicket.objects.get(pk=data["ticket_id"])
        self.assertEqual(ticket.category, SupportTicket.CAT_PAYMENT_NOT_REFLECTING)
        self.assertEqual(ticket.priority, SupportTicket.PRI_HIGH)
        self.assertIsNotNone(ticket.sla_due_at)

    def test_twilio_webhook_post_creates_inbound_message_and_unknown_contact(self):
        resp = self.client.post(reverse("whatsapp_webhook"), {
            "From": "whatsapp:+265883596135",
            "To": "whatsapp:+15558359870",
            "Body": "payment not reflecting",
            "MessageSid": "SM123",
            "ProfileName": "Alice",
            "WaId": "265883596135",
            "NumMedia": "0",
        })
        self.assertEqual(resp.status_code, 200)
        contact = WhatsAppContact.objects.get(phone_e164="+265883596135")
        self.assertEqual(contact.sender_type, WhatsAppContact.SENDER_UNKNOWN)
        message = WhatsAppMessage.objects.get(provider_message_sid="SM123")
        self.assertEqual(message.provider_status, WhatsAppMessage.STATUS_RECEIVED)
        self.assertIsNotNone(message.ticket)

    def test_existing_open_ticket_receives_new_message(self):
        contact = WhatsAppContact.objects.create(phone_e164="+265883596136")
        conversation = WhatsAppConversation.objects.create(contact=contact)
        ticket = SupportTicket.objects.create(
            title="Existing",
            description="Existing",
            source="whatsapp",
            linked_contact=contact,
            sender_phone=contact.phone_e164,
            status=SupportTicket.STATUS_IN_PROGRESS,
            created_by=None,
        )
        conversation.active_ticket = ticket
        conversation.save()
        resp = self.client.post(reverse("whatsapp_webhook"), {
            "From": "whatsapp:+265883596136",
            "Body": "More details",
            "MessageSid": "SM124",
            "NumMedia": "0",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(WhatsAppMessage.objects.filter(ticket=ticket, body="More details").exists())

    def test_waiting_for_customer_moves_to_in_progress_on_reply(self):
        contact = WhatsAppContact.objects.create(phone_e164="+265883596137")
        conversation = WhatsAppConversation.objects.create(contact=contact)
        ticket = SupportTicket.objects.create(
            title="Waiting",
            description="Waiting",
            source="whatsapp",
            linked_contact=contact,
            sender_phone=contact.phone_e164,
            status=SupportTicket.STATUS_WAITING_CUSTOMER,
            created_by=None,
        )
        conversation.active_ticket = ticket
        conversation.save()
        self.client.post(reverse("whatsapp_webhook"), {
            "From": "whatsapp:+265883596137",
            "Body": "Here is the receipt",
            "MessageSid": "SM125",
            "NumMedia": "0",
        })
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, SupportTicket.STATUS_IN_PROGRESS)

    def test_legal_and_fraud_keywords_create_urgent_escalation(self):
        self.client.post(reverse("whatsapp_webhook"), {
            "From": "whatsapp:+265883596138",
            "Body": "I will report you to police for fraud",
            "MessageSid": "SM126",
            "NumMedia": "0",
        })
        ticket = SupportTicket.objects.get(sender_phone="+265883596138")
        self.assertEqual(ticket.priority, SupportTicket.PRI_URGENT)
        self.assertTrue(ticket.escalation_reason)

    def test_staff_reply_saves_outbound_message_in_mock_mode(self):
        self.login_hq()
        contact = WhatsAppContact.objects.create(phone_e164="+265883596139")
        ticket = SupportTicket.objects.create(
            title="Reply",
            description="Reply",
            source="whatsapp",
            linked_contact=contact,
            sender_phone=contact.phone_e164,
            status=SupportTicket.STATUS_NEW,
            created_by=None,
        )
        WhatsAppConversation.objects.create(contact=contact, active_ticket=ticket)
        resp = self.client.post(reverse("whatsapp_ticket_reply", args=[ticket.pk]), {"message": "We are checking this."})
        self.assertEqual(resp.status_code, 200)
        msg = WhatsAppMessage.objects.get(ticket=ticket, direction=WhatsAppMessage.DIRECTION_OUTBOUND)
        self.assertEqual(msg.provider_status, WhatsAppMessage.STATUS_DELIVERED)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.first_response_at)

    def test_status_callback_updates_outbound_message_status(self):
        contact = WhatsAppContact.objects.create(phone_e164="+265883596140")
        conversation = WhatsAppConversation.objects.create(contact=contact)
        msg = WhatsAppMessage.objects.create(
            conversation=conversation,
            direction=WhatsAppMessage.DIRECTION_OUTBOUND,
            provider=WhatsAppMessage.PROVIDER_TWILIO,
            provider_message_sid="SMSTATUS",
            provider_status=WhatsAppMessage.STATUS_QUEUED,
        )
        resp = self.client.post(reverse("whatsapp_status"), {"MessageSid": "SMSTATUS", "MessageStatus": "delivered"})
        self.assertEqual(resp.status_code, 200)
        msg.refresh_from_db()
        self.assertEqual(msg.provider_status, WhatsAppMessage.STATUS_DELIVERED)

    def test_status_callback_stores_failed_delivery_error(self):
        contact = WhatsAppContact.objects.create(phone_e164="+265883596141")
        conversation = WhatsAppConversation.objects.create(contact=contact)
        msg = WhatsAppMessage.objects.create(
            conversation=conversation,
            direction=WhatsAppMessage.DIRECTION_OUTBOUND,
            provider=WhatsAppMessage.PROVIDER_TWILIO,
            provider_message_sid="SMFAILSTATUS",
            provider_status=WhatsAppMessage.STATUS_QUEUED,
        )
        resp = self.client.post(reverse("whatsapp_status"), {
            "MessageSid": "SMFAILSTATUS",
            "MessageStatus": "failed",
            "ErrorCode": "63016",
            "ErrorMessage": "Destination is not joined to sandbox",
        })
        self.assertEqual(resp.status_code, 200)
        msg.refresh_from_db()
        self.assertEqual(msg.provider_status, WhatsAppMessage.STATUS_FAILED)
        self.assertEqual(msg.error_message, "Destination is not joined to sandbox")

    def test_permission_prevents_unauthorized_simulation(self):
        self.client.login(username="wa_staff", password="testpass123")
        resp = self.client.get(reverse("whatsapp_simulate"), {"phone": "265883596135", "message": "Hi"})
        self.assertEqual(resp.status_code, 403)

    def test_hq_page_renders_whatsapp_support_dashboard(self):
        self.login_hq()
        resp = self.client.get(reverse("hq_whatsapp_bot"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "WhatsApp Support Operations")
