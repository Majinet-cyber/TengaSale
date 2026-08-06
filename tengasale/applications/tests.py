import base64
from datetime import timedelta
from decimal import Decimal
from importlib import import_module
import shutil
import tempfile
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.utils import assign_role
from deals.models import DeviceBrand, DeviceDeal

from .forms import CustomerDetailsForm, LocationForm, WorkForm
from .models import FinancingApplication
from .services.device_matching import compare_deal_to_imei_result, compare_devices, descriptor_from_deal
from .income_bands import INCOME_BAND_RULES, validate_income_band_amount


GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
    b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02L"
    b"\x01\x00;"
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
    b"\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\r\xefF\xb8\x00\x00\x00\x00IEND\xaeB`\x82"
)


def valid_signature_data():
    encoded = base64.b64encode(PNG_BYTES).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def valid_customer_data(**overrides):
    data = {
        "customer_name": "Jane Banda",
        "national_id": "RQXFVZC9",
        "customer_phone": "990870616",
        "occupation": "Employed",
        "income_band": "200k_250k",
        "exact_monthly_income": "220000",
    }
    data.update(overrides)
    return data


class IncomeBandConsistencyTests(TestCase):
    def test_every_configured_band_accepts_a_valid_amount(self):
        examples = {"less_than_100k": 50000, "100k_150k": 125000, "150k_200k": 175000, "200k_250k": 220000, "250k_300k": 275000, "300k_350k": 325000, "350k_400k": 375000, "400k_600k": 500000, "more_than_600k": 600001}
        self.assertEqual(set(examples), set(INCOME_BAND_RULES))
        for band, amount in examples.items():
            with self.subTest(band=band):
                self.assertEqual(validate_income_band_amount(band, amount), Decimal(amount))

    def test_upper_band_rejects_values_at_or_below_boundary(self):
        for amount in (40000, 300000, 400000, 600000):
            form = CustomerDetailsForm(data=valid_customer_data(income_band="more_than_600k", exact_monthly_income=str(amount)))
            self.assertFalse(form.is_valid())
            self.assertIn("exact_monthly_income", form.errors)

    def test_correcting_amount_or_band_allows_submission(self):
        self.assertTrue(CustomerDetailsForm(data=valid_customer_data(income_band="more_than_600k", exact_monthly_income="600001")).is_valid())
        self.assertTrue(CustomerDetailsForm(data=valid_customer_data(income_band="350k_400k", exact_monthly_income="400000")).is_valid())


class ApplicationTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="merchant",
            password="test-pass-123",
        )
        assign_role(self.user, "merchant")
        self.client.login(username="merchant", password="test-pass-123")

    def create_application(self):
        return FinancingApplication.objects.create(created_by=self.user)

    def complete_customer_details(self, app):
        app.customer_name = "Jane Banda"
        app.national_id = "RQXFVZC9"
        app.customer_phone = "990870616"
        app.status = "customer_details"
        app.save(update_fields=["customer_name", "national_id", "customer_phone", "status"])

    def complete_kyc_images(self, app):
        app.customer_face_image.save("face.png", ContentFile(PNG_BYTES), save=False)
        app.id_front_image.save("front.png", ContentFile(PNG_BYTES), save=False)
        app.id_back_image.save("back.png", ContentFile(PNG_BYTES), save=False)
        app.save()

    def complete_location_and_work(self, app):
        app.region = "Central Region"
        app.district = "Lilongwe"
        app.traditional_authority = "TA Chadza"
        app.precise_location = "Area 18"
        app.location = "Area 18"
        app.work_description = "Retail business"
        app.save()

    def prime_for_review_submit(self, app):
        from datetime import date

        from applications.test_helpers import attach_complete_pricing

        self.complete_customer_details(app)
        app.national_id = "RQXFVZC9"
        app.date_of_birth = date(1990, 5, 15)
        app.gender = "male"
        attach_complete_pricing(app)
        self.complete_kyc_images(app)
        self.complete_location_and_work(app)
        app.save()

    def create_deal(self):
        brand = DeviceBrand.objects.create(name="TECNO")
        return DeviceDeal.objects.create(
            brand=brand,
            model_name="Pop 10C",
            specs="2+64",
            min_cash_price=Decimal("320000.00"),
            max_cash_price=Decimal("380000.00"),
            default_cash_price=Decimal("350000.00"),
            cash_price=Decimal("350000.00"),
            deposit_percent=Decimal("13.00"),
            loan_multiplier=Decimal("2.50"),
            term_months=12,
            total_12_month_price=Decimal("875000.00"),
        )


class CustomerValidationTests(TestCase):
    def assert_national_id_invalid(self, national_id):
        form = CustomerDetailsForm(data=valid_customer_data(national_id=national_id))

        self.assertFalse(form.is_valid())
        self.assertIn("national_id", form.errors)
        self.assertIn("National ID must be exactly 8 letters or numbers.", form.errors["national_id"])

    def assert_phone_invalid(self, phone):
        form = CustomerDetailsForm(data=valid_customer_data(customer_phone=phone))

        self.assertFalse(form.is_valid())
        self.assertIn("customer_phone", form.errors)
        self.assertIn("Phone number must be exactly 9 digits.", form.errors["customer_phone"])

    def test_blank_national_id_fails(self):
        self.assert_national_id_invalid("")

    def test_national_id_shorter_than_8_fails(self):
        self.assert_national_id_invalid("RQXFVZ")

    def test_national_id_longer_than_8_fails(self):
        self.assert_national_id_invalid("RQXFVZC99")

    def test_national_id_with_symbols_fails(self):
        self.assert_national_id_invalid("RQXF-ZC9")
        self.assert_national_id_invalid("@@@@@@@1")

    def test_national_id_with_spaces_fails(self):
        self.assert_national_id_invalid("RQXF ZC9")

    def test_national_id_with_8_digits_passes(self):
        form = CustomerDetailsForm(data=valid_customer_data(national_id="12345678"))

        self.assertTrue(form.is_valid(), form.errors)

    def test_national_id_with_8_letters_passes(self):
        form = CustomerDetailsForm(data=valid_customer_data(national_id="ABCDEFGH"))

        self.assertTrue(form.is_valid(), form.errors)

    def test_national_id_with_mixed_letters_and_digits_passes(self):
        form = CustomerDetailsForm(data=valid_customer_data(national_id="AB123CD4"))

        self.assertTrue(form.is_valid(), form.errors)

    def test_national_id_is_stored_uppercase(self):
        form = CustomerDetailsForm(data=valid_customer_data(national_id="wghjjju1"))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["national_id"], "WGHJJJU1")

    def test_blank_phone_fails(self):
        self.assert_phone_invalid("")

    def test_phone_shorter_than_9_fails(self):
        self.assert_phone_invalid("99087061")

    def test_phone_longer_than_9_fails(self):
        self.assert_phone_invalid("99087061699")

    def test_phone_with_letters_fails(self):
        self.assert_phone_invalid("990abc870")

    def test_phone_with_symbols_fails(self):
        self.assert_phone_invalid("990-87061")

    def test_valid_9_digit_phone_passes(self):
        form = CustomerDetailsForm(data=valid_customer_data(customer_phone="990870616"))

        self.assertTrue(form.is_valid(), form.errors)

    def test_occupation_options_exist(self):
        form = CustomerDetailsForm()

        values = [value for value, _label in form.fields["occupation"].choices]
        self.assertIn("Employed", values)
        self.assertIn("Business Owner", values)
        self.assertIn("Farming", values)
        self.assertIn("Other", values)

    def test_other_occupation_requires_detail(self):
        form = CustomerDetailsForm(data=valid_customer_data(occupation="Other", occupation_other=""))

        self.assertFalse(form.is_valid())
        self.assertIn("occupation_other", form.errors)

    def test_other_occupation_uses_detail_value(self):
        form = CustomerDetailsForm(data=valid_customer_data(occupation="Other", occupation_other="Tailor"))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["occupation"], "Tailor")

    def test_next_of_kin_phones_must_be_exactly_9_digits(self):
        location_form = LocationForm(data={"region": "Central", "district": "Lilongwe", "next_of_kin_1_phone": "123"})
        work_form = WorkForm(data={"next_of_kin_2_phone": "abcdefghi"})

        self.assertFalse(location_form.is_valid())
        self.assertFalse(work_form.is_valid())
        self.assertIn("next_of_kin_1_phone", location_form.errors)
        self.assertIn("next_of_kin_2_phone", work_form.errors)


class DeviceMatchingTests(ApplicationTestCase):
    def test_descriptor_extracts_ram_and_rom_from_deal_specs(self):
        deal = self.create_deal()

        descriptor = descriptor_from_deal(deal)

        self.assertEqual(descriptor["brand"], "TECNO")
        self.assertEqual(descriptor["model"], "Pop 10C")
        self.assertEqual(descriptor["ram"], 2)
        self.assertEqual(descriptor["rom"], 64)

    def test_compare_deal_to_imei_blocks_model_mismatch(self):
        deal = self.create_deal()

        result = compare_deal_to_imei_result(
            deal,
            api_brand="Infinix",
            api_model="Hot 40",
            api_specs="8GB 128GB",
            imei="123456789012345",
        )

        self.assertFalse(result["matches"])
        self.assertFalse(result["model_match"])
        self.assertFalse(result["ram_match"])
        self.assertFalse(result["rom_match"])

    def test_compare_devices_marks_invalid_imei(self):
        result = compare_devices(
            deal_device={"brand": "TECNO", "model": "Pop 10C", "ram": 2, "rom": 64},
            scanned_device={"brand": "TECNO", "model": "Pop 10C", "ram": 2, "rom": 64},
            imei="123",
        )

        self.assertFalse(result["matches"])
        self.assertTrue(result["invalid_imei"])


