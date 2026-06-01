import base64
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.utils import assign_role
from applications.models import FinancingApplication
from commissions.models import Commission
from deals.models import DeviceBrand, DeviceDeal
from rewards.models import SpinWallet

from .forms import ImeiForm
from .models import Contract, ContractDocumentDelivery


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
    b"\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\r\xefF\xb8\x00\x00\x00\x00IEND\xaeB`\x82"
)


def signature_data():
    return "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")


class ContractFlowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="merchant", password="test-pass-123")
        self.other = User.objects.create_user(username="other", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        assign_role(self.other, "merchant")
        brand = DeviceBrand.objects.create(name="TECNO")
        self.deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="Pop 10C",
            specs="2+64",
            min_cash_price=Decimal("300000.00"),
            max_cash_price=Decimal("400000.00"),
            default_cash_price=Decimal("350000.00"),
            cash_price=Decimal("350000.00"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("875000.00"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="approved",
            customer_name="Jane Banda",
            customer_phone="990870616",
            national_id="RQXFVZC9",
            deal=self.deal,
            selected_cash_price=Decimal("350000.00"),
            calculated_total_loan=Decimal("875000.00"),
            calculated_deposit_amount=Decimal("113750.00"),
            calculated_monthly_payment=Decimal("72916.67"),
            calculated_daily_payment=Decimal("2430.56"),
        )
        self.client.login(username="merchant", password="test-pass-123")

    def test_approved_app_routes_to_contract_terms(self):
        self.assertEqual(self.app.get_continue_url(), reverse("contract_terms", args=[self.app.id]))

    def test_contract_terms_requires_checkbox_and_creates_contract(self):
        response = self.client.post(reverse("contract_terms", args=[self.app.id]), {})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contract.objects.exists())

        response = self.client.post(reverse("contract_terms", args=[self.app.id]), {"confirmed_terms": "on"})
        contract = Contract.objects.get(application=self.app)
        self.app.refresh_from_db()

        self.assertRedirects(response, reverse("contract_signature", args=[contract.id]))
        self.assertTrue(contract.terms_accepted_by_merchant)
        self.assertTrue(contract.contract_number.startswith("E"))
        self.assertEqual(len(contract.contract_number), 9)
        self.assertEqual(self.app.status, "contract_signature")

    def test_contract_numbers_are_unique(self):
        numbers = {Contract.from_application(self.app)[0].contract_number}
        for index in range(3):
            app = FinancingApplication.objects.create(
                created_by=self.merchant,
                status="approved",
                customer_name=f"Customer {index}",
            )
            numbers.add(Contract.from_application(app)[0].contract_number)

        self.assertEqual(len(numbers), 4)

    def test_contract_signature_requires_signature_and_terms(self):
        contract = Contract.from_application(self.app)[0]

        response = self.client.post(reverse("contract_signature", args=[contract.id]), {"customer_terms_accepted": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save the customer contract signature before continuing.")

        response = self.client.post(
            reverse("contract_signature", args=[contract.id]),
            {"signature_data": signature_data(), "customer_terms_accepted": "on"},
        )
        contract.refresh_from_db()
        self.app.refresh_from_db()

        self.assertRedirects(response, reverse("contract_imei", args=[contract.id]))
        self.assertTrue(contract.customer_contract_signature)
        self.assertTrue(contract.customer_terms_accepted)
        self.assertEqual(self.app.status, "imei_entry")

    def test_imei_must_be_exactly_15_digits(self):
        self.assertTrue(ImeiForm(data={"imei_number": "123456789012345"}).is_valid())
        self.assertFalse(ImeiForm(data={"imei_number": "1234567890123456"}).is_valid())
        self.assertFalse(ImeiForm(data={"imei_number": "12345abc9012345"}).is_valid())

    def test_progress_steps_complete_contract_and_award_merchant(self):
        contract = Contract.from_application(self.app)[0]
        contract.status = Contract.STATUS_IMEI_ENTERED
        contract.imei_number = "123456789012345"
        contract.save()

        self.client.get(reverse("contract_progress", args=[contract.id]))
        contract.refresh_from_db()
        self.app.refresh_from_db()
        self.assertEqual(contract.status, Contract.STATUS_CONTRACT_CREATED)
        self.assertEqual(self.app.status, "warranty_check")

        self.client.post(reverse("contract_progress", args=[contract.id]), {"action": "warranty"})
        self.client.post(reverse("contract_progress", args=[contract.id]), {"action": "locked"})
        response = self.client.post(reverse("contract_progress", args=[contract.id]), {"action": "deposit"})
        contract.refresh_from_db()
        self.app.refresh_from_db()

        self.assertRedirects(response, reverse("contract_complete", args=[contract.id]))
        self.assertTrue(contract.warranty_checked)
        self.assertTrue(contract.phone_locked)
        self.assertTrue(contract.deposit_paid)
        self.assertEqual(contract.status, Contract.STATUS_COMPLETE)
        self.assertEqual(self.app.status, "contract_complete")
        self.assertTrue(Commission.objects.filter(application=self.app, role=Commission.ROLE_MERCHANT).exists())
        self.assertEqual(SpinWallet.objects.get(user=self.merchant).available_spins, 1)

    def test_only_creator_or_staff_can_access_contract_flow(self):
        self.client.login(username="other", password="test-pass-123")

        response = self.client.get(reverse("contract_terms", args=[self.app.id]))

        self.assertEqual(response.status_code, 403)

    def test_contract_detail_requires_permission(self):
        contract = Contract.from_application(self.app)[0]
        self.client.login(username="other", password="test-pass-123")

        response = self.client.get(reverse("contract_detail", args=[contract.id]))

        self.assertEqual(response.status_code, 403)

    def test_contract_detail_shows_contract_number_and_amounts(self):
        contract = Contract.from_application(self.app)[0]

        response = self.client.get(reverse("contract_detail", args=[contract.id]))

        self.assertContains(response, contract.contract_number)
        self.assertContains(response, "Cash price")
        self.assertContains(response, "Total loan / contract price")
        self.assertContains(response, "875,000")

    def test_view_contract_link_appears_only_when_contract_exists(self):
        no_contract_app = FinancingApplication.objects.create(created_by=self.merchant, status="completed")
        contract = Contract.from_application(self.app)[0]

        no_contract_response = self.client.get(reverse("application_detail", args=[no_contract_app.id]))
        contract_response = self.client.get(reverse("application_detail", args=[self.app.id]))

        self.assertNotContains(no_contract_response, "VIEW CONTRACT")
        self.assertContains(contract_response, "VIEW CONTRACT")
        self.assertContains(contract_response, reverse("contract_detail", args=[contract.id]))

    def test_contract_complete_page_shows_contract_number(self):
        contract = Contract.from_application(self.app)[0]
        contract.status = Contract.STATUS_COMPLETE
        contract.deposit_paid = True
        contract.save()
        self.app.status = "contract_complete"
        self.app.save(update_fields=["status"])

        response = self.client.get(reverse("contract_complete", args=[contract.id]))

        self.assertContains(response, "Contract complete")
        self.assertContains(response, contract.contract_number)

    def test_signature_sets_terms_accepted_at(self):
        contract = Contract.from_application(self.app)[0]
        before = timezone.now()
        self.client.post(
            reverse("contract_signature", args=[contract.id]),
            {"signature_data": signature_data(), "customer_terms_accepted": "on"},
        )
        contract.refresh_from_db()
        self.assertIsNotNone(contract.terms_accepted_at)
        self.assertGreaterEqual(contract.terms_accepted_at, before)

    def test_signature_sets_customer_terms_accepted(self):
        contract = Contract.from_application(self.app)[0]
        self.client.post(
            reverse("contract_signature", args=[contract.id]),
            {"signature_data": signature_data(), "customer_terms_accepted": "on"},
        )
        contract.refresh_from_db()
        self.assertTrue(contract.customer_terms_accepted)


class ContractCompletionTests(TestCase):
    """Tests for contract completion, PDF generation, and WhatsApp delivery scheduling."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="cm_merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="ITEL")
        self.deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="A70",
            specs="3+64",
            min_cash_price=Decimal("200000.00"),
            max_cash_price=Decimal("280000.00"),
            default_cash_price=Decimal("250000.00"),
            cash_price=Decimal("250000.00"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("625000.00"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="approved",
            customer_name="Alice Phiri",
            customer_phone="991234567",
            national_id="ABCD1234",
            deal=self.deal,
            selected_cash_price=Decimal("250000.00"),
            calculated_total_loan=Decimal("625000.00"),
            calculated_deposit_amount=Decimal("81250.00"),
            calculated_monthly_payment=Decimal("52083.33"),
            calculated_daily_payment=Decimal("1736.11"),
        )
        self.contract, _ = Contract.from_application(self.app)
        self.contract.customer_phone = "0991234567"
        self.contract.save(update_fields=["customer_phone", "updated_at"])
        self.client.login(username="cm_merchant", password="test-pass-123")

    def test_completed_contract_sets_completed_at(self):
        self.contract.status = Contract.STATUS_COMPLETE
        self.contract.deposit_paid = True
        before = timezone.now()
        from services.contracts.completion import handle_contract_completion
        handle_contract_completion(self.contract)
        self.contract.refresh_from_db()
        self.assertIsNotNone(self.contract.completed_at)
        self.assertGreaterEqual(self.contract.completed_at, before)

    def test_completed_contract_sets_ownership_transfer_at(self):
        from services.contracts.completion import handle_contract_completion
        handle_contract_completion(self.contract)
        self.contract.refresh_from_db()
        self.assertIsNotNone(self.contract.ownership_transfer_at)

    def test_completion_creates_delivery_record(self):
        from services.contracts.completion import handle_contract_completion
        handle_contract_completion(self.contract)
        self.assertTrue(ContractDocumentDelivery.objects.filter(contract=self.contract).exists())

    def test_delivery_scheduled_24h_after_completion(self):
        from services.contracts.completion import handle_contract_completion
        handle_contract_completion(self.contract)
        self.contract.refresh_from_db()
        delivery = ContractDocumentDelivery.objects.filter(contract=self.contract).first()
        self.assertIsNotNone(delivery)
        self.assertIsNotNone(self.contract.completed_at)
        expected = self.contract.completed_at + timedelta(hours=24)
        diff = abs((delivery.scheduled_for - expected).total_seconds())
        self.assertLess(diff, 5)

    def test_repeated_completion_does_not_duplicate_delivery(self):
        from services.contracts.completion import handle_contract_completion
        handle_contract_completion(self.contract)
        handle_contract_completion(self.contract)
        count = ContractDocumentDelivery.objects.filter(
            contract=self.contract,
            channel=ContractDocumentDelivery.CHANNEL_WHATSAPP,
            delivery_status__in=["scheduled", "sent"],
        ).count()
        self.assertEqual(count, 1)

    def test_missing_phone_skips_delivery_cleanly(self):
        self.contract.customer_phone = ""
        self.contract.save(update_fields=["customer_phone", "updated_at"])
        from services.contracts.completion import handle_contract_completion
        handle_contract_completion(self.contract)
        delivery = ContractDocumentDelivery.objects.filter(contract=self.contract).first()
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.delivery_status, ContractDocumentDelivery.STATUS_SKIPPED)
        self.assertTrue(len(delivery.error_message) > 0)

    def test_invalid_phone_skips_delivery(self):
        self.contract.customer_phone = "not-a-phone"
        self.contract.save(update_fields=["customer_phone", "updated_at"])
        from services.contracts.completion import handle_contract_completion
        handle_contract_completion(self.contract)
        delivery = ContractDocumentDelivery.objects.filter(contract=self.contract).first()
        self.assertIsNotNone(delivery)
        self.assertIn(delivery.delivery_status, [
            ContractDocumentDelivery.STATUS_SKIPPED,
            ContractDocumentDelivery.STATUS_FAILED,
        ])


class PhoneNormalizationTests(TestCase):

    def _normalize(self, phone):
        from services.messaging.phone import normalize_malawi_phone
        return normalize_malawi_phone(phone)

    def _try_normalize(self, phone):
        from services.messaging.phone import try_normalize_malawi_phone
        return try_normalize_malawi_phone(phone)

    def test_local_099_prefix(self):
        self.assertEqual(self._normalize("0991234567"), "+265991234567")

    def test_local_088_prefix(self):
        self.assertEqual(self._normalize("0881234567"), "+265881234567")

    def test_local_098_prefix(self):
        self.assertEqual(self._normalize("0981234567"), "+265981234567")

    def test_265_prefix_without_plus(self):
        self.assertEqual(self._normalize("265991234567"), "+265991234567")

    def test_e164_already_normalized(self):
        self.assertEqual(self._normalize("+265991234567"), "+265991234567")

    def test_nine_digit_format(self):
        self.assertEqual(self._normalize("991234567"), "+265991234567")

    def test_empty_raises(self):
        from services.messaging.phone import normalize_malawi_phone
        with self.assertRaises(ValueError):
            normalize_malawi_phone("")

    def test_invalid_raises(self):
        from services.messaging.phone import normalize_malawi_phone
        with self.assertRaises(ValueError):
            normalize_malawi_phone("not-a-number")

    def test_try_normalize_returns_error_on_invalid(self):
        normalized, error = self._try_normalize("bad")
        self.assertEqual(normalized, "")
        self.assertNotEqual(error, "")

    def test_try_normalize_succeeds_on_valid(self):
        normalized, error = self._try_normalize("+265991234567")
        self.assertEqual(normalized, "+265991234567")
        self.assertEqual(error, "")


class SendScheduledContractDocumentsTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="cmd_merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="Samsung")
        self.deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="Galaxy A05s",
            specs="4+128",
            min_cash_price=Decimal("350000.00"),
            max_cash_price=Decimal("400000.00"),
            default_cash_price=Decimal("370000.00"),
            cash_price=Decimal("370000.00"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("925000.00"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="contract_complete",
            customer_name="Bob Mkwanda",
            customer_phone="995678901",
            national_id="EFGH5678",
            deal=self.deal,
            selected_cash_price=Decimal("370000.00"),
            calculated_total_loan=Decimal("925000.00"),
            calculated_deposit_amount=Decimal("120250.00"),
            calculated_monthly_payment=Decimal("77083.33"),
            calculated_daily_payment=Decimal("2569.44"),
        )
        self.contract, _ = Contract.from_application(self.app)
        self.contract.status = Contract.STATUS_COMPLETE
        self.contract.completed_at = timezone.now() - timedelta(hours=25)
        self.contract.save(update_fields=["status", "completed_at", "updated_at"])

    def _make_delivery(self, **kwargs):
        defaults = dict(
            contract=self.contract,
            customer_phone="0995678901",
            normalized_customer_phone="+265995678901",
            channel=ContractDocumentDelivery.CHANNEL_WHATSAPP,
            delivery_status=ContractDocumentDelivery.STATUS_SCHEDULED,
            scheduled_for=timezone.now() - timedelta(minutes=5),
            message_text="Test message",
            provider=ContractDocumentDelivery.PROVIDER_MOCK,
        )
        defaults.update(kwargs)
        return ContractDocumentDelivery.objects.create(**defaults)

    def test_dry_run_does_not_send(self):
        delivery = self._make_delivery()
        from django.core.management import call_command
        call_command("send_scheduled_contract_documents", "--dry-run", "--contract-id", str(self.contract.pk))
        delivery.refresh_from_db()
        self.assertEqual(delivery.delivery_status, ContractDocumentDelivery.STATUS_SCHEDULED)

    def test_forced_send_uses_mock_provider(self):
        delivery = self._make_delivery()
        from django.core.management import call_command
        call_command(
            "send_scheduled_contract_documents",
            "--contract-id", str(self.contract.pk),
            "--force",
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.delivery_status, ContractDocumentDelivery.STATUS_SENT)
        self.assertIsNotNone(delivery.sent_at)
        self.assertTrue(delivery.provider_message_id.startswith("MOCK-"))

    def test_sent_delivery_records_sent_at_and_message_id(self):
        delivery = self._make_delivery()
        from django.core.management import call_command
        call_command(
            "send_scheduled_contract_documents",
            "--contract-id", str(self.contract.pk),
            "--force",
        )
        delivery.refresh_from_db()
        self.assertIsNotNone(delivery.sent_at)
        self.assertNotEqual(delivery.provider_message_id, "")

    def test_delivery_not_due_skipped_without_force(self):
        delivery = self._make_delivery(scheduled_for=timezone.now() + timedelta(hours=12))
        from django.core.management import call_command
        call_command(
            "send_scheduled_contract_documents",
            "--contract-id", str(self.contract.pk),
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.delivery_status, ContractDocumentDelivery.STATUS_SCHEDULED)


class MockProviderTests(TestCase):

    def test_mock_always_succeeds(self):
        from services.messaging.providers.mock import MockWhatsAppProvider
        provider = MockWhatsAppProvider()
        result = provider.send_whatsapp_document(
            to_phone="+265991234567",
            document_url="http://example.com/doc.pdf",
            filename="contract.pdf",
            message="Hello",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "sent")
        self.assertTrue(result["provider_message_id"].startswith("MOCK-"))

    def test_mock_records_recipient(self):
        from services.messaging.providers.mock import MockWhatsAppProvider
        provider = MockWhatsAppProvider()
        result = provider.send_whatsapp_document("+265991234567", "http://x.com/a.pdf", "a.pdf", "msg")
        self.assertEqual(result["raw_response"]["to"], "+265991234567")

    def test_twilio_provider_fails_gracefully_without_credentials(self):
        from services.messaging.providers.twilio_whatsapp import TwilioWhatsAppProvider
        provider = TwilioWhatsAppProvider()
        provider.api_key = ""
        provider.api_secret = ""
        result = provider.send_whatsapp_document("+265991234567", "http://x.com/a.pdf", "a.pdf", "msg")
        self.assertFalse(result["success"])
        self.assertIn("credentials", result["error"])

    def test_meta_provider_fails_gracefully_without_credentials(self):
        from services.messaging.providers.meta_whatsapp import MetaWhatsAppProvider
        provider = MetaWhatsAppProvider()
        provider.access_token = ""
        provider.phone_number_id = ""
        result = provider.send_whatsapp_document("+265991234567", "http://x.com/a.pdf", "a.pdf", "msg")
        self.assertFalse(result["success"])
        self.assertIn("credentials", result["error"])


class ContractModelTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="cm2_merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="Nokia")
        self.deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="G21",
            specs="4+64",
            min_cash_price=Decimal("180000.00"),
            max_cash_price=Decimal("220000.00"),
            default_cash_price=Decimal("200000.00"),
            cash_price=Decimal("200000.00"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("500000.00"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="approved",
            customer_name="Carol Tembo",
            customer_phone="881234567",
            national_id="IJKL9012",
            deal=self.deal,
            selected_cash_price=Decimal("200000.00"),
            calculated_total_loan=Decimal("500000.00"),
            calculated_deposit_amount=Decimal("65000.00"),
            calculated_monthly_payment=Decimal("41666.67"),
            calculated_daily_payment=Decimal("1388.89"),
        )
        self.contract, _ = Contract.from_application(self.app)

    def test_contract_has_initial_pdf_field(self):
        self.assertIsNone(self.contract.initial_pdf.name)

    def test_contract_has_completed_pdf_field(self):
        self.assertIsNone(self.contract.completed_pdf.name)

    def test_contract_has_terms_accepted_at_field(self):
        self.assertIsNone(self.contract.terms_accepted_at)

    def test_contract_has_completed_at_field(self):
        self.assertIsNone(self.contract.completed_at)

    def test_contract_has_ownership_transfer_at_field(self):
        self.assertIsNone(self.contract.ownership_transfer_at)

    def test_finance_charges_property(self):
        expected = Decimal("500000.00") - Decimal("200000.00")
        self.assertEqual(self.contract.finance_charges, expected)

    def test_amount_financed_property(self):
        expected = Decimal("500000.00") - Decimal("65000.00")
        self.assertEqual(self.contract.amount_financed, expected)

    def test_completed_pdf_contains_completion_stamp_fields(self):
        self.contract.completed_at = timezone.now()
        self.contract.save(update_fields=["completed_at", "updated_at"])
        from services.contracts.pdf_contracts import get_contract_context, _build_stamp_context
        ctx = get_contract_context(self.contract)
        self.assertIn("contract_number", ctx)
        self.assertIn("customer_full_name", ctx)
        self.assertIn("imei_number", ctx)
        self.assertIn("total_contract_price", ctx)
        stamp = _build_stamp_context(self.contract)
        self.assertIn("stamp_contract_number", stamp)
        self.assertIn("stamp_completed_at", stamp)
        self.assertIn("stamp_ownership_transfer", stamp)
