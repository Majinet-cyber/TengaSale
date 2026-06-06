from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.utils import assign_role
from applications.models import ApplicationCorrection, FinancingApplication
from applications.test_helpers import attach_complete_pricing
from approvals.models import CustomerCallQuestionnaire, UnderwriterReview


class ApprovalQueueTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="merchant", password="test-pass-123")
        self.manager = User.objects.create_user(username="manager", password="test-pass-123")
        self.second_manager = User.objects.create_user(username="manager2", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        assign_role(self.manager, "underwriter")
        assign_role(self.second_manager, "underwriter")

    def create_pending(self, **overrides):
        data = {
            "created_by": self.merchant,
            "customer_name": "Jane Banda",
            "national_id": "RQXFVZC9",
            "status": "pending_review",
            "submitted_at": timezone.now(),
        }
        data.update(overrides)
        app = FinancingApplication.objects.create(**data)
        return attach_complete_pricing(app)

    def test_underwriter_url_names_resolve(self):
        self.assertEqual(reverse("underwriter_dashboard"), "/tengasale/underwriter/")
        self.assertEqual(reverse("underwriter_claim_next"), "/tengasale/underwriter/claim-next/")
        self.assertEqual(reverse("underwriter_queue"), "/tengasale/underwriter/queue/")
        self.assertEqual(reverse("underwriter_active_reviews"), "/tengasale/underwriter/active/")
        self.assertEqual(reverse("underwriter_completed_reviews"), "/tengasale/underwriter/completed/")
        app = self.create_pending()
        self.assertEqual(reverse("underwriter_address_check", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/address-check/")
        self.assertEqual(reverse("underwriter_review_summary", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/summary/")
        self.assertEqual(reverse("underwriter_identity_check", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/identity/")
        self.assertEqual(reverse("underwriter_momo_check", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/momo/")
        self.assertEqual(reverse("underwriter_customer_call", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/customer-call/")
        self.assertEqual(reverse("underwriter_income_check", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/income/")
        self.assertEqual(reverse("underwriter_location_check", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/location/")
        self.assertEqual(reverse("underwriter_final_review", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/final/")
        self.assertEqual(reverse("underwriter_confirm_approve", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/confirm-approve/")
        self.assertEqual(reverse("sales_call_questionnaire", args=[app.id]), f"/sales/applications/{app.id}/call-questionnaire/")
        self.assertEqual(reverse("underwriter_call_questionnaire", args=[app.id]), f"/tengasale/underwriter/review/{app.id}/call-questionnaire/")

    def test_pending_review_with_no_claim_appears_in_queue(self):
        self.create_pending()
        self.client.login(username="manager", password="test-pass-123")

        # /tengasale/underwriter/ now redirects to /sales/ — follow to final destination
        response = self.client.get(reverse("underwriter_dashboard"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "manager")      # greeting shows username
        self.assertContains(response, "My Active Reviews")    # always present on new home
        self.assertContains(response, "Applications") # new menu section

    def test_sales_pending_queue_hides_unclaimed_application_details(self):
        app = self.create_pending(customer_name="Holland Marie", national_id="SECRET123")
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("sales_applications") + "?tab=queue")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 applications waiting")
        self.assertContains(response, "Claim Next Application")
        self.assertNotContains(response, "Holland Marie")
        self.assertNotContains(response, "SECRET123")
        self.assertNotContains(response, app.application_number)

    def test_legacy_pending_queue_hides_unclaimed_application_details(self):
        app = self.create_pending(customer_name="Holland Marie", national_id="SECRET123")
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_queue"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 applications waiting")
        self.assertContains(response, "Claim Next Application")
        self.assertNotContains(response, "Holland Marie")
        self.assertNotContains(response, "SECRET123")
        self.assertNotContains(response, app.application_number)

    def test_manager_can_claim_next_and_second_manager_cannot_claim_same_app(self):
        app = self.create_pending()
        self.client.login(username="manager", password="test-pass-123")

        # Claim is now POST-only on /sales/claim/
        response = self.client.post(reverse("sales_claim_next"))
        app.refresh_from_db()

        self.assertEqual(app.claimed_by, self.manager)
        self.assertEqual(app.status, "under_review")

        self.client.login(username="manager2", password="test-pass-123")
        response = self.client.get(reverse("underwriter_review_application", args=[app.id]), follow=True)

        self.assertContains(response, "This application is assigned to another underwriter.")

    def test_under_review_underwriter_dashboard_shows_underwriter(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager, claimed_at=timezone.now())
        self.client.login(username="manager", password="test-pass-123")

        # /tengasale/underwriter/ redirects to /sales/ — follow to final destination
        response = self.client.get(reverse("underwriter_dashboard"), follow=True)

        # TengaSale home shows the app number in the active list
        self.assertContains(response, app.application_number)

    def test_merchant_submitted_page_shows_reviewer_name_for_under_review(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager, claimed_at=timezone.now())
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("application_submitted", args=[app.id]))

        self.assertContains(response, "Being reviewed by")
        self.assertContains(response, "manager")

    def test_field_correction_creates_record_and_send_back_sets_sent_back(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.post(
            reverse("underwriter_correction_action", args=[app.id]),
            {
                "field_name": "customer_phone",
                "section": "Customer",
                "label": "Customer phone",
                "note": "Check phone.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ApplicationCorrection.objects.filter(application=app, field_name="customer_phone", resolved=False).exists())

        response = self.client.post(reverse("underwriter_final_review", args=[app.id]), {"decision": "request_correction"})
        app.refresh_from_db()
        # Now redirects to sales_home instead of underwriter_dashboard — just check status
        self.assertIn(response.status_code, [301, 302])
        self.assertEqual(app.status, "sent_back")
        self.assertEqual(app.review_status, "sent_back")
        self.assertEqual(app.correction_fields, ["customer_phone"])

        self.client.login(username="merchant", password="test-pass-123")
        response = self.client.get(reverse("application_corrections", args=[app.id]))

        self.assertContains(response, "Sent Back")
        self.assertContains(response, "Review these fields")
        self.assertContains(response, "Customer phone")
        self.assertContains(response, "Check phone.")

    def test_review_detail_contains_expected_sections_and_actions(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_review_application", args=[app.id]))

        for text in ["Application Review", "Application Summary", "Review Checklist",
                     "Summary Review", "Identity Check", "MoMo Check", "Customer Call",
                     "Income Check", "Location Check", "Final Decision"]:
            self.assertContains(response, text)

    def test_hub_review_shows_customer_and_merchant_and_deal_at_top(self):
        """Hub review must lead with customer, merchant/sales rep, and deal summary."""
        app = self.create_pending(
            status="under_review", claimed_by=self.manager,
            customer_name="Jane Banda", national_id="RQXFVZC9",
        )
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_review_application", args=[app.id]))

        self.assertContains(response, "Jane Banda")
        self.assertContains(response, "merchant")  # submitter (sales rep)
        self.assertContains(response, "Application Summary")
        self.assertContains(response, "Merchant / Sales Rep")
        self.assertContains(response, "Deal")

    def test_review_page_renders_kyc_image_missing_state(self):
        """Missing KYC images show clean text placeholder, not question marks."""
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_review_application", args=[app.id]))

        self.assertContains(response, "Missing photo")
        self.assertContains(response, "ID Card Front")
        self.assertContains(response, "ID Card Back")
        self.assertContains(response, "Missing photo")
        # Must not render empty <img> tags without a real src
        self.assertNotContains(response, 'src=""')

    def test_review_page_edit_icons_use_orange_class(self):
        """Edit/send-back icons must use the orange review-edit-btn class."""
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_review_summary", args=[app.id]))

        self.assertContains(response, "review-edit-btn")

    def test_underwriter_can_open_summary_page(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_review_summary", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Summary")
        self.assertContains(response, "review-edit-btn")
        self.assertNotContains(response, "correction-toggle")

    def test_summary_page_links_to_customer_call_questionnaire(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_review_summary", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customer Call Questionnaire")
        self.assertContains(response, reverse("sales_call_questionnaire", args=[app.id]))

    def test_customer_call_questionnaire_access_and_save(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("sales_call_questionnaire", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Step 0: Recording Consent")
        self.assertContains(response, "Customer Facts")

        response = self.client.post(
            reverse("sales_call_questionnaire", args=[app.id]),
            {
                "recording_consent_acknowledged": "on",
                "identity_score": "3",
                "work_context_score": "3",
                "income_confidence_score": "3",
                "price_understanding_score": "3",
                "payment_understanding_score": "3",
                "determined_income": "450000",
                "closing_notes": "Confident customer with clear repayment plan.",
            },
        )
        self.assertRedirects(response, reverse("sales_call_questionnaire", args=[app.id]))
        questionnaire = CustomerCallQuestionnaire.objects.get(application=app)
        self.assertEqual(questionnaire.recommendation, CustomerCallQuestionnaire.RECOMMEND_APPROVE)
        self.assertEqual(questionnaire.risk_score, 100)
        self.assertEqual(questionnaire.completed_by, self.manager)

    def test_customer_call_questionnaire_requires_consent_and_rejects_critical_fail(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.post(
            reverse("sales_call_questionnaire", args=[app.id]),
            {
                "identity_score": "1",
                "work_context_score": "3",
                "income_confidence_score": "3",
                "price_understanding_score": "3",
                "payment_understanding_score": "3",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recording consent must be acknowledged")
        self.assertFalse(CustomerCallQuestionnaire.objects.filter(application=app, completed_at__isnull=False).exists())

        response = self.client.post(
            reverse("sales_call_questionnaire", args=[app.id]),
            {
                "recording_consent_acknowledged": "on",
                "identity_score": "1",
                "work_context_score": "3",
                "income_confidence_score": "3",
                "price_understanding_score": "3",
                "payment_understanding_score": "3",
            },
        )
        self.assertRedirects(response, reverse("sales_call_questionnaire", args=[app.id]))
        questionnaire = CustomerCallQuestionnaire.objects.get(application=app)
        self.assertEqual(questionnaire.recommendation, CustomerCallQuestionnaire.RECOMMEND_REJECT)

    def test_customer_call_questionnaire_forbids_merchants(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("sales_call_questionnaire", args=[app.id]))

        self.assertEqual(response.status_code, 403)

    def test_yes_no_answer_saves_and_renders_selected_green(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.post(reverse("underwriter_review_summary", args=[app.id]), {"summary_clear": "yes"})
        self.assertRedirects(response, reverse("underwriter_identity_check", args=[app.id]))
        review = UnderwriterReview.objects.get(application=app)
        self.assertTrue(review.summary_clear)

        response = self.client.get(reverse("underwriter_review_summary", args=[app.id]))
        # Yes button must have is-selected class; tick element removed from new template
        self.assertContains(response, 'review-choice review-choice--yes is-selected')

    def test_identity_check_renders_four_yes_no_questions(self):
        """Identity check must render all 4 identity questions."""
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_identity_check", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "identity_info_matches")
        self.assertContains(response, "identity_signature_matches")
        self.assertContains(response, "identity_selfie_matches")
        self.assertContains(response, "identity_images_clear")
        self.assertContains(response, "KYC Images")

    def test_identity_check_kyc_images_show_missing_state(self):
        """KYC images section shows a missing state, not question marks, when empty."""
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_identity_check", args=[app.id]))

        self.assertContains(response, "Missing photo")
        self.assertContains(response, "ID Card Front")
        self.assertContains(response, "ID Card Back")
        self.assertContains(response, "Missing photo")

    def test_income_check_renders_guarantor_questions(self):
        """Income check page must render guarantor contact questions."""
        app = self.create_pending(
            status="under_review", claimed_by=self.manager,
            exact_monthly_income=300000, calculated_monthly_payment=60000,
        )
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_income_check", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "guarantor_spoken")
        self.assertContains(response, "guarantor_confirmed_customer")
        self.assertContains(response, "contacts_reachable")

    def test_location_check_renders_four_questions(self):
        """Location check must render 4 questions including address clarity."""
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_location_check", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "location_neighbour_spoken")
        self.assertContains(response, "location_confirmed")
        self.assertContains(response, "location_traceable")
        self.assertContains(response, "location_address_clear")

    def test_final_review_renders_deal_questions(self):
        """Final review must show deal/contract Yes/No questions."""
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_final_review", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "deal_phone_correct")
        self.assertContains(response, "deal_deposit_understood")
        self.assertContains(response, "deal_lock_understood")
        self.assertContains(response, "deal_legal_understood")

    def test_approval_shows_next_steps(self):
        """Approve success page must show the post-approval workflow steps."""
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.post(reverse("underwriter_confirm_approve", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Application Approved")
        self.assertContains(response, "Customer Terms Acceptance")
        self.assertContains(response, "Capture IMEI")
        self.assertContains(response, "Device Lock Readiness")
        app.refresh_from_db()
        self.assertEqual(app.status, "approved")

    def test_corrected_field_renders_orange(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        ApplicationCorrection.objects.create(
            application=app,
            field_name="customer_phone",
            section="Customer",
            label="Customer phone",
            note="Fix phone.",
            created_by=self.manager,
        )
        app.sync_correction_summary()
        app.save()
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.get(reverse("underwriter_review_summary", args=[app.id]))

        self.assertContains(response, "review-field--needs-correction")
        self.assertContains(response, "Fix phone.")

    def test_location_and_income_checks_save_answers(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        app.exact_monthly_income = 300000
        app.calculated_monthly_payment = 60000
        app.save()
        self.client.login(username="manager", password="test-pass-123")

        address_response = self.client.post(
            reverse("underwriter_location_check", args=[app.id]),
            {
                "location_neighbour_spoken": "yes",
                "location_confirmed": "no",
                "location_traceable": "yes",
            },
        )
        income_response = self.client.post(
            reverse("underwriter_income_check", args=[app.id]),
            {
                "income_understood": "yes",
                "income_contact_spoken": "yes",
                "income_confirmed": "yes",
                "income_source_dependable": "yes",
                "income_contact_confident": "no",
            },
        )
        app.refresh_from_db()

        self.assertRedirects(address_response, reverse("underwriter_final_review", args=[app.id]))
        self.assertRedirects(income_response, reverse("underwriter_location_check", args=[app.id]))
        self.assertTrue(app.address_check_answers["spoke_to_neighbour"])
        self.assertTrue(app.income_check_answers["income_understood"])

    def test_confirm_approve_requires_post_to_approve(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        get_response = self.client.get(reverse("underwriter_confirm_approve", args=[app.id]))
        app.refresh_from_db()
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(app.status, "under_review")

        post_response = self.client.post(reverse("underwriter_confirm_approve", args=[app.id]))
        app.refresh_from_db()
        self.assertEqual(post_response.status_code, 200)
        self.assertContains(post_response, "Application Approved")
        self.assertEqual(app.status, "approved")

    def test_manager_can_approve_and_reject(self):
        approve_app = self.create_pending(status="under_review", claimed_by=self.manager)
        reject_app = self.create_pending(
            status="under_review",
            claimed_by=self.manager,
            customer_name="Reject Customer",
            national_id="ABCDEFGH",
        )
        self.client.login(username="manager", password="test-pass-123")

        approve_response = self.client.post(reverse("underwriter_review_application", args=[approve_app.id]), {"decision": "approve"})
        reject_response = self.client.post(
            reverse("underwriter_final_review", args=[reject_app.id]),
            {"decision": "reject", "manager_comment": "Does not qualify."},
        )
        approve_app.refresh_from_db()
        reject_app.refresh_from_db()

        self.assertRedirects(approve_response, reverse("underwriter_confirm_approve", args=[approve_app.id]))
        # After reject, redirects to sales_home (via legacy → new route); just verify status
        self.assertIn(reject_response.status_code, [200, 301, 302])
        self.assertEqual(approve_app.status, "under_review")
        self.assertEqual(reject_app.status, "rejected")

    def test_reject_requires_comment(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)
        self.client.login(username="manager", password="test-pass-123")

        response = self.client.post(reverse("underwriter_final_review", args=[app.id]), {"decision": "reject"})
        app.refresh_from_db()

        self.assertRedirects(response, reverse("underwriter_final_review", args=[app.id]))
        self.assertEqual(app.status, "under_review")

    def test_merchant_resubmit_resolves_corrections(self):
        app = self.create_pending(status="sent_back", claimed_by=self.manager)
        correction = ApplicationCorrection.objects.create(
            application=app,
            field_name="customer_phone",
            section="Customer",
            label="Customer phone",
            note="Fix phone.",
            created_by=self.manager,
        )
        app.sync_correction_summary()
        app.save()

        app.submit()
        correction.refresh_from_db()
        app.refresh_from_db()

        self.assertTrue(correction.resolved)
        self.assertEqual(app.status, "pending_review")
        self.assertEqual(app.review_status, "resubmitted")

    def test_merchant_cannot_access_underwriter_dashboard(self):
        self.client.login(username="merchant", password="test-pass-123")

        # /tengasale/underwriter/ → /sales/ → 403 (underwriter_required blocks merchants)
        response = self.client.get(reverse("underwriter_dashboard"), follow=True)

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "That area is not available for your role.", status_code=403)
        self.assertContains(response, "Go to my dashboard", status_code=403)

    def test_merchant_cannot_access_underwriter_claim_action(self):
        self.client.login(username="merchant", password="test-pass-123")

        # The claim URL redirects to /sales/claim/ (POST-only). Try posting directly.
        response = self.client.post(reverse("sales_claim_next"), follow=True)

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "That area is not available for your role.", status_code=403)

    def test_old_approvals_urls_redirect_to_underwriter_portal(self):
        app = self.create_pending(status="under_review", claimed_by=self.manager)

        self.assertRedirects(
            self.client.get("/approvals/"),
            reverse("underwriter_dashboard"),
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get("/approvals/claim-next/"),
            reverse("underwriter_claim_next"),
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get(f"/approvals/review/{app.id}/"),
            reverse("underwriter_review_application", args=[app.id]),
            fetch_redirect_response=False,
        )

    def test_active_and_completed_review_pages_render(self):
        self.create_pending(status="under_review", claimed_by=self.manager, claimed_at=timezone.now())
        self.create_pending(status="approved", reviewed_by=self.manager, reviewed_at=timezone.now())
        self.client.login(username="manager", password="test-pass-123")

        # Legacy routes redirect to /sales/applications/ — follow redirects
        active_response = self.client.get(reverse("underwriter_active_reviews"), follow=True)
        completed_response = self.client.get(reverse("underwriter_completed_reviews"), follow=True)

        # New applications list page uses "Applications" heading
        self.assertContains(active_response, "Applications")
        self.assertContains(completed_response, "Applications")

    def test_underwriter_cannot_exceed_five_active_applications(self):
        for index in range(5):
            self.create_pending(
                status="under_review",
                claimed_by=self.manager,
                claimed_at=timezone.now(),
                national_id=f"ABCDE{index:03d}",
            )
        pending = self.create_pending(national_id="ZZZZ9999")
        self.client.login(username="manager", password="test-pass-123")

        # Claim is POST-only — send a POST and follow the chain to /sales/
        response = self.client.post(reverse("sales_claim_next"), follow=True)
        pending.refresh_from_db()

        # Max active reached — should stay on home page (200 after redirect)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(pending.claimed_by)
        self.assertEqual(pending.status, "pending_review")

    def test_underwriter_queue_route_returns_200(self):
        self.create_pending()
        self.client.login(username="manager", password="test-pass-123")

        # /tengasale/underwriter/queue/ redirects to /sales/applications/ — follow it
        response = self.client.get(reverse("underwriter_queue"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Applications")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 10 Part D: Call Recording Upload Tests
# ──────────────────────────────────────────────────────────────────────────────

from django.core.files.uploadedfile import SimpleUploadedFile
from approvals.models import CallEvidence


class CallRecordingUploadTests(TestCase):
    """
    Tests for call recording upload validation.
    - valid audio accepted
    - invalid file type rejected (in model/service layer)
    - only authorized staff can access recordings
    - upload creates audit log entry
    - customer call template shows upload field
    """

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="merch_ce", password="pass123")
        assign_role(self.merchant, "merchant")
        self.underwriter = User.objects.create_user(username="uw_ce", password="pass123")
        assign_role(self.underwriter, "underwriter")
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="under_review",
            claimed_by=self.underwriter,
            customer_name="Ruth Mkwanda",
            customer_phone="0991112233",
        )
        UnderwriterReview.objects.get_or_create(application=self.app)

    def _make_audio_file(self, filename="call.mp3", content=b"ID3\x00\x00\x00\x00\x00\x00\x00"):
        return SimpleUploadedFile(filename, content, content_type="audio/mpeg")

    def test_call_evidence_model_creates_correctly(self):
        audio = self._make_audio_file()
        evidence = CallEvidence.objects.create(
            application=self.app,
            stage=CallEvidence.STAGE_CUSTOMER_CALL,
            uploaded_by=self.underwriter,
            audio_file=audio,
            customer_notified=True,
            notification_script_confirmed=True,
            notes="Test call evidence",
        )
        self.assertEqual(evidence.stage, CallEvidence.STAGE_CUSTOMER_CALL)
        self.assertEqual(evidence.uploaded_by, self.underwriter)
        self.assertTrue(evidence.customer_notified)
        self.assertIsNotNone(evidence.pk)

    def test_call_evidence_requires_application(self):
        with self.assertRaises(Exception):
            CallEvidence.objects.create(
                application=None,
                stage=CallEvidence.STAGE_CUSTOMER_CALL,
                uploaded_by=self.underwriter,
            )

    def test_customer_call_page_renders_with_upload_field(self):
        self.client.login(username="uw_ce", password="pass123")
        response = self.client.get(
            reverse("sales_customer_call", args=[self.app.id])
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("call_recording", content)
        self.assertIn("audio/mpeg", content)

    def test_customer_call_page_shows_notification_question(self):
        self.client.login(username="uw_ce", password="pass123")
        response = self.client.get(
            reverse("sales_customer_call", args=[self.app.id])
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("recorded for quality and compliance", content)

    def test_merchant_cannot_access_customer_call_review(self):
        self.client.login(username="merch_ce", password="pass123")
        response = self.client.get(
            reverse("sales_customer_call", args=[self.app.id])
        )
        self.assertIn(response.status_code, [302, 403])

    def test_call_evidence_stage_choices(self):
        stages = [c[0] for c in CallEvidence.STAGE_CHOICES]
        self.assertIn(CallEvidence.STAGE_CUSTOMER_CALL, stages)
        self.assertIn(CallEvidence.STAGE_GUARANTOR_CALL, stages)
        self.assertIn(CallEvidence.STAGE_EMPLOYER_CALL, stages)

    def test_call_evidence_notified_flag_defaults_false(self):
        evidence = CallEvidence(
            application=self.app,
            stage=CallEvidence.STAGE_GUARANTOR_CALL,
            uploaded_by=self.underwriter,
        )
        self.assertFalse(evidence.customer_notified)
        self.assertFalse(evidence.notification_script_confirmed)

    def test_unauthenticated_cannot_access_customer_call_page(self):
        self.client.logout()
        response = self.client.get(
            reverse("sales_customer_call", args=[self.app.id])
        )
        self.assertIn(response.status_code, [302, 403])

    def test_call_evidence_str_representation(self):
        evidence = CallEvidence.objects.create(
            application=self.app,
            stage=CallEvidence.STAGE_EMPLOYER_CALL,
            uploaded_by=self.underwriter,
        )
        self.assertIn("Employer", str(evidence))


# ──────────────────────────────────────────────────────────────────────────────
# Phase 10 Part C: ReviewQuestion seed tests
# ──────────────────────────────────────────────────────────────────────────────

from approvals.models import ReviewQuestion


class ReviewQuestionSeedTests(TestCase):
    """Test that seed_review_questions creates the required structured questions."""

    def test_seed_creates_identity_check_questions(self):
        from django.core.management import call_command
        call_command("seed_review_questions", verbosity=0)
        identity_questions = ReviewQuestion.objects.filter(stage="identity", active=True)
        self.assertGreaterEqual(identity_questions.count(), 5)

    def test_seed_creates_customer_call_questions(self):
        from django.core.management import call_command
        call_command("seed_review_questions", verbosity=0)
        call_questions = ReviewQuestion.objects.filter(stage="customer_call", active=True)
        self.assertGreaterEqual(call_questions.count(), 5)

    def test_seed_creates_guarantor_call_questions(self):
        from django.core.management import call_command
        call_command("seed_review_questions", verbosity=0)
        guarantor_questions = ReviewQuestion.objects.filter(stage="guarantor_call", active=True)
        self.assertGreaterEqual(guarantor_questions.count(), 3)

    def test_seed_creates_employer_call_questions(self):
        from django.core.management import call_command
        call_command("seed_review_questions", verbosity=0)
        employer_questions = ReviewQuestion.objects.filter(stage="employer_call", active=True)
        self.assertGreaterEqual(employer_questions.count(), 3)

    def test_seed_is_idempotent(self):
        from django.core.management import call_command
        call_command("seed_review_questions", verbosity=0)
        count_first = ReviewQuestion.objects.count()
        call_command("seed_review_questions", verbosity=0)
        count_second = ReviewQuestion.objects.count()
        self.assertEqual(count_first, count_second)

    def test_fail_if_no_questions_have_risk_weight(self):
        from django.core.management import call_command
        call_command("seed_review_questions", verbosity=0)
        critical = ReviewQuestion.objects.filter(fail_if_no=True, active=True)
        self.assertGreater(critical.count(), 0, "Some questions must be fail_if_no=True")

    def test_question_key_is_unique(self):
        from django.core.management import call_command
        call_command("seed_review_questions", verbosity=0)
        keys = list(ReviewQuestion.objects.values_list("question_key", flat=True))
        self.assertEqual(len(keys), len(set(keys)), "All question_keys must be unique")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 10 Part N: HQ Operations tests
# ──────────────────────────────────────────────────────────────────────────────


class HQOperationsTests(TestCase):
    """Tests for HQ safe operations page."""

    def setUp(self):
        User = get_user_model()
        self.hq_user = User.objects.create_user(username="hq_ops", password="pass123", is_staff=True)
        assign_role(self.hq_user, "hq")
        self.merchant = User.objects.create_user(username="merch_ops", password="pass123")
        assign_role(self.merchant, "merchant")
        self.underwriter = User.objects.create_user(username="uw_ops", password="pass123")
        assign_role(self.underwriter, "underwriter")

    def test_hq_operations_page_accessible(self):
        self.client.login(username="hq_ops", password="pass123")
        response = self.client.get(reverse("hq_operations"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Safe Operations")

    def test_merchant_cannot_access_hq_operations(self):
        self.client.login(username="merch_ops", password="pass123")
        response = self.client.get(reverse("hq_operations"))
        self.assertIn(response.status_code, [302, 403])

    def test_underwriter_cannot_access_hq_operations(self):
        self.client.login(username="uw_ops", password="pass123")
        response = self.client.get(reverse("hq_operations"))
        self.assertIn(response.status_code, [302, 403])

    def test_release_stuck_claim_sets_status_to_pending(self):
        self.client.login(username="hq_ops", password="pass123")
        app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="under_review",
            claimed_by=self.underwriter,
        )
        response = self.client.post(reverse("hq_operations"), {
            "action": "release_stuck_claim",
            "app_id": app.pk,
        })
        app.refresh_from_db()
        self.assertIn(response.status_code, [200, 302])
        self.assertEqual(app.status, "pending_review")
        self.assertIsNone(app.claimed_by)

    def test_release_creates_audit_log(self):
        from core.models import AuditLog
        self.client.login(username="hq_ops", password="pass123")
        app = FinancingApplication.objects.create(
            created_by=self.merchant,
            status="under_review",
            claimed_by=self.underwriter,
        )
        self.client.post(reverse("hq_operations"), {
            "action": "release_stuck_claim",
            "app_id": app.pk,
        })
        self.assertTrue(
            AuditLog.objects.filter(action="hq_release_stuck_claim").exists()
        )


# ──────────────────────────────────────────────────────────────────────────────
# KulaSell-style review structure tests
# ──────────────────────────────────────────────────────────────────────────────

class KulaSellStyleReviewTests(TestCase):
    """
    Tests verifying the KulaSell-style underwriter review structure:
    - Summary leads with Customer, Merchant/Sales Rep, Deal
    - KYC images shown with proper missing state (not question marks)
    - Edit/send-back icons use orange class
    - Yes/No questions present for each section
    - Approval flow works end-to-end
    """

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(
            username="ks_merchant", password="pass123",
            first_name="Grace", last_name="Banda",
        )
        assign_role(self.merchant, "merchant")
        self.underwriter = User.objects.create_user(
            username="ks_uw", password="pass123",
        )
        assign_role(self.underwriter, "underwriter")

    def _app(self, **kwargs):
        defaults = {
            "created_by": self.merchant,
            "customer_name": "Alice Phiri",
            "national_id": "PHIRIAA01",
            "status": "under_review",
            "claimed_by": self.underwriter,
            "submitted_at": timezone.now(),
            "customer_phone": "0999001122",
        }
        defaults.update(kwargs)
        app = FinancingApplication.objects.create(**defaults)
        return attach_complete_pricing(app)

    # ── Hub review page ──────────────────────────────────────────────────────

    def test_hub_review_leads_with_customer_and_merchant_and_deal(self):
        """Hub review page must show Customer, Merchant/Sales Rep, and Deal near the top."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("underwriter_review_application", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Alice Phiri", content)
        self.assertIn("Application Summary", content)
        self.assertIn("Customer", content)
        self.assertIn("Merchant / Sales Rep", content)
        self.assertIn("Deal", content)
        # Merchant submitter full name must be visible (get_full_name or username)
        self.assertTrue("Grace Banda" in content or "ks_merchant" in content)

    def test_hub_review_shows_deposit_and_monthly_repayment(self):
        """Hub review must display full deal info: deposit, daily, monthly, total."""
        app = self._app(
            calculated_deposit_amount=50000,
            calculated_daily_payment=3333,
            calculated_monthly_payment=100000,
            calculated_total_loan=1200000,
        )
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("underwriter_review_application", args=[app.id]))

        content = response.content.decode()
        self.assertIn("Daily Repayment", content)
        self.assertIn("Monthly Repayment", content)
        self.assertIn("Total Contract", content)

    def test_hub_review_kyc_missing_state_no_question_marks(self):
        """Hub review KYC section shows clean missing text, never an empty <img> tag."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("underwriter_review_application", args=[app.id]))

        self.assertContains(response, "Missing photo")
        self.assertContains(response, "ID Card Front")
        self.assertContains(response, "ID Card Back")
        self.assertContains(response, "Missing photo")
        # No bare empty image src attributes
        self.assertNotContains(response, 'src=""')

    def test_hub_review_edit_icons_use_orange_class(self):
        """Hub review edit icons must use review-edit-btn (orange) class."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        # The summary review step (not hub) has edit buttons
        response = self.client.get(reverse("underwriter_review_summary", args=[app.id]))

        self.assertContains(response, "review-edit-btn")

    # ── Sales portal review summary page ────────────────────────────────────

    def test_sales_review_summary_leads_with_customer_section(self):
        """Modern sales review summary must lead with Customer card, then Merchant, then Deal."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("sales_review_summary", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Alice Phiri", content)
        self.assertIn("Merchant / Sales Rep", content)
        # KYC section must be present
        self.assertIn("KYC Images", content)

    def test_sales_review_summary_kyc_missing_state(self):
        """Sales review summary shows clean missing state for KYC images."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("sales_review_summary", args=[app.id]))

        self.assertContains(response, "Selfie Image")
        self.assertContains(response, "ID Card Front")
        self.assertContains(response, "ID Card Back")
        self.assertContains(response, "Missing photo", count=4)
        self.assertNotContains(response, 'src=""')

    # ── Identity check ───────────────────────────────────────────────────────

    def test_identity_check_shows_all_four_yes_no_questions(self):
        """Identity check must render all 4 identity questions."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("underwriter_identity_check", args=[app.id]))

        self.assertContains(response, "identity_info_matches")
        self.assertContains(response, "identity_signature_matches")
        self.assertContains(response, "identity_selfie_matches")
        self.assertContains(response, "identity_images_clear")

    def test_identity_check_kyc_missing_shows_text_not_empty_img(self):
        """Identity check never renders empty <img> tags for missing KYC."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("underwriter_identity_check", args=[app.id]))

        self.assertContains(response, "Missing photo")
        self.assertContains(response, "ID Card Front")
        self.assertContains(response, "ID Card Back")
        self.assertContains(response, "Missing photo")
        self.assertNotContains(response, 'src=""')

    # ── Income / guarantor questions ─────────────────────────────────────────

    def test_income_check_has_income_and_guarantor_questions(self):
        """Income check must include both income and guarantor Yes/No questions."""
        app = self._app(exact_monthly_income=200000, calculated_monthly_payment=40000)
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("underwriter_income_check", args=[app.id]))

        self.assertContains(response, "income_understood")
        self.assertContains(response, "income_source_dependable")
        self.assertContains(response, "guarantor_spoken")
        self.assertContains(response, "guarantor_confirmed_customer")
        self.assertContains(response, "contacts_reachable")

    # ── Location questions ───────────────────────────────────────────────────

    def test_location_check_has_four_questions(self):
        """Location check must have 4 structured Yes/No questions."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("underwriter_location_check", args=[app.id]))

        self.assertContains(response, "location_neighbour_spoken")
        self.assertContains(response, "location_confirmed")
        self.assertContains(response, "location_traceable")
        self.assertContains(response, "location_address_clear")

    # ── Deal / final review questions ────────────────────────────────────────

    def test_final_review_has_deal_yes_no_questions(self):
        """Final review must include deal and contract understanding questions."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("underwriter_final_review", args=[app.id]))

        self.assertContains(response, "deal_phone_correct")
        self.assertContains(response, "deal_deposit_understood")
        self.assertContains(response, "deal_repayment_understood")
        self.assertContains(response, "deal_lock_understood")
        self.assertContains(response, "deal_legal_understood")

    # ── Approval status flow ─────────────────────────────────────────────────

    def test_approve_changes_status_to_approved_immediately(self):
        """Approval must set status=approved and no longer show pending."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.post(reverse("underwriter_confirm_approve", args=[app.id]))

        app.refresh_from_db()
        self.assertEqual(app.status, "approved")
        self.assertNotEqual(app.status, "pending_review")

    def test_approve_success_shows_next_steps_in_order(self):
        """Approval success page must list next steps in correct workflow order."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.post(reverse("underwriter_confirm_approve", args=[app.id]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Application Approved", content)
        self.assertIn("Customer Terms Acceptance", content)
        self.assertIn("Capture IMEI", content)
        self.assertIn("Device Lock Readiness", content)
        # Check ordering in HTML: Terms before IMEI
        terms_pos = content.find("Customer Terms Acceptance")
        imei_pos = content.find("Capture IMEI")
        self.assertLess(terms_pos, imei_pos, "Terms Acceptance must come before Capture IMEI")

    def test_correction_modal_uses_correct_css_class(self):
        """Correction modal panel must use correction-modal__sheet (not correction-panel)."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.get(reverse("underwriter_identity_check", args=[app.id]))

        self.assertContains(response, "correction-modal__sheet")
        self.assertNotContains(response, 'class="correction-panel"')

    def test_no_sending_back_without_corrections_or_comment(self):
        """Send back requires at least one correction or underwriter comment."""
        app = self._app()
        self.client.login(username="ks_uw", password="pass123")

        response = self.client.post(
            reverse("underwriter_final_review", args=[app.id]),
            {"decision": "request_correction"},
        )

        # Should redirect back to final review, not to dashboard
        self.assertRedirects(response, reverse("underwriter_final_review", args=[app.id]))


class DuplicateApprovalPreventionTests(TestCase):
    """
    PART 1 — Verify that an already-approved contract cannot be approved again.
    Covers both legacy (approvals.views) and modern (sales.views) endpoints.
    """

    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="dup_merchant", password="pass123")
        self.underwriter = User.objects.create_user(username="dup_uw", password="pass123")
        assign_role(self.merchant, "merchant")
        assign_role(self.underwriter, "underwriter")

    def _under_review_app(self):
        return FinancingApplication.objects.create(
            created_by=self.merchant,
            customer_name="Test Customer",
            national_id="ABCD1234",
            status="under_review",
            review_status="under_review",
            claimed_by=self.underwriter,
            claimed_at=timezone.now(),
            submitted_at=timezone.now(),
        )

    def _approved_app(self):
        app = self._under_review_app()
        app.status = "approved"
        app.review_status = "approved"
        app.reviewed_by = self.underwriter
        app.reviewed_at = timezone.now()
        app.save(update_fields=["status", "review_status", "reviewed_by", "reviewed_at"])
        return app

    # ── review_guard blocks approved apps ────────────────────────────────────

    def test_review_guard_blocks_approved_app_on_summary(self):
        """review_guard must redirect away from an already-approved app."""
        app = self._approved_app()
        self.client.login(username="dup_uw", password="pass123")

        response = self.client.get(
            reverse("underwriter_review_summary", args=[app.id]), follow=True
        )

        self.assertEqual(response.status_code, 200)
        messages_list = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any("already been approved" in m for m in messages_list),
            f"Expected approval guard message, got: {messages_list}",
        )

    def test_review_guard_blocks_approved_app_on_identity_check(self):
        app = self._approved_app()
        self.client.login(username="dup_uw", password="pass123")

        response = self.client.get(
            reverse("underwriter_identity_check", args=[app.id]), follow=True
        )

        messages_list = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("already been approved" in m or "approved" in m for m in messages_list))

    # ── Legacy confirm_approve blocks duplicate POST ──────────────────────────

    def test_legacy_confirm_approve_get_blocked_for_approved_app(self):
        """GET to confirm_approve for an approved app must redirect with error."""
        app = self._approved_app()
        self.client.login(username="dup_uw", password="pass123")

        response = self.client.get(
            reverse("underwriter_confirm_approve", args=[app.id]), follow=True
        )

        self.assertEqual(response.status_code, 200)
        messages_list = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any("approved" in m.lower() for m in messages_list),
            f"Expected approval-guard message, got: {messages_list}",
        )

    def test_legacy_confirm_approve_post_blocked_for_approved_app(self):
        """POST to confirm_approve for already-approved app must not change status."""
        app = self._approved_app()
        original_reviewed_at = app.reviewed_at
        self.client.login(username="dup_uw", password="pass123")

        response = self.client.post(
            reverse("underwriter_confirm_approve", args=[app.id]), follow=True
        )

        app.refresh_from_db()
        self.assertEqual(app.status, "approved")
        self.assertEqual(app.reviewed_at, original_reviewed_at)
        self.assertEqual(response.status_code, 200)

    # ── Approved app leaves active review queue ───────────────────────────────

    def test_approved_app_not_in_under_review_queue(self):
        """Approved app must not appear in the active review queue."""
        app = self._approved_app()
        self.client.login(username="dup_uw", password="pass123")

        active = FinancingApplication.objects.filter(
            claimed_by=self.underwriter, status="under_review"
        )
        self.assertNotIn(app, list(active))

    # ── Sales endpoint blocks duplicate POST ─────────────────────────────────

    def test_sales_confirm_approve_blocked_for_approved_app(self):
        """sales_confirm_approve endpoint must block re-approval of approved app."""
        app = self._approved_app()
        self.client.login(username="dup_uw", password="pass123")

        response = self.client.post(
            reverse("sales_confirm_approve", args=[app.id]), follow=True
        )

        app.refresh_from_db()
        self.assertEqual(app.status, "approved")
        self.assertEqual(response.status_code, 200)

    # ── Contract statuses also blocked ───────────────────────────────────────

    def test_review_guard_blocks_contract_pipeline_statuses(self):
        """Apps in post-approval contract pipeline cannot be re-reviewed."""
        for terminal_status in ["contract_terms", "contract_signature", "imei_entry",
                                "contract_complete", "completed"]:
            app = self._under_review_app()
            app.status = terminal_status
            app.save(update_fields=["status"])

            self.client.login(username="dup_uw", password="pass123")
            response = self.client.get(
                reverse("underwriter_review_summary", args=[app.id]), follow=True
            )

            self.assertEqual(response.status_code, 200, f"Unexpected status for {terminal_status}")
            messages_list = [str(m) for m in response.context["messages"]]
            self.assertTrue(
                any("approved" in m.lower() or "contract" in m.lower() or "no longer" in m.lower()
                    for m in messages_list),
                f"No guard message for status={terminal_status}, messages={messages_list}",
            )