class NextOfKinAndWorkFormTests(ApplicationTestCase):
    def location_data(self, **overrides):
        data = {
            "region": "Central",
            "district": "Lilongwe",
            "traditional_authority": "TA Chadza",
            "precise_location": "Area 25",
            "next_of_kin_1_name": "Mary Banda",
            "next_of_kin_1_phone": "991111111",
            "next_of_kin_1_relationship": "Family",
        }
        data.update(overrides)
        return data

    def work_data(self, **overrides):
        data = {
            "work_description": "Runs a grocery stall",
            "next_of_kin_2_name": "Peter Phiri",
            "next_of_kin_2_phone": "992222222",
            "next_of_kin_2_relationship": "Friend",
            "proof_of_income_type": "MoMo",
            "proof_contact_name": "Airtel Agent",
            "proof_contact_phone": "993333333",
            "proof_notes": "",
        }
        data.update(overrides)
        return data

    def test_location_page_contains_relationship_dropdown_choices(self):
        app = self.create_application()
        self.complete_kyc_images(app)

        response = self.client.get(reverse("location_details", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="next_of_kin_1_relationship"')
        for choice in ["Family", "Friend", "Neighbour", "Other"]:
            self.assertContains(response, f'>{choice}</option>')

    def test_work_page_contains_relationship_dropdown_choices(self):
        app = self.create_application()
        self.complete_kyc_images(app)

        response = self.client.get(reverse("work_details", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="next_of_kin_2_relationship"')
        for choice in ["Family", "Friend", "Neighbour", "Other"]:
            self.assertContains(response, f'>{choice}</option>')

    def test_relationship_fields_are_required(self):
        location_form = LocationForm()
        work_form = WorkForm()

        self.assertTrue(location_form.fields["next_of_kin_1_relationship"].required)
        self.assertTrue(work_form.fields["next_of_kin_2_relationship"].required)

    def test_next_of_kin_1_phone_same_as_customer_phone_fails(self):
        app = self.create_application()
        app.customer_phone = "990870616"
        form = LocationForm(data=self.location_data(next_of_kin_1_phone="990870616"), instance=app)

        self.assertFalse(form.is_valid())
        self.assertIn("Next of kin phone cannot be the same as customer phone.", form.errors["next_of_kin_1_phone"])

    def test_next_of_kin_2_phone_same_as_customer_phone_fails(self):
        app = self.create_application()
        app.customer_phone = "990870616"
        app.next_of_kin_1_phone = "991111111"
        form = WorkForm(data=self.work_data(next_of_kin_2_phone="990870616"), instance=app)

        self.assertFalse(form.is_valid())
        self.assertIn("Next of kin phone cannot be the same as customer phone.", form.errors["next_of_kin_2_phone"])

    def test_next_of_kin_2_phone_same_as_next_of_kin_1_phone_fails(self):
        app = self.create_application()
        app.customer_phone = "990870616"
        app.next_of_kin_1_phone = "991111111"
        form = WorkForm(data=self.work_data(next_of_kin_2_phone="991111111"), instance=app)

        self.assertFalse(form.is_valid())
        self.assertIn("Next of kin 2 phone cannot be the same as next of kin 1 phone.", form.errors["next_of_kin_2_phone"])

    def test_valid_next_of_kin_phone_passes(self):
        app = self.create_application()
        app.customer_phone = "990870616"
        form = LocationForm(data=self.location_data(next_of_kin_1_phone="991111111"), instance=app)

        self.assertTrue(form.is_valid(), form.errors)

    def test_next_of_kin_phone_with_letters_fails(self):
        app = self.create_application()
        form = LocationForm(data=self.location_data(next_of_kin_1_phone="991abc111"), instance=app)

        self.assertFalse(form.is_valid())
        self.assertIn("next_of_kin_1_phone", form.errors)

    def test_next_of_kin_phone_shorter_than_9_fails(self):
        app = self.create_application()
        form = LocationForm(data=self.location_data(next_of_kin_1_phone="99111111"), instance=app)

        self.assertFalse(form.is_valid())
        self.assertIn("next_of_kin_1_phone", form.errors)

    def test_next_of_kin_phone_longer_than_9_fails(self):
        app = self.create_application()
        form = LocationForm(data=self.location_data(next_of_kin_1_phone="9911111110"), instance=app)

        self.assertFalse(form.is_valid())
        self.assertIn("next_of_kin_1_phone", form.errors)

    def test_work_proof_required_fields(self):
        required_fields = [
            "work_description",
            "next_of_kin_2_name",
            "next_of_kin_2_phone",
            "next_of_kin_2_relationship",
            "proof_of_income_type",
            "proof_contact_name",
            "proof_contact_phone",
        ]

        for field in required_fields:
            app = self.create_application()
            app.customer_phone = "990870616"
            app.next_of_kin_1_phone = "991111111"
            form = WorkForm(data=self.work_data(**{field: ""}), instance=app)
            self.assertFalse(form.is_valid(), field)
            self.assertIn(field, form.errors)


class ApplicationUrlTests(ApplicationTestCase):
    def test_application_url_names_resolve(self):
        app = self.create_application()

        self.assertEqual(reverse("new_application"), "/applications/new/")
        self.assertEqual(reverse("edit_customer_details", args=[app.id]), f"/applications/{app.id}/customer/")
        self.assertEqual(reverse("choose_device", args=[app.id]), f"/applications/{app.id}/device/")
        self.assertEqual(reverse("kyc_capture", args=[app.id]), f"/applications/{app.id}/kyc/")
        self.assertEqual(reverse("location_details", args=[app.id]), f"/applications/{app.id}/location/")
        self.assertEqual(reverse("work_details", args=[app.id]), f"/applications/{app.id}/work/")
        self.assertEqual(reverse("signature", args=[app.id]), f"/applications/{app.id}/signature/")
        self.assertEqual(reverse("application_detail", args=[app.id]), f"/applications/{app.id}/detail/")
        self.assertEqual(reverse("active_applications"), "/applications/active/")
        self.assertEqual(reverse("pending_applications"), "/applications/pending/")
        self.assertEqual(reverse("needs_edit_applications"), "/applications/needs-edit/")
        self.assertEqual(reverse("approved_applications"), "/applications/approved/")
        self.assertEqual(reverse("completed_applications"), "/applications/completed/")
        self.assertEqual(reverse("rejected_applications"), "/applications/rejected/")

    def test_list_pages_return_200_for_logged_in_user(self):
        for name in [
            "active_applications",
            "pending_applications",
            "needs_edit_applications",
            "approved_applications",
            "completed_applications",
            "rejected_applications",
        ]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)


class ApplicationListTests(ApplicationTestCase):
    def test_submitted_unclaimed_application_shows_pending_review(self):
        app = self.create_application()
        app.customer_name = "Jane Banda"
        app.national_id = "RQXFVZC9"
        app.status = "submitted"
        app.save()

        response = self.client.get(reverse("active_applications"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Review")
        self.assertNotContains(response, "Submitted")
        self.assertContains(response, f'href="{reverse("application_detail", args=[app.id])}"')

    def test_claimed_application_shows_under_review(self):
        manager = get_user_model().objects.create_user(username="manager", password="test-pass-123")
        assign_role(manager, "underwriter")
        app = self.create_application()
        app.customer_name = "Jane Banda"
        app.national_id = "RQXFVZC9"
        app.status = "under_review"
        app.claimed_by = manager
        app.save()

        response = self.client.get(reverse("active_applications"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Under Review")
        self.assertContains(response, "Underwriter: manager")

    def test_queue_position_updates_when_first_application_is_claimed(self):
        first = self.create_application()
        first.status = "pending_review"
        first.submitted_at = timezone.now()
        first.save()
        second = self.create_application()
        second.status = "pending_review"
        second.submitted_at = first.submitted_at + timedelta(minutes=1)
        second.save()

        self.assertEqual(first.queue_position, 1)
        self.assertEqual(second.queue_position, 2)
        self.assertEqual(second.applications_ahead, 1)

        manager = get_user_model().objects.create_user(username="queue-manager", password="test-pass-123")
        assign_role(manager, "underwriter")
        first.claimed_by = manager
        first.claimed_at = timezone.now()
        first.status = "under_review"
        first.save()

        self.assertIsNone(first.queue_position)
        self.assertEqual(second.queue_position, 1)

    def test_active_applications_page_contains_clickable_continue_card(self):
        app = self.create_application()
        app.customer_name = "Jane Banda"
        app.national_id = "RQXFVZC9"
        app.status = "device_selection"
        app.save()

        response = self.client.get(reverse("active_applications"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="app-card-v2"')
        self.assertContains(response, f'href="{app.get_continue_url()}"')
        self.assertContains(response, "Jane Banda")
        self.assertContains(response, app.application_number)

    def test_completed_applications_page_contains_clickable_card(self):
        app = self.create_application()
        app.status = "completed"
        app.save()

        response = self.client.get(reverse("completed_applications"))

        self.assertContains(response, 'class="app-card-v2"')
        self.assertContains(response, "Completed")
        self.assertContains(response, f'href="{app.get_continue_url()}"')
        self.assertEqual(app.get_continue_url(), reverse("application_detail", args=[app.id]))

    def test_completed_application_detail_is_read_only_audit_view(self):
        app = self.create_application()
        self.complete_kyc_images(app)
        app.customer_name = "Jane Banda"
        app.status = "completed"
        app.save()

        response = self.client.get(reverse("application_detail", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Completed Application")
        self.assertContains(response, "readonly-detail-card")
        self.assertContains(response, "kyc-smart-card__frame--selfie")
        self.assertContains(response, "kyc-smart-card__frame--id_front")
        self.assertContains(response, "kyc-smart-card__frame--id_back")
        self.assertNotContains(response, "Continue Application")
        self.assertNotContains(response, "Continue")

    def test_needs_edit_application_page_contains_clickable_card(self):
        app = self.create_application()
        app.status = "correction_requested"
        app.save()

        response = self.client.get(reverse("needs_edit_applications"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="app-card-v2"')
        self.assertContains(response, "Needs Edit")
        self.assertNotContains(response, "Correction Requested")
        self.assertContains(response, f'href="{app.get_continue_url()}"')

    def test_application_list_searches_contract_and_imei(self):
        app = self.create_application()
        app.customer_name = "Jane Banda"
        app.imei_number = "123456789012345"
        app.status = "device_locked"
        app.imei_number = "123456789012345"
        app.save()

        response = self.client.get(reverse("approved_applications"), {"q": "123456789012345"})

        self.assertContains(response, "Jane Banda")
        self.assertContains(response, "123456789012345")

    def test_rejected_applications_page_contains_clickable_card(self):
        app = self.create_application()
        app.status = "rejected"
        app.save()

        response = self.client.get(reverse("rejected_applications"))

        self.assertContains(response, 'class="app-card-v2"')
        self.assertContains(response, f'href="{app.get_continue_url()}"')
        self.assertEqual(app.get_continue_url(), reverse("application_detail", args=[app.id]))

    def test_application_detail_uses_merchant_status_label(self):
        app = self.create_application()
        app.status = "correction_requested"
        app.save()

        response = self.client.get(reverse("application_detail", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Needs Edit")
        self.assertNotContains(response, "Correction Requested")

    def test_list_customer_and_detail_pages_contain_soft_back(self):
        app = self.create_application()

        urls = [
            reverse("active_applications"),
            reverse("completed_applications"),
            reverse("rejected_applications"),
            reverse("edit_customer_details", args=[app.id]),
            reverse("application_detail", args=[app.id]),
        ]

        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            # Verify back navigation exists (back is in form-actions or icon-button in topbar)
            self.assertTrue(
                b'class="soft-back"' in response.content or
                b'class="icon-button"' in response.content or
                b'btn-outline' in response.content or
                b'ts-button-outline' in response.content,
                f"No back button found in {url}",
            )


class ApplicationContinueUrlTests(ApplicationTestCase):
    def test_get_continue_url_returns_customer_page_when_details_incomplete(self):
        app = self.create_application()

        for status in ["started", "customer_details"]:
            app.status = status
            app.save(update_fields=["status"])
            self.assertEqual(app.get_continue_url(), reverse("edit_customer_details", args=[app.id]))

    def test_get_continue_url_returns_deals_after_customer_details_complete(self):
        app = self.create_application()
        app.status = "customer_details"
        app.customer_name = "Jane Banda"
        app.national_id = "RQXFVZC9"
        app.customer_phone = "990870616"
        app.save()
        self.assertEqual(app.get_continue_url(), reverse("choose_device", args=[app.id]))

    def test_get_continue_url_returns_device_page_for_device_selection(self):
        app = self.create_application()
        self.complete_kyc_images(app)
        self.complete_location_and_work(app)
        app.status = "device_selection"
        app.save(update_fields=["status"])

        self.assertEqual(app.get_continue_url(), reverse("choose_device", args=[app.id]))

    def test_get_continue_url_returns_detail_page_for_terminal_statuses(self):
        app = self.create_application()

        for status in ["submitted", "rejected"]:
            app.status = status
            app.save(update_fields=["status"])
            self.assertEqual(app.get_continue_url(), reverse("application_detail", args=[app.id]))

    def test_approved_application_routes_to_contract_terms(self):
        app = self.create_application()
        app.status = "approved"
        app.save(update_fields=["status"])

        self.assertEqual(app.get_continue_url(), reverse("capture_imei", args=[app.id]))


class ApplicationDetailTests(ApplicationTestCase):
    def test_creator_can_access_application_detail(self):
        app = self.create_application()

        response = self.client.get(reverse("application_detail", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Application Detail")
        self.assertContains(response, 'class="soft-back"')

    def test_unrelated_user_gets_403_on_application_detail(self):
        app = self.create_application()
        other = get_user_model().objects.create_user(username="other", password="test-pass-123")
        assign_role(other, "merchant")
        self.client.login(username="other", password="test-pass-123")

        response = self.client.get(reverse("application_detail", args=[app.id]))

        self.assertEqual(response.status_code, 403)


class ApplicationFlowTests(ApplicationTestCase):
    def test_logged_in_user_can_start_new_application(self):
        response = self.client.get(reverse("new_application"))
        app = FinancingApplication.objects.get()

        self.assertRedirects(response, reverse("edit_customer_details", args=[app.id]))

    @patch("core.business_hours.is_business_hours", return_value=False)
    def test_outside_hours_modal_shows_detailed_text(self, _mock_hours):
        app = self.create_application()
        response = self.client.get(reverse("edit_customer_details", args=[app.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="outside-hours-modal"')
        self.assertContains(response, 'role="dialog"')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, "Outside business hours")
        self.assertContains(response, "Monday–Friday: 9am–6pm CAT")
        self.assertContains(response, "Sunday: closed")
        self.assertContains(response, "OK, continue")
        self.assertContains(response, "Cancel")
        self.assertContains(response, "Customer Details")

    @patch("core.business_hours.is_business_hours", return_value=False)
    def test_outside_hours_notice_is_centered_modal(self, _mock_hours):
        app = self.create_application()
        response = self.client.get(reverse("edit_customer_details", args=[app.id]))
        content = response.content.decode()
        self.assertIn('data-testid="outside-hours-modal"', content)
        self.assertIn("ts-oha", content)
        self.assertIn("ts-oha__dialog", content)
        self.assertIn("position: fixed", content)
        self.assertIn("100dvh", content)
        self.assertIn("env(safe-area-inset-top)", content)
        self.assertIn("sessionStorage.setItem", content)
        self.assertIn("ts-oha-open", content)
        self.assertNotIn('data-testid="outside-hours-chip"', content)
        self.assertNotIn("document.body.style.overflow = ''", content)
        self.assertNotIn("localStorage", content)
        self.assertNotIn("outside-hours-warning-card", content)

    @patch("core.business_hours.is_business_hours", return_value=True)
    def test_outside_hours_modal_hidden_during_business_hours(self, _mock_hours):
        app = self.create_application()
        response = self.client.get(reverse("edit_customer_details", args=[app.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="outside-hours-modal"')

    def test_customer_page_saves_valid_data_and_redirects_to_device_page(self):
        app = self.create_application()

        response = self.client.post(
            reverse("edit_customer_details", args=[app.id]),
            valid_customer_data(national_id="ab123cd4"),
        )

        app.refresh_from_db()
        self.assertRedirects(response, reverse("choose_device", args=[app.id]))
        self.assertEqual(app.national_id, "AB123CD4")
        self.assertEqual(app.customer_phone, "990870616")
        self.assertEqual(app.status, "customer_details")

    def test_customer_page_allows_duplicate_unpaid_in_progress_application(self):
        old_app = self.create_application()
        old_app.customer_name = "Jane Banda"
        old_app.national_id = "RQXFVZC9"
        old_app.customer_phone = "990870616"
        old_app.status = "customer_details"
        old_app.save(update_fields=["customer_name", "national_id", "customer_phone", "status"])
        new_app = self.create_application()

        response = self.client.post(
            reverse("edit_customer_details", args=[new_app.id]),
            valid_customer_data(),
        )

        self.assertRedirects(response, reverse("choose_device", args=[new_app.id]))

    def test_customer_page_blocks_duplicate_active_contract_stage_application(self):
        old_app = self.create_application()
        old_app.customer_name = "Jane Banda"
        old_app.national_id = "RQXFVZC9"
        old_app.customer_phone = "990870616"
        old_app.status = "active_contract"
        old_app.save(update_fields=["customer_name", "national_id", "customer_phone", "status"])
        new_app = self.create_application()

        response = self.client.post(
            reverse("edit_customer_details", args=[new_app.id]),
            valid_customer_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has an active TengaSale contract/application")

    def test_after_valid_customer_details_user_lands_on_deal_selection(self):
        app = self.create_application()

        response = self.client.post(
            reverse("edit_customer_details", args=[app.id]),
            valid_customer_data(),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a Phone Deal")
        self.assertEqual(response.resolver_match.url_name, "choose_device")

    def test_invalid_national_id_does_not_proceed(self):
        app = self.create_application()

        response = self.client.post(
            reverse("edit_customer_details", args=[app.id]),
            valid_customer_data(national_id="RQXF-ZC9"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "National ID must be exactly 8 letters or numbers.")
        app.refresh_from_db()
        self.assertEqual(app.status, "started")

    def test_invalid_phone_number_does_not_proceed(self):
        app = self.create_application()

        response = self.client.post(
            reverse("edit_customer_details", args=[app.id]),
            valid_customer_data(customer_phone="990abc870"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Phone number must be exactly 9 digits.")
        app.refresh_from_db()
        self.assertEqual(app.status, "started")

    def test_customer_page_contains_input_locking_attributes_and_script(self):
        app = self.create_application()

        response = self.client.get(reverse("edit_customer_details", args=[app.id]))

        self.assertContains(response, 'maxlength="8"')
        self.assertContains(response, 'pattern="[A-Za-z0-9]{8}"')
        self.assertContains(response, 'maxlength="9"')
        self.assertContains(response, 'pattern="[0-9]{9}"')
        self.assertContains(response, "+265")
        self.assertContains(response, "data-national-id-input")
        self.assertContains(response, "data-phone-input")
        self.assertContains(response, "slice(0, limit)")

    def test_device_page_with_no_deals_does_not_crash(self):
        app = self.create_application()
        self.complete_customer_details(app)

        response = self.client.get(reverse("choose_device", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No deals available. Please ask management to add device deals.")

    def test_device_page_contains_seeded_brand_choices(self):
        app = self.create_application()
        self.complete_customer_details(app)
        call_command("seed_tengasale")

        response = self.client.get(reverse("choose_device", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tecno")
        self.assertContains(response, "Itel")
        self.assertContains(response, "Redmi/Xiaomi")

    @override_settings(DEBUG=False)
    def test_device_page_with_brand_logos_does_not_500_when_manifest_missing(self):
        app = self.create_application()
        self.complete_customer_details(app)
        call_command("seed_tengasale")

        response = self.client.get(reverse("choose_device", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose brand")

    def test_device_page_contains_guided_flow_text_and_hidden_inputs(self):
        app = self.create_application()
        self.complete_customer_details(app)
        self.create_deal()

        response = self.client.get(reverse("choose_device", args=[app.id]))

        self.assertContains(response, "Choose brand")
        self.assertContains(response, "Choose model")
        self.assertContains(response, "Choose specs")
        self.assertContains(response, "Pay early, pay less")
        self.assertContains(response, "6 months")
        self.assertContains(response, "15% discount")
        self.assertContains(response, "3 months")
        self.assertContains(response, "25% discount")
        self.assertContains(response, 'name="deal_id"')
        self.assertContains(response, 'name="selected_cash_price"')
        self.assertContains(response, "data-term-display")
        self.assertContains(response, "data-deal-js-error")
        self.assertContains(response, 'document.querySelector("[data-term-display]")')
        self.assertContains(response, "term_discounts")
        self.assertContains(response, '"3": "25"')
        self.assertContains(response, '"6": "15"')
        self.assertNotContains(response, "totalLoan * 0.85")
        self.assertNotContains(response, "totalLoan * 0.75")

    def test_invalid_deal_id_post_returns_error(self):
        app = self.create_application()
        self.complete_customer_details(app)
        self.create_deal()

        response = self.client.post(
            reverse("choose_device", args=[app.id]),
            {"deal_id": "not-a-deal", "selected_cash_price": "360000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select an available device deal before continuing.")

    def test_valid_deal_selection_saves_calculated_values_and_redirects_to_kyc(self):
        app = self.create_application()
        self.complete_customer_details(app)
        deal = self.create_deal()

        response = self.client.post(
            reverse("choose_device", args=[app.id]),
            {"deal_id": deal.id, "selected_cash_price": "360000"},
        )

        app.refresh_from_db()
        self.assertRedirects(response, reverse("kyc_capture", args=[app.id]))
        self.assertEqual(app.deal, deal)
        self.assertEqual(app.selected_cash_price, Decimal("360000.00"))
        self.assertEqual(app.selected_deposit_percent, Decimal("13.00"))
        self.assertEqual(app.selected_loan_multiplier, Decimal("2.50"))
        self.assertEqual(app.calculated_total_loan, Decimal("900000.00"))
        self.assertEqual(app.calculated_deposit_amount, Decimal("117000.00"))
        self.assertEqual(app.calculated_monthly_payment, Decimal("75000.00"))
        self.assertEqual(app.calculated_daily_payment, Decimal("2500.00"))
        self.assertEqual(app.calculated_6_month_total, Decimal("765000.00"))
        self.assertEqual(app.calculated_6_month_monthly, Decimal("127500.00"))
        self.assertEqual(app.calculated_6_month_daily, Decimal("4250.00"))
        self.assertEqual(app.calculated_3_month_total, Decimal("675000.00"))
        self.assertEqual(app.calculated_3_month_monthly, Decimal("225000.00"))
        self.assertEqual(app.calculated_3_month_daily, Decimal("7500.00"))
        self.assertEqual(app.status, "device_selection")

    def test_selected_cash_price_below_min_returns_error(self):
        app = self.create_application()
        self.complete_customer_details(app)
        deal = self.create_deal()

        response = self.client.post(
            reverse("choose_device", args=[app.id]),
            {"deal_id": deal.id, "selected_cash_price": "319000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cash price must stay within the selected deal price range.")

    def test_selected_cash_price_above_max_returns_error(self):
        app = self.create_application()
        self.complete_customer_details(app)
        deal = self.create_deal()

        response = self.client.post(
            reverse("choose_device", args=[app.id]),
            {"deal_id": deal.id, "selected_cash_price": "381000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cash price must stay within the selected deal price range.")

    def test_kyc_location_and_work_pages_load(self):
        from applications.test_helpers import attach_complete_pricing

        app = self.create_application()
        self.complete_customer_details(app)
        attach_complete_pricing(app)
        self.complete_kyc_images(app)

        for name in ["kyc_capture", "location_details", "work_details"]:
            response = self.client.get(reverse(name, args=[app.id]))
            self.assertEqual(response.status_code, 200)

    def test_signature_page_requires_agreed_to_terms_before_submit(self):
        app = self.create_application()
        self.prime_for_review_submit(app)
        app.signature_image.save("signature.png", ContentFile(PNG_BYTES), save=False)
        app.save(update_fields=["signature_image"])

        response = self.client.post(reverse("application_review", args=[app.id]), {})

        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertNotEqual(app.status, "pending_review")

    def test_final_submit_sets_status_pending_review(self):
        app = self.create_application()
        self.prime_for_review_submit(app)
        app.signature_image.save("signature.png", ContentFile(PNG_BYTES), save=False)
        app.save(update_fields=["signature_image"])

        response = self.client.post(
            reverse("application_review", args=[app.id]),
            {"agreed_to_terms": "on"},
        )

        app.refresh_from_db()
        self.assertRedirects(response, reverse("application_submitted", args=[app.id]))
        self.assertEqual(app.status, "pending_review")
        self.assertIsNotNone(app.submitted_at)


class SignaturePageTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()

    def prime_for_signature_flow(self, app):
        self.prime_for_review_submit(app)

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def test_signature_page_contains_live_canvas_controls(self):
        app = self.create_application()
        self.prime_for_signature_flow(app)

        response = self.client.get(reverse("signature", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<canvas")
        self.assertContains(response, "Capture Signature")
        self.assertContains(response, 'aria-label="Clear signature"')
        self.assertNotContains(response, 'type="file"')

    def test_save_signature_redirects_to_review(self):
        app = self.create_application()
        self.prime_for_signature_flow(app)

        response = self.client.post(
            reverse("signature", args=[app.id]),
            {"save_signature": "1", "agreed_to_terms": "on", "signature_data": valid_signature_data()},
        )

        app.refresh_from_db()
        self.assertRedirects(response, reverse("application_review", args=[app.id]))
        self.assertTrue(app.signature_image)

    def test_review_page_shows_saved_signature(self):
        app = self.create_application()
        self.prime_for_signature_flow(app)
        app.signature_image.save("signature.png", ContentFile(PNG_BYTES), save=False)
        app.save(update_fields=["signature_image"])

        response = self.client.get(reverse("application_review", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, app.signature_image.url)
        self.assertContains(response, "Signature saved")

    def test_submit_without_signature_fails(self):
        app = self.create_application()
        self.prime_for_signature_flow(app)

        response = self.client.post(reverse("application_review", args=[app.id]), {"agreed_to_terms": "on"})

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("signature", args=[app.id]))
        app.refresh_from_db()
        self.assertNotEqual(app.status, "submitted")

    def test_submit_without_terms_fails(self):
        app = self.create_application()
        self.prime_for_signature_flow(app)
        app.signature_image.save("signature.png", ContentFile(PNG_BYTES), save=False)
        app.save(update_fields=["signature_image"])

        response = self.client.post(reverse("application_review", args=[app.id]), {})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The customer must agree to the terms before submitting.")
        app.refresh_from_db()
        self.assertNotEqual(app.status, "submitted")

    def test_submit_with_valid_signature_and_terms_succeeds(self):
        app = self.create_application()
        self.prime_for_signature_flow(app)
        app.signature_image.save("signature.png", ContentFile(PNG_BYTES), save=False)
        app.save(update_fields=["signature_image"])

        response = self.client.post(
            reverse("application_review", args=[app.id]),
            {"agreed_to_terms": "on"},
        )

        app.refresh_from_db()
        self.assertRedirects(response, reverse("application_submitted", args=[app.id]))
        self.assertEqual(app.status, "pending_review")
        self.assertTrue(app.signature_image)
        self.assertTrue(app.agreed_to_terms)


class ApplicationDetailDataTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def test_detail_page_shows_next_of_kin_proof_and_signature_data(self):
        app = self.create_application()
        app.next_of_kin_1_name = "Mary Banda"
        app.next_of_kin_1_phone = "991111111"
        app.next_of_kin_1_relationship = "Family"
        app.next_of_kin_2_name = "Peter Phiri"
        app.next_of_kin_2_phone = "992222222"
        app.next_of_kin_2_relationship = "Friend"
        app.work_description = "Runs a grocery stall"
        app.proof_of_income_type = "MoMo"
        app.proof_contact_name = "Airtel Agent"
        app.proof_contact_phone = "993333333"
        app.agreed_to_terms = True
        app.signature_image.save("signature.png", ContentFile(PNG_BYTES), save=False)
        app.save()

        response = self.client.get(reverse("application_detail", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        for value in [
            "Mary Banda",
            "+265 991111111",
            "Family",
            "Peter Phiri",
            "+265 992222222",
            "Friend",
            "Runs a grocery stall",
            "MoMo",
            "Airtel Agent",
            "+265 993333333",
            "Customer signature",
            "Yes",
        ]:
            self.assertContains(response, value)


class KYCCaptureTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()

    def create_application(self):
        from applications.test_helpers import attach_complete_pricing

        app = super().create_application()
        self.complete_customer_details(app)
        attach_complete_pricing(app)
        return app

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def image_upload(self, name):
        return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")

    def save_existing_images(self, app):
        app.customer_face_image.save("face.png", ContentFile(PNG_BYTES), save=False)
        app.id_front_image.save("front.png", ContentFile(PNG_BYTES), save=False)
        app.id_back_image.save("back.png", ContentFile(PNG_BYTES), save=False)
        app.save()

    def test_get_continue_url_returns_kyc_or_location_based_on_images(self):
        app = self.create_application()
        app.status = "kyc"
        app.save(update_fields=["status"])
        self.assertEqual(app.get_continue_url(), reverse("kyc_capture", args=[app.id]))

        self.save_existing_images(app)
        app.refresh_from_db()
        self.assertEqual(app.get_continue_url(), reverse("location_details", args=[app.id]))

    def test_kyc_page_get_shows_live_capture_controls(self):
        app = self.create_application()

        response = self.client.get(reverse("kyc_capture", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Capture Selfie")
        self.assertContains(response, "Take ID Front Photo")
        self.assertContains(response, "Take ID Back Photo")
        self.assertNotContains(response, "Capture Customer Phone")
        self.assertContains(response, 'capture="user"')
        self.assertContains(response, 'capture="environment"', count=2)
        self.assertContains(response, 'class="kyc-file-input"')

    def test_kyc_page_has_correct_facing_mode_data_attributes(self):
        """Selfie step uses front camera; ID steps use rear camera."""
        app = self.create_application()

        response = self.client.get(reverse("kyc_capture", args=[app.id]))

        self.assertContains(response, 'data-default-facing="user"')
        self.assertContains(response, 'data-default-facing="environment"', count=2)

    def test_kyc_page_has_agent_mode_pill(self):
        """Agent mode pill toggle should be present on selfie step."""
        app = self.create_application()

        response = self.client.get(reverse("kyc_capture", args=[app.id]))

        self.assertContains(response, 'kyc-mode-pill')
        self.assertContains(response, 'data-mode-customer')
        self.assertContains(response, 'data-mode-agent')

    def test_kyc_js_has_no_unclosed_block_comment(self):
        """Regression: ensure the camera JS is not broken by an unclosed /* comment."""
        app = self.create_application()

        response = self.client.get(reverse("kyc_capture", args=[app.id]))
        content = response.content.decode()

        # The broken comment marker that previously swallowed the script
        self.assertNotIn('/* ── Initial active step determination ── #}', content)
        # The correct line comment must be present
        self.assertIn('// ── Initial active step determination ──', content)

    def test_kyc_post_without_images_stays_on_page_with_errors(self):
        app = self.create_application()

        response = self.client.post(reverse("kyc_capture", args=[app.id]), {})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customer face image is required.")
        self.assertContains(response, "ID front image is required.")
        self.assertContains(response, "ID back image is required.")
        self.assertNotContains(response, "Customer phone image is required.")
        app.refresh_from_db()
        self.assertNotEqual(app.status, "kyc")

    def test_kyc_post_with_only_face_image_fails(self):
        app = self.create_application()

        response = self.client.post(
            reverse("kyc_capture", args=[app.id]),
            {"customer_face_image": self.image_upload("face.png")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ID front image is required.")
        self.assertContains(response, "ID back image is required.")
        self.assertNotContains(response, "Customer phone image is required.")

    def test_kyc_post_with_face_and_id_front_fails(self):
        app = self.create_application()

        response = self.client.post(
            reverse("kyc_capture", args=[app.id]),
            {
                "customer_face_image": self.image_upload("face.png"),
                "id_front_image": self.image_upload("front.png"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ID back image is required.")
        self.assertNotContains(response, "Customer phone image is required.")

    def test_kyc_post_with_all_images_succeeds_and_redirects_to_location(self):
        app = self.create_application()

        response = self.client.post(
            reverse("kyc_capture", args=[app.id]),
            {
                "customer_face_image": self.image_upload("face.png"),
                "id_front_image": self.image_upload("front.png"),
                "id_back_image": self.image_upload("back.png"),
            },
        )

        app.refresh_from_db()
        self.assertRedirects(response, reverse("location_details", args=[app.id]))
        self.assertEqual(app.status, "kyc")
        self.assertTrue(app.customer_face_image)
        self.assertTrue(app.id_front_image)
        self.assertTrue(app.id_back_image)
        self.assertFalse(app.customer_phone_image)

    def test_existing_saved_images_show_previews_and_allow_continue(self):
        app = self.create_application()
        self.save_existing_images(app)

        response = self.client.get(reverse("kyc_capture", args=[app.id]))

        self.assertContains(response, "Customer Face preview")
        self.assertContains(response, "ID Front preview")
        self.assertContains(response, "ID Back preview")
        self.assertNotContains(response, "Customer Phone preview")
        self.assertContains(response, 'data-existing="true"', count=3)
        self.assertNotContains(response, "data-next-button disabled")

        post_response = self.client.post(reverse("kyc_capture", args=[app.id]), {})
        self.assertRedirects(post_response, reverse("location_details", args=[app.id]))

    def test_recapture_controls_exist_and_new_image_replaces_saved_image(self):
        app = self.create_application()
        self.save_existing_images(app)
        original_face_name = app.customer_face_image.name

        response = self.client.get(reverse("kyc_capture", args=[app.id]))
        # 3 section retake buttons + 1 in the confirm modal = at least 3
        self.assertContains(response, "Retake")

        post_response = self.client.post(
            reverse("kyc_capture", args=[app.id]),
            {"customer_face_image": self.image_upload("new-face.png")},
        )

        app.refresh_from_db()
        self.assertRedirects(post_response, reverse("location_details", args=[app.id]))
        self.assertNotEqual(app.customer_face_image.name, original_face_name)
        self.assertIn("kyc/faces/", app.customer_face_image.name)

    def test_another_user_cannot_access_or_modify_kyc_page(self):
        app = self.create_application()
        other_user = get_user_model().objects.create_user(
            username="other-merchant",
            password="test-pass-123",
        )
        assign_role(other_user, "merchant")
        self.client.login(username=other_user.username, password="test-pass-123")

        get_response = self.client.get(reverse("kyc_capture", args=[app.id]))
        post_response = self.client.post(
            reverse("kyc_capture", args=[app.id]),
            {
                "customer_face_image": self.image_upload("face.png"),
                "id_front_image": self.image_upload("front.png"),
                "id_back_image": self.image_upload("back.png"),
            },
        )

        app.refresh_from_db()
        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)
        self.assertFalse(app.customer_face_image)
        self.assertNotEqual(app.status, "kyc")

    def test_kyc_save_image_ajax_persists_single_photo(self):
        app = self.create_application()

        response = self.client.post(
            reverse("kyc_save_image", args=[app.id]),
            {"field": "customer_face_image", "image": self.image_upload("face.png")},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("/media/", payload["url"])
        self.assertIn("preview_url", payload)
        self.assertIn("?v=", payload["preview_url"])

        app.refresh_from_db()
        self.assertTrue(app.customer_face_image)
        self.assertIn("kyc/faces/", app.customer_face_image.name)

    def test_kyc_save_image_returns_absolute_media_url(self):
        app = self.create_application()
        response = self.client.post(
            reverse("kyc_save_image", args=[app.id]),
            {"field": "customer_face_image", "image": self.image_upload("face.png")},
        )
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["url"].startswith("http"))
        self.assertIn("/media/", payload["url"])

    def test_kyc_template_skips_empty_review_src_error_binding(self):
        response = self.client.get(reverse("kyc_capture", args=[self.create_application().id]))
        self.assertContains(response, "if (!src || src === window.location.href) return")
        self.assertContains(response, 'id="kyc-img-selfie"')
        self.assertContains(response, 'id="kyc-img-id_front"')
        self.assertContains(response, 'id="kyc-img-id_back"')
        self.assertContains(response, 'id="kyc-img-id_front-empty"')
        self.assertContains(response, 'id="kyc-img-id_back-empty"')

    def test_kyc_review_shows_saved_image_urls(self):
        app = self.create_application()
        self.save_existing_images(app)

        response = self.client.get(reverse("kyc_capture", args=[app.id]))

        self.assertContains(response, app.customer_face_image.url)
        self.assertContains(response, app.id_front_image.url)
        self.assertContains(response, app.id_back_image.url)
        self.assertContains(response, "kyc-portrait-stack")
        self.assertContains(response, "kyc-smart-card")

    def test_kyc_review_no_images_shows_missing_and_disabled_submit(self):
        app = self.create_application()
        response = self.client.get(reverse("kyc_capture", args=[app.id]))

        self.assertContains(response, 'data-kyc-complete="false"')
        self.assertContains(response, "Missing: Selfie, ID Card Front, ID Card Back")
        self.assertContains(response, 'id="kyc-submit-btn"')
        self.assertRegex(
            response.content.decode(),
            r'id="kyc-submit-btn"[^>]*\bdisabled\b',
        )

    def test_kyc_review_selfie_only_shows_missing_id_messages(self):
        app = self.create_application()
        app.customer_face_image.save("face.png", ContentFile(PNG_BYTES), save=True)

        response = self.client.get(reverse("kyc_capture", args=[app.id]))

        self.assertContains(response, 'data-kyc-complete="false"')
        self.assertContains(response, "Missing: ID Card Front, ID Card Back")
        self.assertRegex(
            response.content.decode(),
            r'id="kyc-submit-btn"[^>]*\bdisabled\b',
        )

    def test_kyc_review_selfie_and_front_shows_missing_back(self):
        app = self.create_application()
        app.customer_face_image.save("face.png", ContentFile(PNG_BYTES), save=False)
        app.id_front_image.save("front.png", ContentFile(PNG_BYTES), save=True)

        response = self.client.get(reverse("kyc_capture", args=[app.id]))

        self.assertContains(response, 'data-kyc-complete="false"')
        self.assertContains(response, "Missing: ID Card Back")
        self.assertRegex(
            response.content.decode(),
            r'id="kyc-submit-btn"[^>]*\bdisabled\b',
        )

    def test_kyc_review_all_images_enables_submit_and_post_redirects(self):
        app = self.create_application()
        self.save_existing_images(app)

        response = self.client.get(reverse("kyc_capture", args=[app.id]))

        self.assertContains(response, 'data-kyc-complete="true"')
        self.assertContains(response, 'id="kyc-img-selfie"')
        self.assertContains(response, 'id="kyc-img-id_front"')
        self.assertContains(response, 'id="kyc-img-id_back"')
        self.assertContains(response, 'data-disabled-note hidden')
        self.assertContains(response, 'id="kyc-submit-btn"')
        self.assertNotRegex(
            response.content.decode(),
            r'id="kyc-submit-btn"[^>]*\bdisabled\b',
        )

        post_response = self.client.post(reverse("kyc_capture", args=[app.id]), {})
        app.refresh_from_db()
        self.assertRedirects(post_response, reverse("location_details", args=[app.id]))
        self.assertEqual(app.status, "kyc")

    def test_kyc_retake_replaces_image_and_submit_still_works(self):
        app = self.create_application()
        self.save_existing_images(app)
        original_back = app.id_back_image.name

        self.client.post(
            reverse("kyc_save_image", args=[app.id]),
            {"field": "id_back_image", "image": self.image_upload("new-back.png")},
        )
        app.refresh_from_db()
        self.assertNotEqual(app.id_back_image.name, original_back)

        post_response = self.client.post(reverse("kyc_capture", args=[app.id]), {})
        self.assertRedirects(post_response, reverse("location_details", args=[app.id]))


class ApplicationAdminImportTests(TestCase):
    def test_importing_applications_admin_does_not_crash(self):
        admin_module = import_module("applications.admin")

        self.assertIs(admin_module.FinancingApplication, FinancingApplication)

    def test_financing_application_is_registered_in_admin(self):
        self.assertIn(FinancingApplication, admin.site._registry)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 10: Field Marking and Customer Correction Flow
# ──────────────────────────────────────────────────────────────────────────────

from applications.models import ApplicationCorrectionToken, ApplicationFieldReview


class Phase10FieldMarkingTests(TestCase):
    """Test field marking by underwriters and secure customer correction flow."""

    def setUp(self):
        self.merchant = get_user_model().objects.create_user(
            username="merch_fm10", password="pass123"
        )
        assign_role(self.merchant, "merchant")
        self.underwriter = get_user_model().objects.create_user(
            username="uw_fm10", password="pass123"
        )
        assign_role(self.underwriter, "underwriter")
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="under_review",
            claimed_by=self.underwriter,
            customer_name="Janet Banda",
            national_id="ABCD1234",
            customer_phone="099123456",
        )

    # ── Model-level tests ──

    def test_create_field_review_model(self):
        review = ApplicationFieldReview.objects.create(
            application=self.app,
            field_key="customer_name",
            field_label="Customer Name",
            section=ApplicationFieldReview.SECTION_CUSTOMER,
            reason=ApplicationFieldReview.REASON_INCORRECT,
            marked_by=self.underwriter,
            status=ApplicationFieldReview.STATUS_MARKED,
        )
        self.assertEqual(review.status, ApplicationFieldReview.STATUS_MARKED)
        self.assertEqual(review.application, self.app)

    def test_dismiss_field_review_changes_status(self):
        review = ApplicationFieldReview.objects.create(
            application=self.app,
            field_key="national_id",
            field_label="National ID",
            section=ApplicationFieldReview.SECTION_CUSTOMER,
            reason=ApplicationFieldReview.REASON_UNCLEAR_DOC,
            marked_by=self.underwriter,
            status=ApplicationFieldReview.STATUS_MARKED,
        )
        review.status = ApplicationFieldReview.STATUS_DISMISSED
        review.save()
        review.refresh_from_db()
        self.assertEqual(review.status, ApplicationFieldReview.STATUS_DISMISSED)

    # ── Mark field via view ──

    def test_underwriter_can_mark_field_via_post(self):
        self.client.login(username="uw_fm10", password="pass123")
        response = self.client.post(
            reverse("sales_mark_field", args=[self.app.id]),
            {
                "field_key": "full_name",
                "field_label": "Customer Full Name",
                "reason": ApplicationFieldReview.REASON_INCORRECT,
                "section": ApplicationFieldReview.SECTION_CUSTOMER,
                "current_value": "Janet Banda",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ApplicationFieldReview.objects.filter(
                application=self.app, field_key="full_name"
            ).exists()
        )

    # ── Customer correction page ──

    def test_correction_page_returns_404_on_invalid_token(self):
        response = self.client.get(
            reverse("customer_field_correction", args=["totally-invalid-token"])
        )
        self.assertEqual(response.status_code, 404)

    def test_correction_page_returns_410_on_expired_token(self):
        from datetime import timedelta
        from django.utils import timezone

        ApplicationFieldReview.objects.create(
            application=self.app,
            field_key="customer_name",
            field_label="Customer Name",
            section=ApplicationFieldReview.SECTION_CUSTOMER,
            reason=ApplicationFieldReview.REASON_INCORRECT,
            marked_by=self.underwriter,
            status=ApplicationFieldReview.STATUS_MARKED,
        )
        token_obj = ApplicationCorrectionToken.create_or_refresh(self.app)
        token_obj.expires_at = timezone.now() - timedelta(hours=1)
        token_obj.save()

        response = self.client.get(
            reverse("customer_field_correction", args=[token_obj.token])
        )
        self.assertEqual(response.status_code, 410)

    def test_correction_page_shows_only_marked_fields(self):
        ApplicationFieldReview.objects.create(
            application=self.app,
            field_key="full_name",          # matches EDITABLE_FIELD_MAP key
            field_label="Customer Full Name",
            section=ApplicationFieldReview.SECTION_CUSTOMER,
            reason=ApplicationFieldReview.REASON_INCORRECT,
            marked_by=self.underwriter,
            status=ApplicationFieldReview.STATUS_MARKED,
        )
        # Dismissed review — should NOT appear on the correction page
        ApplicationFieldReview.objects.create(
            application=self.app,
            field_key="national_id",
            field_label="National ID",
            section=ApplicationFieldReview.SECTION_CUSTOMER,
            reason=ApplicationFieldReview.REASON_UNCLEAR_DOC,
            marked_by=self.underwriter,
            status=ApplicationFieldReview.STATUS_DISMISSED,
        )
        token_obj = ApplicationCorrectionToken.create_or_refresh(self.app)
        response = self.client.get(
            reverse("customer_field_correction", args=[token_obj.token])
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Customer Full Name", content)
        self.assertNotIn("National ID", content)

    def test_correction_page_no_internal_data_leakage(self):
        ApplicationFieldReview.objects.create(
            application=self.app,
            field_key="full_name",
            field_label="Customer Full Name",
            section=ApplicationFieldReview.SECTION_CUSTOMER,
            reason=ApplicationFieldReview.REASON_INCORRECT,
            marked_by=self.underwriter,
            status=ApplicationFieldReview.STATUS_MARKED,
        )
        token_obj = ApplicationCorrectionToken.create_or_refresh(self.app)
        response = self.client.get(
            reverse("customer_field_correction", args=[token_obj.token])
        )
        content = response.content.decode().lower()
        self.assertNotIn("commission", content)
        self.assertNotIn("audit", content)
        self.assertNotIn("claimed_by", content)
        self.assertNotIn("kulasell", content)
        self.assertNotIn("yellow africa", content)

    def test_customer_can_correct_marked_field(self):
        ApplicationFieldReview.objects.create(
            application=self.app,
            field_key="full_name",          # matches EDITABLE_FIELD_MAP key
            field_label="Customer Full Name",
            section=ApplicationFieldReview.SECTION_CUSTOMER,
            reason=ApplicationFieldReview.REASON_INCORRECT,
            marked_by=self.underwriter,
            status=ApplicationFieldReview.STATUS_MARKED,
        )
        token_obj = ApplicationCorrectionToken.create_or_refresh(self.app)
        response = self.client.post(
            reverse("customer_field_correction", args=[token_obj.token]),
            {"full_name": "Janet Moyo"},
        )
        self.assertEqual(response.status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.customer_name, "Janet Moyo")

    def test_correction_changes_status_to_customer_updated(self):
        review = ApplicationFieldReview.objects.create(
            application=self.app,
            field_key="full_name",
            field_label="Customer Full Name",
            section=ApplicationFieldReview.SECTION_CUSTOMER,
            reason=ApplicationFieldReview.REASON_INCORRECT,
            marked_by=self.underwriter,
            status=ApplicationFieldReview.STATUS_MARKED,
        )
        token_obj = ApplicationCorrectionToken.create_or_refresh(self.app)
        self.client.post(
            reverse("customer_field_correction", args=[token_obj.token]),
            {"full_name": "Janet Phiri"},
        )
        review.refresh_from_db()
        self.assertEqual(review.status, ApplicationFieldReview.STATUS_CUSTOMER_UPDATED)

    def test_unmarked_field_not_editable_via_correction_page(self):
        ApplicationFieldReview.objects.create(
            application=self.app,
            field_key="full_name",
            field_label="Customer Full Name",
            section=ApplicationFieldReview.SECTION_CUSTOMER,
            reason=ApplicationFieldReview.REASON_INCORRECT,
            marked_by=self.underwriter,
            status=ApplicationFieldReview.STATUS_MARKED,
        )
        token_obj = ApplicationCorrectionToken.create_or_refresh(self.app)
        original_national_id = self.app.national_id

        # Submit national_id but it is not marked — must not be accepted
        self.client.post(
            reverse("customer_field_correction", args=[token_obj.token]),
            {"full_name": "Janet Chirwa", "national_id": "HACKED01"},
        )
        self.app.refresh_from_db()
        self.assertEqual(self.app.national_id, original_national_id)

    # ── Token behaviour ──

    def test_correction_token_is_generated_securely(self):
        token_obj = ApplicationCorrectionToken.create_or_refresh(self.app)
        self.assertGreater(len(token_obj.token), 20)
        self.assertTrue(token_obj.is_valid)

    def test_create_or_refresh_replaces_existing_token(self):
        token1 = ApplicationCorrectionToken.create_or_refresh(self.app)
        old_token_value = token1.token
        token2 = ApplicationCorrectionToken.create_or_refresh(self.app)
        self.assertEqual(token1.pk, token2.pk)
        self.assertNotEqual(token2.token, old_token_value)


# ──────────────────────────────────────────────────────────────────────────────
# IMEI Verification Tests
# ──────────────────────────────────────────────────────────────────────────────

from unittest.mock import MagicMock, patch

from applications.services.imei_verification import (
    extract_brand_model_from_api_result,
    normalize_brand,
    normalize_model,
    score_device_match,
    verify_imei_against_selected_device,
)


class NormalisationTests(TestCase):
    """normalize_brand and normalize_model helpers."""

    def test_tecno_variants_all_normalise_to_tecno(self):
        for text in ["Tecno", "TECNO", "Tecno Mobile", "Tecno Telecom (HK) Limited", "tecno telecom"]:
            self.assertEqual(normalize_brand(text), "tecno", msg=text)

    def test_samsung_galaxy_normalises_to_samsung(self):
        self.assertEqual(normalize_brand("Samsung Galaxy"), "samsung")
        self.assertEqual(normalize_brand("Samsung"), "samsung")

    def test_apple_iphone_normalises_to_apple(self):
        self.assertEqual(normalize_brand("Apple"), "apple")
        self.assertEqual(normalize_brand("iPhone"), "apple")

    def test_xiaomi_redmi_poco_normalise_to_xiaomi(self):
        self.assertEqual(normalize_brand("Xiaomi"), "xiaomi")
        self.assertEqual(normalize_brand("Redmi"), "xiaomi")
        self.assertEqual(normalize_brand("POCO"), "xiaomi")

    def test_itel_normalises(self):
        self.assertEqual(normalize_brand("itel"), "itel")
        self.assertEqual(normalize_brand("iTel"), "itel")

    def test_model_strips_parenthetical_suffix(self):
        self.assertEqual(normalize_model("iPhone 15 Pro (A3104)"), "iphone 15 pro")

    def test_model_lowercased_and_stripped(self):
        self.assertEqual(normalize_model("TECNO SPARK 50"), "tecno spark 50")

    def test_model_empty_returns_empty(self):
        self.assertEqual(normalize_model(""), "")


class BrandModelExtractionTests(TestCase):
    """extract_brand_model_from_api_result."""

    def test_tecno_from_raw_text(self):
        raw = "Brand: Tecno Telecom (HK) Limited\nModel: TECNO SPARK 50\nModel Name: KN4"
        brand, model = extract_brand_model_from_api_result(raw, {})
        self.assertEqual(brand, "tecno")
        self.assertEqual(model, "TECNO SPARK 50")

    def test_apple_from_object_dict(self):
        obj = {"model": "iPhone 15 Pro (A3104)", "imei": "35698831919000"}
        brand, model = extract_brand_model_from_api_result("", obj)
        self.assertEqual(brand, "apple")
        self.assertIn("iPhone 15 Pro", model)

    def test_empty_returns_empty(self):
        brand, model = extract_brand_model_from_api_result("", {})
        self.assertEqual(brand, "")
        self.assertEqual(model, "")


class MatchScoringTests(TestCase):
    """score_device_match — all required test cases from spec."""

    def _score(self, sel_brand, sel_model, api_brand, api_model, raw=""):
        confidence, reasons, status = score_device_match(sel_brand, sel_model, api_brand, api_model, raw)
        return confidence, status

    def test_tecno_spark_50_exact_match(self):
        conf, status = self._score("Tecno", "Spark 50", "tecno", "TECNO SPARK 50")
        self.assertEqual(status, "match")
        self.assertGreaterEqual(conf, 85)

    def test_tecno_vs_infinix_is_mismatch(self):
        conf, status = self._score("Tecno", "Spark 20", "infinix", "Hot 40")
        self.assertEqual(status, "mismatch")
        self.assertEqual(conf, 0)

    def test_apple_iphone_15_pro_match(self):
        conf, status = self._score("Apple", "iPhone 15 Pro", "apple", "iPhone 15 Pro (A3104)")
        self.assertEqual(status, "match")
        self.assertGreaterEqual(conf, 85)

    def test_redmi_vs_xiaomi_redmi_13c_is_match_or_possible(self):
        conf, status = self._score("Redmi", "13C", "xiaomi", "Xiaomi Redmi 13C")
        self.assertIn(status, ("match", "possible_match"))

    def test_samsung_a05_vs_samsung_galaxy_a05_is_match_or_possible(self):
        conf, status = self._score("Samsung", "A05", "samsung", "Samsung Galaxy A05")
        self.assertIn(status, ("match", "possible_match"))

    def test_tecno_spark_50_vs_spark_50c_is_possible_or_mismatch(self):
        conf, status = self._score("Tecno", "Spark 50", "tecno", "TECNO SPARK 50C")
        self.assertIn(status, ("possible_match", "mismatch"))

    def test_missing_api_model_returns_unknown(self):
        _, _, status = score_device_match("Tecno", "Spark 50", "tecno", "")
        self.assertEqual(status, "unknown")

    def test_conflicting_brands_always_mismatch(self):
        _, _, status = score_device_match("Samsung", "A05", "tecno", "Spark 50")
        self.assertEqual(status, "mismatch")

    def test_no_api_data_returns_unknown(self):
        _, _, status = score_device_match("Tecno", "Spark 50", "", "")
        self.assertEqual(status, "unknown")


class VerifyImeiTests(TestCase):
    """verify_imei_against_selected_device — integration-style tests with mocked API."""

    def _make_deal(self, brand_name="Tecno", model_name="Spark 50"):
        brand = MagicMock()
        brand.name = brand_name
        deal = MagicMock()
        deal.brand = brand
        deal.model_name = model_name
        return deal

    @patch("applications.services.imei_client.ImeiCheckClient")
    @override_settings(IMEI_CHECK_ENABLED=True, IMEI_CHECK_STRICT_MODE=True)
    def test_matching_imei_returns_match(self, MockClient):
        MockClient.return_value.check_imei.return_value = {
            "success": True,
            "imei": "358089361347363",
            "order_id": "111",
            "status": "success",
            "raw_result": "Brand: Tecno Telecom (HK) Limited\nModel: TECNO SPARK 50",
            "object": {},
            "price": "0.01",
            "duration": "2s",
            "error": "",
        }
        deal = self._make_deal("Tecno", "Spark 50")
        result = verify_imei_against_selected_device("358089361347363", deal, force_recheck=True)
        self.assertIn(result["match_status"], ("match", "possible_match"))
        self.assertFalse(result.get("should_block", False))

    @patch("applications.services.imei_client.ImeiCheckClient")
    @override_settings(IMEI_CHECK_ENABLED=True, IMEI_CHECK_STRICT_MODE=True)
    def test_mismatched_imei_blocks_in_strict_mode(self, MockClient):
        MockClient.return_value.check_imei.return_value = {
            "success": True,
            "imei": "358089361347363",
            "order_id": "222",
            "status": "success",
            "raw_result": "Brand: Infinix\nModel: HOT 40",
            "object": {},
            "price": "0.01",
            "duration": "2s",
            "error": "",
        }
        deal = self._make_deal("Tecno", "Spark 20 Pro")
        result = verify_imei_against_selected_device("358089361347363", deal, force_recheck=True)
        self.assertEqual(result["match_status"], "mismatch")
        self.assertTrue(result["should_block"])

    @patch("applications.services.imei_client.ImeiCheckClient")
    @override_settings(IMEI_CHECK_ENABLED=True, IMEI_CHECK_STRICT_MODE=False)
    def test_mismatched_imei_does_not_block_in_non_strict_mode(self, MockClient):
        MockClient.return_value.check_imei.return_value = {
            "success": True,
            "imei": "358089361347363",
            "order_id": "333",
            "status": "success",
            "raw_result": "Brand: Infinix\nModel: HOT 40",
            "object": {},
            "price": "0.01",
            "duration": "2s",
            "error": "",
        }
        deal = self._make_deal("Tecno", "Spark 20 Pro")
        result = verify_imei_against_selected_device("358089361347363", deal, force_recheck=True)
        self.assertEqual(result["match_status"], "mismatch")
        self.assertFalse(result["should_block"])

    @patch("applications.services.imei_client.ImeiCheckClient")
    @override_settings(IMEI_CHECK_ENABLED=True)
    def test_api_failure_returns_api_error(self, MockClient):
        MockClient.return_value.check_imei.return_value = {
            "success": False,
            "imei": "358089361347363",
            "order_id": "",
            "status": "error",
            "raw_result": "",
            "object": {},
            "price": "",
            "duration": "",
            "error": "Service unavailable",
        }
        deal = self._make_deal("Tecno", "Spark 50")
        result = verify_imei_against_selected_device("358089361347363", deal, force_recheck=True)
        self.assertEqual(result["match_status"], "api_error")
        self.assertFalse(result["success"])

    @patch("applications.services.imei_client.ImeiCheckClient")
    @override_settings(IMEI_CHECK_ENABLED=True)
    def test_cached_result_prevents_duplicate_api_call(self, MockClient):
        from django.core.cache import cache
        imei = "123456789012345"
        cache_key = f"imei_check:{imei}"
        cache.set(cache_key, {
            "api_brand": "tecno",
            "api_model": "Spark 50",
            "api_raw_result": "Model: TECNO SPARK 50",
            "order_id": "cached-999",
            "raw_response": {},
        }, 3600)
        deal = self._make_deal("Tecno", "Spark 50")
        result = verify_imei_against_selected_device(imei, deal, force_recheck=False)
        MockClient.return_value.check_imei.assert_not_called()
        self.assertIn("from cache", " ".join(result["reasons"]).lower())
        cache.delete(cache_key)

    @override_settings(IMEI_CHECK_ENABLED=True)
    def test_invalid_imei_returns_api_error(self):
        deal = self._make_deal("Tecno", "Spark 50")
        result = verify_imei_against_selected_device("123", deal)
        self.assertEqual(result["match_status"], "api_error")

    @override_settings(IMEI_CHECK_ENABLED=False)
    def test_disabled_check_returns_api_error(self):
        deal = self._make_deal("Tecno", "Spark 50")
        result = verify_imei_against_selected_device("358089361347363", deal)
        self.assertEqual(result["match_status"], "api_error")


class GenderChoicesTests(TestCase):
    """Gender field must only offer Male and Female — no 'Prefer not to say'."""

    def test_gender_field_has_male_and_female(self):
        form = CustomerDetailsForm()
        choice_values = [value for value, _label in form.fields["gender"].choices]
        self.assertIn("male", choice_values)
        self.assertIn("female", choice_values)

    def test_gender_field_does_not_have_other(self):
        form = CustomerDetailsForm()
        choice_values = [value for value, _label in form.fields["gender"].choices]
        self.assertNotIn("other", choice_values)

    def test_gender_labels_do_not_include_prefer_not_to_say(self):
        form = CustomerDetailsForm()
        choice_labels = [label.lower() for _value, label in form.fields["gender"].choices]
        self.assertFalse(
            any("prefer" in label or "not to say" in label for label in choice_labels),
            "Gender choices must not include 'Prefer not to say'",
        )

    def test_gender_other_value_is_not_valid(self):
        data = valid_customer_data(gender="other")
        form = CustomerDetailsForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("gender", form.errors)


class ApplicationPageLoadTests(TestCase):
    """Basic smoke tests: key pages must return 200 for the right role."""

    def setUp(self):
        call_command("seed_roles")
        self.User = get_user_model()

        self.merchant = self.User.objects.create_user(username="pl-merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")

        self.underwriter = self.User.objects.create_user(username="pl-underwriter", password="test-pass-123")
        assign_role(self.underwriter, "underwriter")

        self.hq = self.User.objects.create_user(username="pl-hq", password="test-pass-123", is_staff=True)
        assign_role(self.hq, "hq")

        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="pending_review",
        )

    def _login(self, user):
        self.client.force_login(user)

    def test_deals_page_loads_for_merchant(self):
        self._login(self.merchant)
        self.app.customer_name = "Jane Banda"
        self.app.national_id = "RQXFVZC9"
        self.app.customer_phone = "990870616"
        self.app.status = "customer_details"
        self.app.save()
        url = reverse("choose_device", args=[self.app.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a Phone Deal")

    def test_hq_dashboard_loads_for_hq_user(self):
        self._login(self.hq)
        url = reverse("hq_dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_capture_imei_shows_real_submission_progress_overlay(self):
        brand = DeviceBrand.objects.create(name="TECNO")
        deal = DeviceDeal.objects.create(
            brand=brand,
            model_name="Spark 20",
            specs="128GB",
            cash_price=Decimal("350000.00"),
            deposit_percent=Decimal("13.00"),
        )
        self.app.customer_name = "Jane Banda"
        self.app.customer_phone = "0999000000"
        self.app.national_id = "PL123456"
        self.app.status = "approved"
        self.app.deal = deal
        self.app.save(update_fields=["customer_name", "customer_phone", "national_id", "status", "deal"])

        self._login(self.merchant)
        response = self.client.get(reverse("capture_imei", args=[self.app.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="contract-progress-overlay"')
        self.assertContains(response, "Generating final contract")
        self.assertContains(response, "Preparing device locking profile")
        self.assertContains(response, "Saving records and opening contract terms")
        self.assertNotContains(response, "successfully locked")

    def test_underwriter_review_summary_loads(self):
        self.app.claimed_by = self.underwriter
        self.app.status = "under_review"
        self.app.save(update_fields=["claimed_by", "status"])
        self._login(self.underwriter)
        url = reverse("underwriter_review_summary", args=[self.app.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_underwriter_identity_check_loads(self):
        self.app.claimed_by = self.underwriter
        self.app.status = "under_review"
        self.app.save(update_fields=["claimed_by", "status"])
        self._login(self.underwriter)
        url = reverse("underwriter_identity_check", args=[self.app.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_underwriter_final_review_loads(self):
        self.app.claimed_by = self.underwriter
        self.app.status = "under_review"
        self.app.save(update_fields=["claimed_by", "status"])
        self._login(self.underwriter)
        url = reverse("underwriter_final_review", args=[self.app.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class ApprovalStatusTests(TestCase):
    """Verify that approving an application updates status correctly and
    that the detail page shows the post-approval banner."""

    def setUp(self):
        call_command("seed_roles")
        User = get_user_model()
        self.merchant = User.objects.create_user(username="appr-merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="under_review",
            review_status="under_review",
        )

    def test_approved_status_key_is_approved(self):
        self.app.status = "approved"
        self.app.save(update_fields=["status"])
        self.assertEqual(self.app.merchant_status_key, "approved")

    def test_approved_application_not_in_pending_review_queryset(self):
        self.app.status = "approved"
        self.app.save(update_fields=["status"])
        pending = FinancingApplication.objects.filter(status="pending_review")
        self.assertNotIn(self.app, pending)

    def test_approved_application_routes_to_imei_capture(self):
        self.app.status = "approved"
        self.app.save(update_fields=["status"])
        self.assertEqual(
            self.app.get_continue_url(),
            reverse("capture_imei", args=[self.app.id]),
        )

    def test_approved_detail_page_shows_next_step_banner(self):
        self.app.status = "approved"
        self.app.save(update_fields=["status"])
        self.client.force_login(self.merchant)
        url = reverse("application_detail", args=[self.app.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "app-next-step-banner")
        self.assertContains(response, "Application approved")
        self.assertContains(response, "Continue")

    def test_pending_application_does_not_show_next_step_banner(self):
        self.app.status = "pending_review"
        self.app.save(update_fields=["status"])
        self.client.force_login(self.merchant)
        url = reverse("application_detail", args=[self.app.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "app-next-step-banner")

    def test_imei_entry_step_shows_continue_button(self):
        self.app.status = "imei_entry"
        self.app.save(update_fields=["status"])
        self.client.force_login(self.merchant)
        url = reverse("application_detail", args=[self.app.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "app-next-step-banner")

    def test_status_pill_approved_uses_green_class(self):
        """Status pill for approved should carry .status-approved CSS class."""
        self.app.status = "approved"
        self.app.save(update_fields=["status"])
        self.client.force_login(self.merchant)
        url = reverse("application_detail", args=[self.app.id])
        response = self.client.get(url)
        self.assertContains(response, 'status-approved')


# ──────────────────────────────────────────────────────────────────────────────
# Production stability: media, placeholders, end-to-end flow
# ──────────────────────────────────────────────────────────────────────────────

from pathlib import Path

from django.conf import settings as django_settings

from contracts.models import Contract


class ProductionMediaServeTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.settings_override = override_settings(
            DEBUG=False,
            MEDIA_ROOT=self.media_root,
            ROOT_URLCONF="config.urls",
        )
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def test_authenticated_user_can_load_uploaded_kyc_media(self):
        app = self.create_application()
        app.customer_face_image.save("face.png", ContentFile(PNG_BYTES), save=True)
        media_url = app.customer_face_image.url

        response = self.client.get(media_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")


class MotionJsRegressionTests(TestCase):
    def test_motion_js_does_not_render_initials_from_alt_text(self):
        motion_path = Path(django_settings.BASE_DIR) / "static" / "js" / "tengasale-motion.js"
        source = motion_path.read_text(encoding="utf-8")
        self.assertNotIn("slice(0, 2).map", source)
        self.assertIn("Image unavailable", source)


class ApplicationStabilityE2ETests(ApplicationTestCase):
    """
    Merchant application → contract complete → PDF open.
    Asserts real media URLs render and no initials-placeholder regressions.
    """

    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        call_command("seed_legal_documents", verbosity=0)

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def image_upload(self, name):
        return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")

    def location_data(self):
        return {
            "region": "Central",
            "district": "Lilongwe",
            "traditional_authority": "TA Chadza",
            "precise_location": "Area 25",
            "next_of_kin_1_name": "Mary Banda",
            "next_of_kin_1_phone": "991111111",
            "next_of_kin_1_relationship": "Family",
        }

    def work_data(self):
        return {
            "work_description": "Runs a grocery stall",
            "next_of_kin_2_name": "Peter Phiri",
            "next_of_kin_2_phone": "992222222",
            "next_of_kin_2_relationship": "Friend",
            "proof_of_income_type": "MoMo",
            "proof_contact_name": "Airtel Agent",
            "proof_contact_phone": "993333333",
            "proof_notes": "",
        }

    def test_full_merchant_flow_images_signature_and_pdf(self):
        deal = self.create_deal()
        app = self.create_application()
        self.complete_customer_details(app)
        app.national_id = "RQXFVZC9"
        app.save(update_fields=["national_id"])

        self.client.post(
            reverse("choose_device", args=[app.id]),
            {"deal_id": deal.id, "selected_cash_price": "350000"},
        )

        for field in ("customer_face_image", "id_front_image", "id_back_image"):
            response = self.client.post(
                reverse("kyc_save_image", args=[app.id]),
                {"field": field, "image": self.image_upload(f"{field}.png")},
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.json()["ok"])

        app.refresh_from_db()
        kyc_response = self.client.get(reverse("kyc_capture", args=[app.id]))
        self.assertEqual(kyc_response.status_code, 200)
        self.assertContains(kyc_response, app.customer_face_image.url)
        self.assertContains(kyc_response, app.id_front_image.url)
        self.assertContains(kyc_response, app.id_back_image.url)
        self.assertNotContains(kyc_response, 'class="ts-img-fallback"')
        self.client.post(reverse("location_details", args=[app.id]), self.location_data())
        self.client.post(reverse("work_details", args=[app.id]), self.work_data())
        self.client.post(
            reverse("signature", args=[app.id]),
            {"save_signature": "1", "agreed_to_terms": "on", "signature_data": valid_signature_data()},
        )

        app.refresh_from_db()
        review_response = self.client.get(reverse("application_review", args=[app.id]))
        self.assertEqual(review_response.status_code, 200)
        self.assertContains(review_response, app.signature_image.url)
        self.assertNotContains(review_response, ">CS<")

        self.client.post(
            reverse("application_review", args=[app.id]),
            {"agreed_to_terms": "on"},
        )
        app.refresh_from_db()
        app.status = "approved"
        app.calculated_total_loan = deal.total_12_month_price
        app.calculated_deposit_amount = Decimal("113750.00")
        app.calculated_monthly_payment = Decimal("72916.67")
        app.calculated_daily_payment = Decimal("2430.56")
        app.selected_cash_price = Decimal("350000.00")
        app.save()

        self.client.post(reverse("contract_terms", args=[app.id]), {
            "confirmed_terms": "on",
            "accept_contract_summary": "on",
            "accept_master_terms": "on",
            "consent_device_management": "on",
            "consent_communication": "on",
            "confirm_information_true": "on",
        })
        contract, _ = Contract.from_application(app)
        self.client.post(
            reverse("contract_signature", args=[contract.id]),
            {"signature_data": valid_signature_data(), "customer_terms_accepted": "on"},
        )
        contract.refresh_from_db()
        contract.status = Contract.STATUS_IMEI_ENTERED
        contract.imei_number = "123456789012345"
        contract.save()

        for action in ("warranty", "locked", "deposit"):
            self.client.post(reverse("contract_progress", args=[contract.id]), {"action": action})

        contract.refresh_from_db()
        self.assertEqual(contract.status, Contract.STATUS_COMPLETE)

        pdf_response = self.client.get(reverse("contract_pdf_initial", args=[contract.id]))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertGreater(int(pdf_response.get("Content-Length", 0) or 0), 100)

        detail_response = self.client.get(reverse("application_detail", args=[app.id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, app.signature_image.url)
        self.assertNotContains(detail_response, ">TN<")
        self.assertNotContains(detail_response, ">CS<")
        self.assertNotContains(detail_response, 'class="ts-img-fallback"')

    def test_pdf_json_error_not_raw_500_when_generation_fails(self):
        app = self.create_application()
        app.status = "approved"
        app.save(update_fields=["status"])
        contract = Contract.from_application(app)[0]
        contract.initial_pdf = None
        contract.save(update_fields=["initial_pdf"])

        with patch(
            "services.contracts.pdf_contracts.generate_customer_contract_pdf",
            return_value=None,
        ):
            response = self.client.get(
                reverse("contract_pdf_initial", args=[contract.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "Unable to generate contract PDF")
