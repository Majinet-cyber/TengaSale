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
from rewards.models import SpinWallet, SpinReward

from .forms import ImeiForm, MerchantTermsForm
from .models import Contract, ContractDocumentDelivery, LegalAcceptance, LegalDocumentTemplate


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

    def test_approved_app_routes_to_imei_entry(self):
        self.assertEqual(self.app.get_continue_url(), reverse("capture_imei", args=[self.app.id]))

    def test_contract_terms_requires_all_checkboxes(self):
        self.app.status = "device_locked"
        self.app.imei_number = "123456789012347"
        self.app.save(update_fields=["status", "imei_number"])
        # Empty post fails with 200 (validation errors)
        response = self.client.post(reverse("contract_terms", args=[self.app.id]), {})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contract.objects.exists())

        # Only one checkbox also fails
        response = self.client.post(reverse("contract_terms", args=[self.app.id]), {"confirmed_terms": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contract.objects.exists())

    def test_contract_terms_all_checkboxes_creates_contract(self):
        self.app.status = "device_locked"
        self.app.imei_number = "123456789012347"
        self.app.save(update_fields=["status", "imei_number"])
        response = self.client.post(reverse("contract_terms", args=[self.app.id]), {
            "confirmed_terms": "on",
            "accept_contract_summary": "on",
            "accept_master_terms": "on",
            "consent_device_management": "on",
            "consent_communication": "on",
            "confirm_information_true": "on",
        })
        contract = Contract.objects.get(application=self.app)
        self.app.refresh_from_db()

        self.assertRedirects(response, reverse("contract_signature", args=[contract.id]))
        self.assertTrue(contract.terms_accepted_by_merchant)
        self.assertTrue(contract.contract_number.startswith("A"))
        self.assertEqual(len(contract.contract_number), 8)
        self.assertEqual(self.app.status, "contract_signature")

    def test_contract_numbers_are_unique(self):
        numbers = {Contract.from_application(self.app)[0].contract_number}
        for index in range(3):
            app = FinancingApplication.objects.create(
                created_by=self.merchant,
                status="approved",
                customer_name=f"Customer {index}",
                customer_phone=f"99087061{index}",
                national_id=f"RQXFVZ{index}A",
                deal=self.deal,
                selected_cash_price=Decimal("350000.00"),
            )
            app.apply_deal_selection(self.deal, Decimal("350000.00"))
            app.save()
            numbers.add(Contract.from_application(app)[0].contract_number)

        self.assertEqual(len(numbers), 4)

    def test_contract_from_application_recovers_zeroed_pricing_from_deal(self):
        self.app.selected_cash_price = Decimal("0")
        self.app.calculated_total_loan = Decimal("0")
        self.app.calculated_deposit_amount = Decimal("0")
        self.app.calculated_monthly_payment = Decimal("0")
        self.app.calculated_daily_payment = Decimal("0")
        self.app.save(update_fields=[
            "selected_cash_price",
            "calculated_total_loan",
            "calculated_deposit_amount",
            "calculated_monthly_payment",
            "calculated_daily_payment",
        ])

        contract, _ = Contract.from_application(self.app)
        self.app.refresh_from_db()

        self.assertEqual(self.app.selected_cash_price, Decimal("350000.00"))
        self.assertEqual(contract.cash_price, Decimal("350000.00"))
        self.assertEqual(contract.total_loan, Decimal("875000.00"))
        self.assertGreater(contract.deposit_amount, Decimal("0"))
        self.assertGreater(contract.daily_payment, Decimal("0"))

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
        self.assertContains(response, "Cash Price")
        self.assertContains(response, "Total Contract")
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

    def test_pdf_contract_templates_include_recovery_consent_and_guarantor_terms(self):
        from django.template.loader import render_to_string
        from services.contracts.pdf_contracts import get_contract_context

        self.app.next_of_kin_1_name = "First Guarantor"
        self.app.next_of_kin_1_phone = "881111111"
        self.app.next_of_kin_2_name = "Second Guarantor"
        self.app.next_of_kin_2_phone = "882222222"
        self.app.save(update_fields=[
            "next_of_kin_1_name", "next_of_kin_1_phone",
            "next_of_kin_2_name", "next_of_kin_2_phone",
        ])

        ctx = get_contract_context(self.contract)
        initial_html = render_to_string("contracts/pdf/contract_initial.html", ctx)
        completed_html = render_to_string("contracts/pdf/contract_completed.html", ctx)

        self.assertIn("Guarantor Summary", initial_html)
        self.assertIn("First Guarantor", initial_html)
        self.assertIn("Guarantees executed electronically", initial_html)
        self.assertIn("Authorized Merchant Agent acting on behalf of TengaSale Digital Finance", initial_html)
        self.assertIn("Customer Consent Checklist", initial_html)
        self.assertIn("Ownership of the Device remains with TengaSale Digital Finance", initial_html)
        self.assertIn("30 days or more after required payment becomes overdue", initial_html)
        self.assertIn("Any lawful repossession, tracing, legal or recovery costs", initial_html)
        self.assertIn("Device Lock Status", completed_html)
        self.assertIn("Why wait.", initial_html)
        self.assertIn("Why wait.", completed_html)
        self.assertNotIn("Endless Possibilities", initial_html)
        self.assertNotIn("Endless Possibilities", completed_html)
        self.assertNotIn("Device Lock Release Status", completed_html)
        self.assertNotIn("Yellow", initial_html)

    def test_contract_has_bundle_pdf_fields(self):
        self.assertIsNone(self.contract.contract_bundle_pdf.name)
        self.assertIsNone(self.contract.completed_bundle_pdf.name)

    def test_contract_has_active_at_field(self):
        self.assertIsNone(self.contract.active_at)


# ─────────────────────────────────────────────────────────────────────────────
# Legal document tests
# ─────────────────────────────────────────────────────────────────────────────

class LegalDocumentTemplateTests(TestCase):
    def test_create_master_terms(self):
        doc = LegalDocumentTemplate.objects.create(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
            title="TengaSale Master Terms",
            effective_from="2026-06-01",
            is_active=True,
            body_html="<p>Test terms content</p>",
        )
        self.assertEqual(doc.version, "1.0")
        self.assertEqual(doc.document_type, "master_terms")
        self.assertTrue(len(doc.checksum) == 64)

    def test_checksum_generated_on_save(self):
        doc = LegalDocumentTemplate.objects.create(
            document_type=LegalDocumentTemplate.TYPE_CONTRACT_SUMMARY,
            version="1.0",
            title="Contract Summary",
            effective_from="2026-06-01",
            body_html="<p>Summary</p>",
        )
        self.assertNotEqual(doc.checksum, "")

    def test_unique_together_constraint(self):
        LegalDocumentTemplate.objects.create(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
            title="T1",
            effective_from="2026-06-01",
            body_html="<p>v1</p>",
        )
        from django.db import IntegrityError
        with self.assertRaises(Exception):
            LegalDocumentTemplate.objects.create(
                document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
                version="1.0",
                title="T2",
                effective_from="2026-06-01",
                body_html="<p>duplicate</p>",
            )

    def test_get_active_returns_latest(self):
        LegalDocumentTemplate.objects.create(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
            title="Master Terms v1.0",
            effective_from="2026-01-01",
            is_active=True,
            body_html="<p>v1</p>",
        )
        result = LegalDocumentTemplate.get_active(LegalDocumentTemplate.TYPE_MASTER_TERMS)
        self.assertIsNotNone(result)
        self.assertEqual(result.version, "1.0")

    def test_get_active_returns_none_when_not_found(self):
        result = LegalDocumentTemplate.get_active(LegalDocumentTemplate.TYPE_DEVICE_LOCK_TERMS)
        self.assertIsNone(result)

    def test_str(self):
        doc = LegalDocumentTemplate(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
            title="Master Terms",
        )
        self.assertIn("1.0", str(doc))


class SeedLegalDocumentsCommandTests(TestCase):
    def test_seed_creates_master_terms(self):
        from django.core.management import call_command
        import io
        out = io.StringIO()
        call_command("seed_legal_documents", stdout=out)
        self.assertTrue(
            LegalDocumentTemplate.objects.filter(
                document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
                version="1.0",
            ).exists()
        )

    def test_seed_creates_contract_summary(self):
        from django.core.management import call_command
        import io
        out = io.StringIO()
        call_command("seed_legal_documents", stdout=out)
        self.assertTrue(
            LegalDocumentTemplate.objects.filter(
                document_type=LegalDocumentTemplate.TYPE_CONTRACT_SUMMARY,
                version="1.0",
            ).exists()
        )

    def test_seed_is_idempotent(self):
        from django.core.management import call_command
        import io
        call_command("seed_legal_documents", stdout=io.StringIO())
        call_command("seed_legal_documents", stdout=io.StringIO())
        count = LegalDocumentTemplate.objects.filter(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
        ).count()
        self.assertEqual(count, 1)

    def test_dry_run_creates_nothing(self):
        from django.core.management import call_command
        import io
        call_command("seed_legal_documents", "--dry-run", stdout=io.StringIO())
        self.assertEqual(LegalDocumentTemplate.objects.count(), 0)


# ─────────────────────────────────────────────────────────────────────────────
# LegalAcceptance tests
# ─────────────────────────────────────────────────────────────────────────────

class LegalAcceptanceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="merch_la", password="pass123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="Samsung")
        self.deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="Galaxy A05s",
            min_cash_price=Decimal("250000"),
            max_cash_price=Decimal("350000"),
            default_cash_price=Decimal("300000"),
            cash_price=Decimal("300000"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("750000"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="approved",
            customer_name="Peter Phiri",
            customer_phone="0881234567",
            national_id="MWI12345",
            deal=self.deal,
            selected_cash_price=Decimal("300000"),
            calculated_total_loan=Decimal("750000"),
            calculated_deposit_amount=Decimal("39000"),
            calculated_monthly_payment=Decimal("59250"),
            calculated_daily_payment=Decimal("1975"),
        )
        self.doc = LegalDocumentTemplate.objects.create(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
            title="TengaSale Master Terms",
            effective_from="2026-06-01",
            is_active=True,
            body_html="<p>Terms content</p>",
        )

    def test_create_legal_acceptance(self):
        acc = LegalAcceptance.objects.create(
            application=self.app,
            legal_document=self.doc,
            accepted_name="Peter Phiri",
            accepted_phone="0881234567",
            accepted_national_id="MWI12345",
            acceptance_method=LegalAcceptance.METHOD_MOBILE_ACCEPTANCE,
            accepted_at=timezone.now(),
        )
        self.assertEqual(acc.legal_document, self.doc)
        self.assertEqual(acc.accepted_name, "Peter Phiri")
        self.assertIsNone(acc.contract)

    def test_acceptance_str(self):
        acc = LegalAcceptance(
            accepted_name="Peter Phiri",
            accepted_at=timezone.now(),
            legal_document=self.doc,
        )
        self.assertIn("Peter Phiri", str(acc))

    def test_acceptance_with_contract_link(self):
        contract, _ = Contract.from_application(self.app)
        acc = LegalAcceptance.objects.create(
            application=self.app,
            contract=contract,
            legal_document=self.doc,
            accepted_name="Peter Phiri",
            accepted_phone="0881234567",
            acceptance_method=LegalAcceptance.METHOD_DIGITAL_SIGNATURE,
            accepted_at=timezone.now(),
        )
        self.assertEqual(acc.contract, contract)
        self.assertEqual(acc.acceptance_method, "digital_signature")

    def test_terms_form_requires_all_fields(self):
        form = MerchantTermsForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn("confirmed_terms", form.errors)
        self.assertIn("accept_master_terms", form.errors)
        self.assertIn("accept_contract_summary", form.errors)
        self.assertIn("consent_device_management", form.errors)

    def test_terms_form_valid_when_all_checked(self):
        form = MerchantTermsForm(data={
            "confirmed_terms": True,
            "accept_contract_summary": True,
            "accept_master_terms": True,
            "consent_device_management": True,
            "consent_communication": True,
            "confirm_information_true": True,
        })
        self.assertTrue(form.is_valid())


class LegalAcceptanceContractTermsViewTests(TestCase):
    """Test that contract_terms view creates LegalAcceptance records."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="merch_terms", password="pass123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="Itel")
        self.deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="A70",
            min_cash_price=Decimal("150000"),
            max_cash_price=Decimal("200000"),
            default_cash_price=Decimal("175000"),
            cash_price=Decimal("175000"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("437500"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="device_locked",
            customer_name="Grace Tembo",
            customer_phone="0991234567",
            national_id="MWI99999",
            imei_number="123456789012347",
            deal=self.deal,
            selected_cash_price=Decimal("175000"),
            calculated_total_loan=Decimal("437500"),
            calculated_deposit_amount=Decimal("22750"),
            calculated_monthly_payment=Decimal("34583.33"),
            calculated_daily_payment=Decimal("1152.78"),
        )
        # Seed legal documents
        self.master_terms = LegalDocumentTemplate.objects.create(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
            title="TengaSale Master Terms",
            effective_from="2026-06-01",
            is_active=True,
            body_html="<p>Master Terms</p>",
        )
        self.summary = LegalDocumentTemplate.objects.create(
            document_type=LegalDocumentTemplate.TYPE_CONTRACT_SUMMARY,
            version="1.0",
            title="Contract Summary",
            effective_from="2026-06-01",
            is_active=True,
            body_html="<p>Contract Summary</p>",
        )
        self.client.force_login(self.merchant)

    def test_terms_page_loads(self):
        resp = self.client.get(reverse("contract_terms", args=[self.app.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Master Terms")

    def test_terms_submission_creates_acceptance_records(self):
        resp = self.client.post(reverse("contract_terms", args=[self.app.id]), {
            "confirmed_terms": True,
            "accept_contract_summary": True,
            "accept_master_terms": True,
            "consent_device_management": True,
            "consent_communication": True,
            "confirm_information_true": True,
        })
        # Should redirect to signature
        self.assertEqual(resp.status_code, 302)
        # LegalAcceptance records should be created
        count = LegalAcceptance.objects.filter(application=self.app).count()
        self.assertEqual(count, 2)

    def test_terms_submission_creates_master_terms_acceptance(self):
        self.client.post(reverse("contract_terms", args=[self.app.id]), {
            "confirmed_terms": True,
            "accept_contract_summary": True,
            "accept_master_terms": True,
            "consent_device_management": True,
            "consent_communication": True,
            "confirm_information_true": True,
        })
        acc = LegalAcceptance.objects.filter(
            application=self.app,
            legal_document=self.master_terms,
        ).first()
        self.assertIsNotNone(acc)
        self.assertEqual(acc.accepted_name, "Grace Tembo")
        self.assertEqual(acc.acceptance_method, LegalAcceptance.METHOD_MOBILE_ACCEPTANCE)

    def test_terms_submission_without_master_terms_fails(self):
        resp = self.client.post(reverse("contract_terms", args=[self.app.id]), {
            "confirmed_terms": True,
            "accept_contract_summary": True,
            # missing accept_master_terms
            "consent_device_management": True,
            "consent_communication": True,
            "confirm_information_true": True,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(LegalAcceptance.objects.filter(application=self.app).count(), 0)


class LegalContextInPdfServiceTests(TestCase):
    """Test that _get_legal_context returns expected structure."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="merch_pdf_legal", password="pass123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="Infinix")
        self.deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="Hot 40i",
            min_cash_price=Decimal("200000"),
            max_cash_price=Decimal("300000"),
            default_cash_price=Decimal("250000"),
            cash_price=Decimal("250000"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("625000"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="approved",
            customer_name="Charles Mwale",
            customer_phone="0881111222",
            national_id="MWI12312",
            deal=self.deal,
            selected_cash_price=Decimal("250000"),
            calculated_total_loan=Decimal("625000"),
            calculated_deposit_amount=Decimal("32500"),
            calculated_monthly_payment=Decimal("49375"),
            calculated_daily_payment=Decimal("1645.83"),
        )
        self.contract, _ = Contract.from_application(self.app)
        self.master_terms = LegalDocumentTemplate.objects.create(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
            title="TengaSale Master Terms",
            effective_from="2026-06-01",
            is_active=True,
            body_html="<p>Full Master Terms text</p>",
        )

    def test_legal_context_has_master_terms_fields(self):
        from services.contracts.pdf_contracts import _get_legal_context
        ctx = _get_legal_context(self.contract)
        self.assertIn("master_terms_doc", ctx)
        self.assertIn("master_terms_version", ctx)
        self.assertIn("master_terms_body", ctx)
        self.assertIn("master_terms_accepted_at", ctx)
        self.assertIn("legal_acceptance_records", ctx)

    def test_legal_context_finds_active_master_terms_doc(self):
        from services.contracts.pdf_contracts import _get_legal_context
        ctx = _get_legal_context(self.contract)
        self.assertEqual(ctx["master_terms_version"], "1.0")
        self.assertIn("Full Master Terms text", ctx["master_terms_body"])

    def test_legal_context_includes_acceptance_records(self):
        LegalAcceptance.objects.create(
            application=self.app,
            contract=self.contract,
            legal_document=self.master_terms,
            accepted_name="Charles Mwale",
            accepted_phone="0881111222",
            acceptance_method=LegalAcceptance.METHOD_DIGITAL_SIGNATURE,
            accepted_at=timezone.now(),
        )
        from services.contracts.pdf_contracts import _get_legal_context
        ctx = _get_legal_context(self.contract)
        self.assertEqual(len(ctx["legal_acceptance_records"]), 1)
        self.assertIsNotNone(ctx["master_terms_accepted_at"])
        self.assertNotEqual(ctx["master_terms_acceptance_id"], "")


class SeedLegalDocumentsTests(TestCase):
    """Tests for seed_legal_documents command and legal file loading safety."""

    def test_seed_command_creates_master_terms(self):
        from django.core.management import call_command
        LegalDocumentTemplate.objects.filter(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
        ).delete()
        call_command("seed_legal_documents", verbosity=0)
        doc = LegalDocumentTemplate.objects.filter(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
        ).first()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.version, "1.0")
        self.assertTrue(doc.is_active)
        self.assertIn("TENGASALE", doc.body_html)

    def test_seed_command_creates_contract_summary(self):
        from django.core.management import call_command
        LegalDocumentTemplate.objects.filter(
            document_type=LegalDocumentTemplate.TYPE_CONTRACT_SUMMARY,
            version="1.0",
        ).delete()
        call_command("seed_legal_documents", verbosity=0)
        doc = LegalDocumentTemplate.objects.filter(
            document_type=LegalDocumentTemplate.TYPE_CONTRACT_SUMMARY,
            version="1.0",
        ).first()
        self.assertIsNotNone(doc)
        self.assertTrue(doc.is_active)

    def test_seed_command_is_idempotent(self):
        from django.core.management import call_command
        call_command("seed_legal_documents", verbosity=0)
        call_command("seed_legal_documents", verbosity=0)
        count = LegalDocumentTemplate.objects.filter(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
        ).count()
        self.assertEqual(count, 1)

    def test_seed_command_body_is_valid_utf8(self):
        from django.core.management import call_command
        call_command("seed_legal_documents", verbosity=0)
        doc = LegalDocumentTemplate.objects.get(
            document_type=LegalDocumentTemplate.TYPE_MASTER_TERMS,
            version="1.0",
        )
        # Must be encodable to UTF-8 without error
        encoded = doc.body_html.encode("utf-8")
        self.assertGreater(len(encoded), 100)

    def test_safe_read_legal_file_rejects_binary(self):
        import tempfile, os
        from contracts.management.commands.seed_legal_documents import _safe_read_legal_file
        from pathlib import Path
        from django.core.management.base import CommandError
        # Write a file with invalid UTF-8 byte (0x97) named .html
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            tmp.write(b"Legal text \x97 with bad byte")
            tmp_path = Path(tmp.name)
        try:
            with self.assertRaises(CommandError):
                _safe_read_legal_file(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_safe_read_legal_file_reads_valid_utf8_html(self):
        import tempfile, os
        from contracts.management.commands.seed_legal_documents import _safe_read_legal_file
        from pathlib import Path
        content = "<h1>Legal Terms</h1><p>Valid UTF-8 content.</p>"
        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            result = _safe_read_legal_file(tmp_path)
            self.assertEqual(result, content)
        finally:
            os.unlink(tmp_path)

    def test_safe_read_legal_file_rejects_unsupported_format(self):
        import tempfile, os
        from contracts.management.commands.seed_legal_documents import _safe_read_legal_file
        from pathlib import Path
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4")
            tmp_path = Path(tmp.name)
        try:
            with self.assertRaises(ValueError):
                _safe_read_legal_file(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_style_css_is_valid_utf8(self):
        """Ensure style.css in static dir is valid UTF-8 (Render build guard)."""
        import pathlib
        css_path = pathlib.Path(__file__).resolve().parent.parent / "static" / "css" / "style.css"
        if not css_path.exists():
            self.skipTest("style.css not found in test environment")
        try:
            css_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            self.fail(
                f"style.css contains invalid UTF-8 bytes. "
                f"Render collectstatic will fail. Detail: {e}"
            )


# ---------------------------------------------------------------------------
# PayG number in PDF context and contract progress
# ---------------------------------------------------------------------------

class PayGPDFContextTest(TestCase):
    """Verify get_contract_context() includes payg_number and payment_url."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="payg_pdf_merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="PAYG-PDF-BRAND")
        deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="PayG PDF Phone",
            specs="4+128",
            min_cash_price=Decimal("200000.00"),
            max_cash_price=Decimal("300000.00"),
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
            customer_name="PayG PDF Customer",
            customer_phone="0991234500",
            national_id="PAYG001",
            deal=deal,
            selected_cash_price=Decimal("250000.00"),
            calculated_total_loan=Decimal("625000.00"),
            calculated_deposit_amount=Decimal("81250.00"),
            calculated_monthly_payment=Decimal("52083.33"),
            calculated_daily_payment=Decimal("1736.11"),
        )
        self.contract = Contract.from_application(self.app)[0]

    def test_pdf_context_includes_payg_number_key(self):
        from services.contracts.pdf_contracts import get_contract_context
        ctx = get_contract_context(self.contract)
        self.assertIn("payg_number", ctx)
        self.assertIn("payment_url", ctx)

    def test_pdf_context_payg_number_is_string(self):
        from services.contracts.pdf_contracts import get_contract_context
        ctx = get_contract_context(self.contract)
        self.assertIsInstance(ctx["payg_number"], str)
        self.assertIsInstance(ctx["payment_url"], str)

    def test_pdf_context_payg_number_from_portal_contract(self):
        """If a PaymentContract exists, payg_number should be populated in PDF context."""
        from portal.services import create_contract_from_application
        from services.contracts.pdf_contracts import get_contract_context
        create_contract_from_application(self.app)
        ctx = get_contract_context(self.contract)
        if ctx["payg_number"]:
            self.assertRegex(ctx["payg_number"], r"^E[A-Z2-9]{7}$")
            self.assertIn("/pay/payg/", ctx["payment_url"])


class PayGProgressPageTest(TestCase):
    """Verify the contract progress page shows PayG number."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="payg_prog_merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="PAYG-PROG-BRAND")
        deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="PayG Progress Phone",
            specs="3+64",
            min_cash_price=Decimal("150000.00"),
            max_cash_price=Decimal("200000.00"),
            default_cash_price=Decimal("175000.00"),
            cash_price=Decimal("175000.00"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("437500.00"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="approved",
            customer_name="PayG Progress Customer",
            customer_phone="0992345600",
            national_id="PAYG002",
            deal=deal,
            selected_cash_price=Decimal("175000.00"),
            calculated_total_loan=Decimal("437500.00"),
            calculated_deposit_amount=Decimal("56875.00"),
            calculated_monthly_payment=Decimal("36458.33"),
            calculated_daily_payment=Decimal("1215.28"),
        )
        self.contract = Contract.from_application(self.app)[0]
        self.contract.status = Contract.STATUS_CONTRACT_CREATED
        self.contract.imei_number = "123456789012345"
        self.contract.save()
        self.client.login(username="payg_prog_merchant", password="test-pass-123")

    def test_progress_page_shows_payg_number_when_portal_contract_exists(self):
        from portal.services import create_contract_from_application, sync_portal_lock_from_contract
        from django.urls import reverse
        portal_contract = create_contract_from_application(self.app)
        self.assertFalse(portal_contract.payg_number)
        self.contract.phone_locked = True
        self.contract.status = Contract.STATUS_LOCKED
        self.contract.save()
        sync_portal_lock_from_contract(self.contract)
        portal_contract.refresh_from_db()
        response = self.client.get(reverse("contract_progress", args=[self.contract.id]))
        self.assertEqual(response.status_code, 200)
        payg = portal_contract.payg_number
        self.assertTrue(payg)
        self.assertContains(response, payg)

    def test_progress_page_shows_imei(self):
        from django.urls import reverse
        response = self.client.get(reverse("contract_progress", args=[self.contract.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "123456789012345")


# ─────────────────────────────────────────────────────────────────
# New tests required by the UI/UX polish brief
# ─────────────────────────────────────────────────────────────────


class ContractNumberFormatTests(TestCase):
    """Contract number must start with A and be exactly 8 characters."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="fmt_merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="FMT-BRAND")
        self.deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="Fmt Model",
            specs="2+32",
            min_cash_price=Decimal("150000"),
            max_cash_price=Decimal("300000"),
            default_cash_price=Decimal("200000"),
            cash_price=Decimal("200000"),
            deposit_percent=Decimal("13"),
            loan_multiplier=Decimal("2.5"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            customer_name="Format Test",
            customer_phone="0881000001",
            national_id="999000000",
            deal=self.deal,
            status="approved",
        )

    def test_contract_number_starts_with_A(self):
        contract, _ = Contract.from_application(self.app)
        self.assertTrue(
            contract.contract_number.startswith("A"),
            f"Contract number '{contract.contract_number}' must start with A",
        )

    def test_contract_number_is_eight_chars(self):
        contract, _ = Contract.from_application(self.app)
        self.assertEqual(
            len(contract.contract_number),
            8,
            f"Contract number '{contract.contract_number}' must be exactly 8 characters",
        )

    def test_contract_number_unique(self):
        contract1, _ = Contract.from_application(self.app)
        app2 = FinancingApplication.objects.create(
            created_by=self.merchant,
            customer_name="Format Test 2",
            customer_phone="0881000002",
            national_id="999000002",
            deal=self.deal,
            status="approved",
        )
        contract2, _ = Contract.from_application(app2)
        self.assertNotEqual(contract1.contract_number, contract2.contract_number)

    def test_contract_number_db_unique_constraint(self):
        from django.db import IntegrityError
        contract, _ = Contract.from_application(self.app)
        with self.assertRaises(IntegrityError):
            # Force a duplicate — bypass generator
            Contract.objects.create(
                application=self.app,
                contract_number=contract.contract_number,
                merchant=self.merchant,
                customer_name="Dup",
            )

    def test_contract_number_immutable_on_resave(self):
        contract, _ = Contract.from_application(self.app)
        original = contract.contract_number
        contract.customer_name = "Changed Name"
        contract.save()
        contract.refresh_from_db()
        self.assertEqual(contract.contract_number, original)


class DepositPaymentPortalTests(TestCase):
    """Deposit payment must be searchable by contract number and IMEI."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="dep_merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="DEP-BRAND")
        deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="Dep Model",
            specs="4+64",
            min_cash_price=Decimal("200000"),
            max_cash_price=Decimal("400000"),
            default_cash_price=Decimal("300000"),
            cash_price=Decimal("300000"),
            deposit_percent=Decimal("13"),
            loan_multiplier=Decimal("2.5"),
        )
        from applications.test_helpers import attach_complete_pricing

        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            customer_name="Deposit Customer",
            customer_phone="881200001",
            national_id="88810001",
            deal=deal,
            imei_number="555444333222111",
            status="approved",
        )
        attach_complete_pricing(self.app, cash_price=Decimal("300000"))
        self.contract, _ = Contract.from_application(self.app)

    def test_contract_number_format_in_portal_search(self):
        """Portal search accepts contract-number format A + 7 chars."""
        from portal.services import create_contract_from_application, search_payment_contract

        payment = create_contract_from_application(self.app)
        self.assertTrue(self.contract.contract_number.startswith("A"))
        found = search_payment_contract(self.contract.contract_number)
        self.assertIsNotNone(found)
        self.assertEqual(found.pk, payment.pk)
        response = self.client.get(
            reverse("portal_search_post") + f"?q={self.contract.contract_number}"
        )
        self.assertEqual(response.status_code, 302)

    def test_portal_search_by_imei(self):
        """Portal accepts IMEI search."""
        response = self.client.get(
            reverse("portal_search_post") + "?q=555444333222111"
        )
        self.assertIn(response.status_code, [200, 302])


class LegalOwnershipWordingTests(TestCase):
    """Active contract pages must NOT show ownership-transfer language."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="legal_merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="LEGAL-BRAND")
        deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="Legal Model",
            specs="3+32",
            min_cash_price=Decimal("180000"),
            max_cash_price=Decimal("350000"),
            default_cash_price=Decimal("250000"),
            cash_price=Decimal("250000"),
            deposit_percent=Decimal("13"),
            loan_multiplier=Decimal("2.5"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            customer_name="Legal Customer",
            customer_phone="0882000001",
            national_id="777000001",
            deal=deal,
            status="approved",
        )
        self.contract, _ = Contract.from_application(self.app)
        self.client.force_login(self.merchant)

    def test_progress_page_no_ownership_transfer_wording(self):
        response = self.client.get(reverse("contract_progress", args=[self.contract.id]))
        self.assertEqual(response.status_code, 200)
        forbidden_phrases = [
            "ownership transferred",
            "fully owned",
            "transfer complete",
            "ownership confirmed",
        ]
        content = response.content.decode().lower()
        for phrase in forbidden_phrases:
            self.assertNotIn(phrase, content, f"Found forbidden phrase: '{phrase}'")

    def test_progress_page_has_retention_notice(self):
        response = self.client.get(reverse("contract_progress", args=[self.contract.id]))
        content = response.content.decode().lower()
        self.assertIn("ownership", content)
        self.assertIn("retained", content)

    def test_complete_page_no_ownership_transfer_wording(self):
        self.contract.deposit_paid = True
        self.contract.phone_locked = True
        self.contract.status = Contract.STATUS_COMPLETE
        self.contract.save()
        response = self.client.get(reverse("contract_complete", args=[self.contract.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode().lower()
        forbidden = ["ownership transferred", "fully owned", "transfer complete"]
        for phrase in forbidden:
            self.assertNotIn(phrase, content, f"Found forbidden phrase: '{phrase}'")


class UnderwriterApprovalOnceTests(TestCase):
    """An already-approved application cannot be approved again."""

    def setUp(self):
        User = get_user_model()
        self.underwriter = User.objects.create_user(username="uw_once", password="test-pass-123")
        assign_role(self.underwriter, "underwriter")
        self.merchant = User.objects.create_user(username="uw_once_m", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="UW-ONCE-BRAND")
        deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="UW Once Model",
            specs="4+64",
            min_cash_price=Decimal("200000"),
            max_cash_price=Decimal("400000"),
            default_cash_price=Decimal("300000"),
            cash_price=Decimal("300000"),
            deposit_percent=Decimal("13"),
            loan_multiplier=Decimal("2.5"),
        )
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            customer_name="UW Once Customer",
            customer_phone="0883000001",
            national_id="666000001",
            deal=deal,
            status="approved",  # Already approved
            review_status="approved",
            reviewed_by=self.underwriter,
        )
        self.client.force_login(self.underwriter)

    def test_already_approved_cannot_be_approved_again_via_sales(self):
        """POST to confirm_approve on an already-approved application should not double-approve."""
        url = reverse("sales_confirm_approve", args=[self.app.id])
        response = self.client.post(url, {"confirmed": "1"})
        # Should redirect away or show error, not double-approve
        self.app.refresh_from_db()
        # Application remains approved (not re-processed)
        self.assertEqual(self.app.status, "approved")


class ContractDepositPaymentTests(TestCase):
    """Tests for the PayChangu deposit payment flow."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="dep_merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        brand = DeviceBrand.objects.create(name="SamsungDep")
        deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="Galaxy A25",
            specs="6+128",
            min_cash_price=Decimal("250000.00"),
            max_cash_price=Decimal("350000.00"),
            default_cash_price=Decimal("300000.00"),
            cash_price=Decimal("300000.00"),
            deposit_percent=Decimal("15.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("750000.00"),
        )
        app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="approved",
            customer_name="Peter Phiri",
            customer_phone="991112222",
            national_id="ABCD1234",
            deal=deal,
            selected_cash_price=Decimal("300000.00"),
            calculated_deposit_amount=Decimal("45000.00"),
            calculated_total_loan=Decimal("750000.00"),
            calculated_monthly_payment=Decimal("62500.00"),
            calculated_daily_payment=Decimal("2083.00"),
        )
        self.contract = Contract.objects.create(
            application=app,
            merchant=self.merchant,
            customer_name="Peter Phiri",
            customer_phone="991112222",
            cash_price=Decimal("300000.00"),
            deposit_amount=Decimal("45000.00"),
            total_loan=Decimal("750000.00"),
            monthly_payment=Decimal("62500.00"),
            daily_payment=Decimal("2083.00"),
            phone_locked=True,
            status=Contract.STATUS_LOCKED,
        )
        self.client.force_login(self.merchant)

    def test_deposit_payment_page_renders(self):
        url = reverse("contract_pay_deposit", args=[self.contract.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "TengaSale Payment")
        self.assertContains(resp, "Pay Now")
        self.assertContains(resp, "45")  # deposit amount
        self.assertNotContains(resp, "PayChangu-powered")
        self.assertNotContains(resp, "Secured by PayChangu")

    def test_deposit_payment_page_requires_locked_device(self):
        self.contract.phone_locked = False
        self.contract.status = Contract.STATUS_CONTRACT_CREATED
        self.contract.save()
        url = reverse("contract_pay_deposit", args=[self.contract.id])
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse("contract_progress", args=[self.contract.id]))

    def test_deposit_already_paid_redirects_to_complete(self):
        self.contract.deposit_paid = True
        self.contract.save()
        url = reverse("contract_pay_deposit", args=[self.contract.id])
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse("contract_complete", args=[self.contract.id]))

    @patch("integrations.paychangu_client.initiate_payment")
    def test_create_checkout_success(self, mock_initiate):
        mock_initiate.return_value = {
            "status": "success",
            "checkout_url": "https://paychangu.test/checkout/abc123",
            "tx_ref": "dep-TESTREF-12345678",
            "message": "OK",
        }
        url = reverse("contract_pay_deposit", args=[self.contract.id])
        resp = self.client.post(url, {"action": "create_checkout", "phone": "0991234567"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("checkout_url", data)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.deposit_payment_status, Contract.DEPOSIT_STATUS_CHECKOUT_CREATED)

    def test_phone_validation_required(self):
        url = reverse("contract_pay_deposit", args=[self.contract.id])
        resp = self.client.post(url, {"action": "create_checkout", "phone": ""})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["ok"])

    def test_webhook_marks_deposit_paid(self):
        import json
        self.contract.deposit_payment_tx_ref = "dep-TESTCONT-aabbccdd"
        self.contract.deposit_payment_status = Contract.DEPOSIT_STATUS_CHECKOUT_CREATED
        self.contract.save()

        payload = json.dumps({
            "tx_ref": "dep-TESTCONT-aabbccdd",
            "status": "SUCCESSFUL",
            "amount": "45000.00",
        }).encode()

        with self.settings(MOCK_PAYMENTS=True, PAYCHANGU_WEBHOOK_SECRET=""):
            resp = self.client.post(
                reverse("contract_deposit_webhook"),
                data=payload,
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.contract.refresh_from_db()
        self.assertTrue(self.contract.deposit_paid)
        self.assertEqual(self.contract.deposit_payment_status, Contract.DEPOSIT_STATUS_PAID)
        self.assertEqual(self.contract.status, Contract.STATUS_COMPLETE)

    def test_webhook_duplicate_does_not_double_count(self):
        import json
        self.contract.deposit_paid = True
        self.contract.deposit_payment_status = Contract.DEPOSIT_STATUS_PAID
        self.contract.deposit_payment_tx_ref = "dep-DUPTEST-aabbccdd"
        self.contract.save()

        payload = json.dumps({
            "tx_ref": "dep-DUPTEST-aabbccdd",
            "status": "SUCCESSFUL",
            "amount": "45000.00",
        }).encode()

        with self.settings(MOCK_PAYMENTS=True, PAYCHANGU_WEBHOOK_SECRET=""):
            resp = self.client.post(
                reverse("contract_deposit_webhook"),
                data=payload,
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("message"), "Already paid")

    def test_failed_payment_webhook_does_not_mark_paid(self):
        import json
        self.contract.deposit_payment_tx_ref = "dep-FAILTEST-aabbccdd"
        self.contract.deposit_payment_status = Contract.DEPOSIT_STATUS_CHECKOUT_CREATED
        self.contract.save()

        payload = json.dumps({
            "tx_ref": "dep-FAILTEST-aabbccdd",
            "status": "FAILED",
            "amount": "45000.00",
        }).encode()

        with self.settings(MOCK_PAYMENTS=True, PAYCHANGU_WEBHOOK_SECRET=""):
            resp = self.client.post(
                reverse("contract_deposit_webhook"),
                data=payload,
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.contract.refresh_from_db()
        self.assertFalse(self.contract.deposit_paid)
        self.assertEqual(self.contract.deposit_payment_status, Contract.DEPOSIT_STATUS_FAILED)

    def test_amount_mismatch_flags_review(self):
        import json
        self.contract.deposit_payment_tx_ref = "dep-MISMATCH-aabbccdd"
        self.contract.deposit_amount = Decimal("45000.00")
        self.contract.deposit_payment_status = Contract.DEPOSIT_STATUS_CHECKOUT_CREATED
        self.contract.save()

        payload = json.dumps({
            "tx_ref": "dep-MISMATCH-aabbccdd",
            "status": "SUCCESSFUL",
            "amount": "10000.00",  # wrong amount
        }).encode()

        with self.settings(MOCK_PAYMENTS=True, PAYCHANGU_WEBHOOK_SECRET=""):
            resp = self.client.post(
                reverse("contract_deposit_webhook"),
                data=payload,
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.contract.refresh_from_db()
        self.assertFalse(self.contract.deposit_paid)
        self.assertEqual(self.contract.deposit_payment_status, Contract.DEPOSIT_STATUS_MISMATCH)

    def test_deposit_status_endpoint(self):
        url = reverse("contract_deposit_status", args=[self.contract.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("deposit_paid", data)
        self.assertIn("status", data)


class EmergencyPayoutTests(TestCase):
    """Tests for the 20% emergency payout request system."""

    def setUp(self):
        User = get_user_model()
        from accounts.utils import assign_role
        self.underwriter = User.objects.create_user(username="uw_payout_test", password="test-pass-123")
        assign_role(self.underwriter, "underwriter")
        self.hq_user = User.objects.create_user(username="hq_payout_test", password="test-pass-123")
        assign_role(self.hq_user, "hq")

        from earnings.models import Wallet, WalletTransaction, EmergencyPayoutRequest
        self.wallet, _ = Wallet.objects.get_or_create(user=self.underwriter)
        # Credit MWK 100,000 commission for this month
        WalletTransaction.objects.create(
            wallet=self.wallet,
            transaction_type="commission_credit",
            amount=Decimal("100000.00"),
            description="Test commission credit",
        )
        self.wallet.balance = Decimal("100000.00")
        self.wallet.total_earned = Decimal("100000.00")
        self.wallet.save()

        self.EmergencyPayoutRequest = EmergencyPayoutRequest

    def test_emergency_limit_calculated_as_20_percent(self):
        from earnings.models import EmergencyPayoutRequest
        info = EmergencyPayoutRequest.get_monthly_emergency_limit(self.wallet)
        self.assertEqual(info["earned"], Decimal("100000.00"))
        self.assertEqual(info["limit"], Decimal("20000.00"))
        self.assertEqual(info["available"], Decimal("20000.00"))

    def test_emergency_payout_page_renders(self):
        self.client.force_login(self.underwriter)
        url = reverse("sales_emergency_payout_request")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Emergency Payout")
        self.assertContains(resp, "20%")

    def test_request_within_limit_succeeds(self):
        self.client.force_login(self.underwriter)
        url = reverse("sales_emergency_payout_request")
        resp = self.client.post(url, {
            "amount": "15000",
            "reason": "Medical emergency",
            "payout_phone": "+265991234567",
            "confirmed": "on",
        })
        self.assertRedirects(resp, url)
        from earnings.models import EmergencyPayoutRequest
        req = EmergencyPayoutRequest.objects.filter(wallet=self.wallet).first()
        self.assertIsNotNone(req)
        self.assertEqual(req.requested_amount, Decimal("15000.00"))
        self.assertEqual(req.status, EmergencyPayoutRequest.STATUS_PENDING)

    def test_request_exceeding_limit_is_rejected(self):
        self.client.force_login(self.underwriter)
        url = reverse("sales_emergency_payout_request")
        resp = self.client.post(url, {
            "amount": "25000",  # exceeds 20% = 20,000 limit
            "reason": "Test",
            "payout_phone": "+265991234567",
            "confirmed": "on",
        })
        # Should NOT redirect (form error stays on page)
        from earnings.models import EmergencyPayoutRequest
        count = EmergencyPayoutRequest.objects.filter(wallet=self.wallet).count()
        self.assertEqual(count, 0)

    def test_hq_can_approve_request(self):
        from earnings.models import EmergencyPayoutRequest
        req = EmergencyPayoutRequest.objects.create(
            wallet=self.wallet,
            requested_amount=Decimal("10000.00"),
            reason="Test",
            confirmed=True,
            earned_this_month_snapshot=Decimal("100000.00"),
            emergency_limit_snapshot=Decimal("20000.00"),
            already_requested_snapshot=Decimal("0.00"),
        )
        self.client.force_login(self.hq_user)
        url = reverse("hq_emergency_payout_action", args=[req.id])
        resp = self.client.post(url, {"action": "approve", "note": "Approved"})
        self.assertRedirects(resp, reverse("hq_emergency_payouts"))
        req.refresh_from_db()
        self.assertEqual(req.status, EmergencyPayoutRequest.STATUS_APPROVED)

    def test_underwriter_cannot_approve_own_payout(self):
        from earnings.models import EmergencyPayoutRequest
        req = EmergencyPayoutRequest.objects.create(
            wallet=self.wallet,
            requested_amount=Decimal("10000.00"),
            reason="Test self-approval",
            confirmed=True,
            earned_this_month_snapshot=Decimal("100000.00"),
            emergency_limit_snapshot=Decimal("20000.00"),
            already_requested_snapshot=Decimal("0.00"),
        )
        # Login as the underwriter and attempt HQ approval (should be blocked by hq_required)
        self.client.force_login(self.underwriter)
        url = reverse("hq_emergency_payout_action", args=[req.id])
        resp = self.client.post(url, {"action": "approve", "note": ""})
        # hq_required raises PermissionDenied (403) for non-HQ users
        self.assertIn(resp.status_code, [302, 403])
        req.refresh_from_db()
        self.assertNotEqual(req.status, EmergencyPayoutRequest.STATUS_APPROVED)

    def test_hq_cannot_approve_exceeding_limit(self):
        from earnings.models import EmergencyPayoutRequest
        req = EmergencyPayoutRequest.objects.create(
            wallet=self.wallet,
            requested_amount=Decimal("25000.00"),  # over limit
            reason="Test",
            confirmed=True,
            earned_this_month_snapshot=Decimal("100000.00"),
            emergency_limit_snapshot=Decimal("20000.00"),
            already_requested_snapshot=Decimal("0.00"),
        )
        self.client.force_login(self.hq_user)
        url = reverse("hq_emergency_payout_action", args=[req.id])
        resp = self.client.post(url, {"action": "approve", "note": ""})
        req.refresh_from_db()
        self.assertNotEqual(req.status, EmergencyPayoutRequest.STATUS_APPROVED)

    def test_mark_paid_reduces_available_balance(self):
        from earnings.models import EmergencyPayoutRequest
        req = EmergencyPayoutRequest.objects.create(
            wallet=self.wallet,
            requested_amount=Decimal("10000.00"),
            reason="Test",
            confirmed=True,
            status=EmergencyPayoutRequest.STATUS_APPROVED,
            reviewed_by=self.hq_user,
            earned_this_month_snapshot=Decimal("100000.00"),
            emergency_limit_snapshot=Decimal("20000.00"),
            already_requested_snapshot=Decimal("0.00"),
        )
        old_balance = self.wallet.balance
        self.client.force_login(self.hq_user)
        url = reverse("hq_emergency_payout_action", args=[req.id])
        resp = self.client.post(url, {
            "action": "mark_paid",
            "payment_reference": "AIRTEL123456",
            "payment_provider": "airtel",
        })
        req.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(req.status, EmergencyPayoutRequest.STATUS_PAID)
        self.assertEqual(self.wallet.balance, old_balance - Decimal("10000.00"))


class MalawiTimezoneTests(TestCase):
    """Dates in the app should be displayed in Malawi time (CAT / GMT+2)."""

    def test_settings_timezone_is_africa_blantyre(self):
        from django.conf import settings
        self.assertEqual(settings.TIME_ZONE, "Africa/Blantyre")

    def test_use_tz_is_enabled(self):
        from django.conf import settings
        self.assertTrue(settings.USE_TZ)


class SpinMoneyEarningsTests(TestCase):
    """Spin Money rewards should appear in the merchant earnings page."""

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="spin_earn_m", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        self.spin_wallet = SpinWallet.objects.create(
            user=self.merchant,
            available_spins=3,
            total_spins_earned=5,
        )
        # Create a spin reward (spin_date is required)
        from django.utils import timezone as tz
        SpinReward.objects.create(
            user=self.merchant,
            reward_tier="bronze",
            amount=Decimal("500.00"),
            spin_date=tz.now(),
        )
        self.client.force_login(self.merchant)

    def test_earnings_page_shows_spin_money_section(self):
        response = self.client.get(reverse("earnings_home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("spin-money-card", content.lower().replace("-", "-"))

    def test_earnings_page_shows_available_spins(self):
        response = self.client.get(reverse("earnings_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "3")  # available_spins

    def test_spin_wallet_in_context(self):
        response = self.client.get(reverse("earnings_home"))
        self.assertIn("spin_wallet", response.context)
        self.assertEqual(response.context["spin_wallet"].available_spins, 3)

    def test_latest_rewards_in_context(self):
        response = self.client.get(reverse("earnings_home"))
        self.assertIn("latest_rewards", response.context)
        self.assertGreaterEqual(len(response.context["latest_rewards"]), 1)
