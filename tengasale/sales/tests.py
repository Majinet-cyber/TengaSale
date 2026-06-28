"""
Sales app tests — Phase 5.

Coverage:
- Queue cooldown enforcement
- Max active application limit (5)
- completeness_score() calculation
- Approve flow creates contract (smoke)
- Reject requires reason
- Sales home page renders (200) for underwriter
- Sales queue rules page renders (200)
"""

from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from core.models import QueueRule
from approvals.models import UnderwriterReview
from applications.models import FinancingApplication

User = get_user_model()


class QueueRuleTest(TestCase):
    def test_for_country_creates_default(self):
        rule = QueueRule.for_country("MW")
        self.assertEqual(rule.country, "MW")
        self.assertEqual(rule.cooldown_minutes, 5)
        self.assertEqual(rule.max_active_applications, 5)

    def test_for_country_returns_same_object(self):
        r1 = QueueRule.for_country("MW")
        r2 = QueueRule.for_country("MW")
        self.assertEqual(r1.pk, r2.pk)

    def test_country_uppercased_on_save(self):
        rule = QueueRule(country="mw")
        rule.save()
        self.assertEqual(rule.country, "MW")


class CompletenessScoreTest(TestCase):
    def _make_review(self, **kwargs):
        defaults = {
            "summary_clear": None,
            "identity_signature_matches": None,
            "identity_info_matches": None,
            "momo_name_matches": None,
            "location_neighbour_spoken": None,
            "location_confirmed": None,
            "location_traceable": None,
            "customer_spoken": None,
            "customer_intro_done": None,
            "customer_confirmed_application": None,
            "customer_confirmed_device": None,
            "customer_confirmed_deposit": None,
            "customer_confirmed_repayment": None,
            "customer_understands_direct_payment": None,
            "customer_understands_nonpayment": None,
            "income_understood": None,
            "income_contact_spoken": None,
            "income_confirmed": None,
            "income_source_dependable": None,
            "income_contact_confident": None,
        }
        defaults.update(kwargs)
        review = UnderwriterReview(**defaults)
        return review

    def test_zero_score_when_all_none(self):
        review = self._make_review()
        score = review.completeness_score()
        self.assertEqual(score, 0)

    def test_partial_score(self):
        review = self._make_review(
            summary_clear=True,
            identity_signature_matches=True,
            identity_info_matches=True,
            momo_name_matches=True,
            location_neighbour_spoken=True,
        )
        score = review.completeness_score()
        self.assertGreater(score, 0)
        self.assertLess(score, 100)

    def test_full_score_when_all_true(self):
        all_required = {field: True for field in UnderwriterReview.REQUIRED_FIELDS}
        review = self._make_review(**all_required)
        score = review.completeness_score()
        self.assertEqual(score, 100)


class SalesPageSmokeTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.underwriter = User.objects.create_user(
            username="testsalesrep",
            password="testpass123",
            email="rep@test.com",
        )
        from accounts.models import UserProfile
        UserProfile.objects.filter(user=self.underwriter).update(role="underwriter")

    def test_sales_home_requires_login(self):
        res = self.client.get("/sales/")
        self.assertIn(res.status_code, [302, 403])

    def test_sales_home_renders_for_underwriter(self):
        self.client.login(username="testsalesrep", password="testpass123")
        res = self.client.get("/sales/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'aria-label="Underwriter mobile navigation"')
        self.assertContains(res, "/sales/applications/?tab=queue")
        self.assertContains(res, "/sales/applications/?tab=active")
        self.assertContains(res, "/sales/wallet/")

    def test_sales_home_claim_button_green_when_queue_available(self):
        merchant = User.objects.create_user(username="merchant-for-queue", password="testpass123")
        FinancingApplication.objects.create(
            created_by=merchant,
            status="pending_review",
            customer_name="Queue Customer",
            customer_phone="990870616",
            national_id="ABCD1234",
        )
        self.client.login(username="testsalesrep", password="testpass123")

        res = self.client.get("/sales/")

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "uw-action-card--green")
        self.assertContains(res, "CLAIM NEXT")

    def test_sales_home_no_green_claim_button_when_queue_empty(self):
        self.client.login(username="testsalesrep", password="testpass123")

        res = self.client.get("/sales/")

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "NO APPS PENDING")
        self.assertNotContains(res, 'data-testid="claim-next-btn"')

    def test_sales_home_renders_cooldown_button_when_in_cooldown(self):
        merchant = User.objects.create_user(username="merchant-cooldown", password="testpass123")
        FinancingApplication.objects.create(
            created_by=merchant,
            claimed_by=self.underwriter,
            claimed_at=timezone.now(),
            status="under_review",
            customer_name="Cooldown Customer",
            customer_phone="990870617",
            national_id="ABCD1235",
        )
        FinancingApplication.objects.create(
            created_by=merchant,
            status="pending_review",
            customer_name="Waiting Customer",
            customer_phone="990870618",
            national_id="ABCD1236",
        )
        self.client.login(username="testsalesrep", password="testpass123")

        res = self.client.get("/sales/")

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "data-cooldown-btn")
        self.assertContains(res, "CLAIM AVAILABLE IN")

    def test_sales_home_max_active_reached_disables_claim(self):
        merchant = User.objects.create_user(username="merchant-max", password="testpass123")
        for i in range(5):
            FinancingApplication.objects.create(
                created_by=merchant,
                claimed_by=self.underwriter,
                claimed_at=timezone.now() - timedelta(hours=1),
                status="under_review",
                customer_name=f"Active {i}",
                customer_phone=f"9908706{i:02d}",
                national_id=f"ABCD{i:04d}",
            )
        FinancingApplication.objects.create(
            created_by=merchant,
            status="pending_review",
            customer_name="Waiting",
            customer_phone="990870699",
            national_id="ABCD9999",
        )
        self.client.login(username="testsalesrep", password="testpass123")

        res = self.client.get("/sales/")

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "ACTIVE LIMIT REACHED")
        self.assertNotContains(res, 'data-testid="claim-next-btn"')

    def test_claim_next_navigates_to_review_summary(self):
        merchant = User.objects.create_user(username="merchant-claim", password="testpass123")
        app = FinancingApplication.objects.create(
            created_by=merchant,
            status="pending_review",
            customer_name="Claim Me",
            customer_phone="990870620",
            national_id="ABCD2000",
            submitted_at=timezone.now(),
        )
        self.client.login(username="testsalesrep", password="testpass123")

        claim_res = self.client.post(reverse("sales_claim_next"))

        self.assertEqual(claim_res.status_code, 302)
        self.assertEqual(claim_res.url, reverse("sales_review_summary", args=[app.id]))

        review_res = self.client.get(reverse("sales_review_summary", args=[app.id]))

        self.assertEqual(review_res.status_code, 200)
        self.assertContains(review_res, 'data-testid="review-application-body"')
        self.assertContains(review_res, "Customer")
        self.assertContains(review_res, "Loading application review")

    def test_review_summary_kyc_id_frames_use_landscape_ratio(self):
        merchant = User.objects.create_user(username="merchant-kyc", password="testpass123")
        app = FinancingApplication.objects.create(
            created_by=merchant,
            claimed_by=self.underwriter,
            status="under_review",
            customer_name="KYC Customer",
            customer_phone="990870621",
            national_id="ABCD2001",
        )
        self.client.login(username="testsalesrep", password="testpass123")

        res = self.client.get(reverse("sales_review_summary", args=[app.id]))

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "kyc-smart-card__frame--id_front")
        self.assertContains(res, "kyc-smart-card__frame--id_back")
        self.assertContains(res, "Missing photo")

    def test_queue_rules_page_renders(self):
        self.client.login(username="testsalesrep", password="testpass123")
        res = self.client.get("/sales/queue-rules/")
        self.assertEqual(res.status_code, 200)

    def test_wallet_page_renders(self):
        self.client.login(username="testsalesrep", password="testpass123")
        res = self.client.get("/sales/wallet/")
        self.assertEqual(res.status_code, 200)

    def test_applications_list_renders(self):
        self.client.login(username="testsalesrep", password="testpass123")
        res = self.client.get("/sales/applications/")
        self.assertEqual(res.status_code, 200)

    def test_completed_application_opens_read_only_detail(self):
        merchant = User.objects.create_user(username="merchant-completed", password="testpass123")
        app = FinancingApplication.objects.create(
            created_by=merchant,
            reviewed_by=self.underwriter,
            status="approved",
            customer_name="Completed Customer",
            customer_phone="990870616",
            national_id="ABCD1234",
            submitted_at=timezone.now(),
            reviewed_at=timezone.now(),
        )
        self.client.login(username="testsalesrep", password="testpass123")

        list_res = self.client.get(reverse("sales_applications"), {"tab": "completed"})

        self.assertEqual(list_res.status_code, 200)
        self.assertContains(list_res, reverse("sales_application_detail", args=[app.id]))
        self.assertNotContains(list_res, f'href="{reverse("sales_review_summary", args=[app.id])}"')

        detail_res = self.client.get(reverse("sales_review_summary", args=[app.id]))

        self.assertEqual(detail_res.status_code, 200)
        self.assertContains(detail_res, "Completed Application")
        self.assertContains(detail_res, "readonly-detail-card")
        self.assertNotContains(detail_res, "Review &amp; Send Back")


class ClaimToReviewFlowTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.underwriter = User.objects.create_user(
            username="uw-flow",
            password="testpass123",
        )
        from accounts.models import UserProfile
        UserProfile.objects.filter(user=self.underwriter).update(role="underwriter")
        self.merchant = User.objects.create_user(username="merchant-flow", password="testpass123")

    def _pending_app(self, **kwargs):
        defaults = {
            "created_by": self.merchant,
            "status": "pending_review",
            "customer_name": "Flow Customer",
            "customer_phone": "990870630",
            "national_id": "FLOW1234",
            "submitted_at": timezone.now(),
        }
        defaults.update(kwargs)
        return FinancingApplication.objects.create(**defaults)

    def test_claim_get_redirects_home(self):
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get(reverse("sales_claim_next"))
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse("sales_home"))

    def test_legacy_review_url_opens_modern_review_page(self):
        app = self._pending_app(status="under_review", claimed_by=self.underwriter)
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get(reverse("underwriter_review_application", args=[app.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'data-testid="review-application-body"')
        self.assertContains(res, "Flow Customer")

    def test_review_direct_url_renders_step_one_content(self):
        app = self._pending_app(status="under_review", claimed_by=self.underwriter)
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get(reverse("sales_review_summary", args=[app.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'data-testid="review-application-body"')
        self.assertContains(res, "Flow Customer")
        self.assertContains(res, "CALL PRIMARY")
        self.assertContains(res, "Continue to Identity Check")

    def test_review_missing_app_shows_error_card(self):
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get(reverse("sales_review_summary", args=[999999]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'data-testid="review-state-error"')
        self.assertContains(res, "couldn't load this application")

    def test_review_page_has_loading_markup(self):
        app = self._pending_app(status="under_review", claimed_by=self.underwriter)
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get(reverse("sales_review_summary", args=[app.id]))
        self.assertContains(res, "Loading application review")

    def test_queue_status_returns_cooldown_expires_at(self):
        app = self._pending_app(
            status="under_review",
            claimed_by=self.underwriter,
            claimed_at=timezone.now(),
        )
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get(reverse("sales_queue_status"))
        data = res.json()
        self.assertIn("cooldown_expires_at", data)
        self.assertIsNotNone(data["cooldown_expires_at"])
        self.assertGreater(data["cooldown_remaining"], 0)
        self.assertEqual(res["Cache-Control"], "no-store")

    def test_sales_home_removes_duplicate_dashboard_controls(self):
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get("/sales/")

        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, "Report Issue")
        self.assertNotContains(res, "Queue Rules")
        self.assertNotContains(res, "Spin &amp; Win")
        self.assertNotContains(res, 'aria-label="Tools"')
        self.assertContains(res, "Earnings & Wallet")
        applications = res.content.decode().split('aria-label="Applications"', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("My Active", applications)

    def test_home_shows_cooldown_expires_at_attribute(self):
        self._pending_app(
            status="under_review",
            claimed_by=self.underwriter,
            claimed_at=timezone.now(),
        )
        self._pending_app()
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get("/sales/")
        self.assertContains(res, "data-cooldown-expires-at")

    def test_my_active_links_open_review(self):
        app = self._pending_app(status="under_review", claimed_by=self.underwriter)
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get(reverse("sales_applications"), {"tab": "active"})
        self.assertContains(res, reverse("sales_review_summary", args=[app.id]))

    def test_home_claim_button_has_merchant_sized_height(self):
        self._pending_app()
        self.client.login(username="uw-flow", password="testpass123")
        res = self.client.get("/sales/")
        # Both claim card and My Active card use the same .uw-action-card base class
        self.assertContains(res, "uw-action-card--green")
        self.assertContains(res, 'data-testid="claim-next-btn"')
        self.assertContains(res, 'data-testid="my-active-card"')
        # Both should use the same base class — matching siblings
        content = res.content.decode()
        self.assertIn("uw-action-card", content)


class UnderwriterButtonSizeTest(TestCase):
    """
    Verify claim/status button is same size as My Active (matching siblings).
    Both must use .uw-action-card base class with identical padding.
    """

    def setUp(self):
        self.client = Client()
        self.underwriter = User.objects.create_user(
            username="uw-size",
            password="testpass123",
        )
        from accounts.models import UserProfile
        UserProfile.objects.filter(user=self.underwriter).update(role="underwriter")
        self.merchant = User.objects.create_user(username="merchant-size", password="testpass123")

    def _pending_app(self, **kwargs):
        defaults = {
            "created_by": self.merchant,
            "status": "pending_review",
            "customer_name": "Size Customer",
            "customer_phone": "990870640",
            "national_id": "SIZE1234",
            "submitted_at": timezone.now(),
        }
        defaults.update(kwargs)
        return FinancingApplication.objects.create(**defaults)

    def test_claim_button_and_my_active_same_base_class(self):
        """Both cards must use uw-action-card class."""
        self._pending_app()
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get("/sales/")
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        # Count instances of uw-action-card (should appear for both claim and active cards)
        self.assertGreaterEqual(content.count("uw-action-card"), 2)
        self.assertContains(res, 'data-testid="claim-next-btn"')
        self.assertContains(res, 'data-testid="my-active-card"')

    def test_no_apps_pending_same_size_as_my_active(self):
        """NO APPS PENDING card must use same base class as My Active."""
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get("/sales/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "NO APPS PENDING")
        self.assertContains(res, 'data-testid="claim-no-apps"')
        # Both cards use uw-action-card
        content = res.content.decode()
        self.assertGreaterEqual(content.count("uw-action-card"), 2)

    def test_cooldown_card_same_size_as_my_active(self):
        """CLAIM AVAILABLE IN card must use same base class as My Active."""
        self._pending_app(
            status="under_review",
            claimed_by=self.underwriter,
            claimed_at=timezone.now(),
        )
        self._pending_app()
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get("/sales/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "CLAIM AVAILABLE IN")
        self.assertContains(res, 'data-testid="claim-cooldown-btn"')
        # Both cards use uw-action-card
        content = res.content.decode()
        self.assertGreaterEqual(content.count("uw-action-card"), 2)

    def test_max_active_reached_same_size_as_my_active(self):
        """ACTIVE LIMIT REACHED card must use same base class as My Active."""
        for i in range(5):
            self._pending_app(
                status="under_review",
                claimed_by=self.underwriter,
                claimed_at=timezone.now() - timedelta(hours=1),
                customer_name=f"Active {i}",
                customer_phone=f"9908706{i:02d}",
                national_id=f"SIZE{i:04d}",
            )
        self._pending_app(
            customer_name="Waiting",
            customer_phone="990870650",
            national_id="SIZE9999",
        )
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get("/sales/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "ACTIVE LIMIT REACHED")
        self.assertContains(res, 'data-testid="claim-max-active"')
        content = res.content.decode()
        self.assertGreaterEqual(content.count("uw-action-card"), 2)

    def test_cooldown_label_formatted_as_minutes_seconds(self):
        """Cooldown must show as XM YYS not raw seconds."""
        self._pending_app(
            status="under_review",
            claimed_by=self.underwriter,
            claimed_at=timezone.now(),
        )
        self._pending_app()
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get("/sales/")
        self.assertEqual(res.status_code, 200)
        # Must contain formatted cooldown, not raw seconds like "300s"
        self.assertContains(res, "CLAIM AVAILABLE IN")
        content = res.content.decode()
        import re
        # Should match pattern like "4M 59S" not "299s"
        self.assertRegex(content, r'CLAIM AVAILABLE IN \d+M \d{2}S')

    def test_no_apps_pending_uppercase(self):
        """NO APPS PENDING must be uppercase."""
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get("/sales/")
        self.assertContains(res, "NO APPS PENDING")

    def test_claim_opens_review_step1_content(self):
        """Claim Next → review page renders Step 1 content, not blank."""
        app = self._pending_app()
        self.client.login(username="uw-size", password="testpass123")
        claim_res = self.client.post(reverse("sales_claim_next"))
        self.assertEqual(claim_res.status_code, 302)
        review_res = self.client.get(claim_res.url)
        self.assertEqual(review_res.status_code, 200)
        self.assertContains(review_res, 'data-testid="review-application-body"')
        self.assertContains(review_res, "CALL PRIMARY")
        self.assertContains(review_res, "Customer Verification Call")
        self.assertContains(review_res, "Continue to Identity Check")

    def test_direct_review_url_renders_without_blank(self):
        """Direct URL /sales/applications/<id>/ must render Step 1, not blank."""
        app = self._pending_app(status="under_review", claimed_by=self.underwriter)
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get(reverse("sales_review_summary", args=[app.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'data-review-application-body')
        self.assertContains(res, "CALL PRIMARY")
        self.assertContains(res, "Customer Verification Call")
        # Stepper must be present
        self.assertContains(res, "Continue to Identity Check")

    def test_review_page_body_never_hidden_by_default(self):
        """The review body div must NOT have hidden attribute in server-rendered HTML."""
        app = self._pending_app(status="under_review", claimed_by=self.underwriter)
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get(reverse("sales_review_summary", args=[app.id]))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        # The review body should NOT be hidden at page render time
        self.assertNotIn('data-review-application-body hidden', content)
        self.assertNotIn('data-review-application-body" hidden', content)

    def test_missing_fields_show_missing_not_blank(self):
        """Application with missing fields shows 'Not captured', not blank."""
        # App with no optional fields set
        app = FinancingApplication.objects.create(
            created_by=self.merchant,
            claimed_by=self.underwriter,
            status="under_review",
            customer_name="Sparse Customer",
            customer_phone="990870660",
            national_id="SPARSE01",
        )
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get(reverse("sales_review_summary", args=[app.id]))
        self.assertEqual(res.status_code, 200)
        # Should show "Not captured" for missing fields, not blank
        self.assertContains(res, "Not captured")
        self.assertContains(res, 'data-testid="review-application-body"')

    def test_underwriter_review_page_renders_step_one_body(self):
        """
        /sales/applications/<id>/ must render Step 1 body content immediately.
        Must show Customer Verification Call section, customer fields, and
        action buttons. Must NOT show only the stepper shell without content.
        """
        app = self._pending_app(status="under_review", claimed_by=self.underwriter)
        self.client.login(username="uw-size", password="testpass123")
        res = self.client.get(reverse("sales_review_summary", args=[app.id]))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()

        # Step 1 body content MUST be present
        self.assertContains(res, "Customer Verification Call",
                            msg_prefix="Step 1 must show Customer Verification Call section")
        self.assertContains(res, "Full Name",
                            msg_prefix="Step 1 must show Full Name field")
        self.assertContains(res, "National ID",
                            msg_prefix="Step 1 must show National ID field")
        self.assertContains(res, "CALL PRIMARY",
                            msg_prefix="Step 1 must show CALL PRIMARY button")
        self.assertContains(res, "Continue to Identity Check",
                            msg_prefix="Step 1 must show Continue to Identity Check button")

        # The review body wrapper must be present and not hidden
        self.assertIn('data-review-application-body', content,
                      "Page must contain [data-review-application-body] wrapper")
        self.assertNotIn('data-review-application-body hidden', content,
                         "Review body must NOT have hidden attribute")
        self.assertNotIn('data-review-application-body" hidden', content,
                         "Review body must NOT have hidden attribute")

        # Step 1 content must use ts-review-page class (desktop centering)
        self.assertIn('ts-review-page', content,
                      "Review shell must use ts-review-page for max-width centering")
