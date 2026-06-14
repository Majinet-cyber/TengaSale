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
        self.assertContains(res, "uw-claim-btn--available")
        self.assertContains(res, "CLAIM NEXT")

    def test_sales_home_no_green_claim_button_when_queue_empty(self):
        self.client.login(username="testsalesrep", password="testpass123")

        res = self.client.get("/sales/")

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "No Applications in Queue")
        self.assertContains(res, "NO APPS PENDING")
        self.assertNotContains(res, 'class="uw-claim-btn uw-claim-btn--available')

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
        self.assertContains(res, "NEXT CLAIM IN")

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
        self.assertContains(res, "MAX ACTIVE REACHED")
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
        self.assertContains(res, "kyc-smart-card__img--contain")

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
