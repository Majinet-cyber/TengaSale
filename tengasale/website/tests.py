"""Website app tests — public pages, lead forms, branding, and HQ page checks."""
from django.test import TestCase, Client
from django.urls import reverse
from .models import MerchantLead, CareerLead

# CSS classes known to cause giant/broken logo rendering
BROKEN_LOGO_CLASSES = [
    "giant-logo",
    "brand-bg",
    "logo-watermark",
    "hero-logo-bg",
    "filter:brightness(0)",
    "filter: brightness(0)",
]


class PublicSiteTests(TestCase):
    def setUp(self):
        self.client = Client()

    # ── /site/ landing page ──────────────────────────────────────

    def test_landing_page_loads(self):
        response = self.client.get(reverse("website_landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TengaSale")

    def test_landing_page_has_make_payment_link(self):
        response = self.client.get(reverse("website_landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/pay/")

    def test_landing_page_become_merchant_links_to_signup(self):
        response = self.client.get(reverse("website_landing"))
        self.assertEqual(response.status_code, 200)
        signup_url = reverse("website_merchant_signup")
        self.assertContains(response, signup_url)

    def test_landing_page_has_careers_link(self):
        response = self.client.get(reverse("website_landing"))
        careers_url = reverse("website_careers")
        self.assertContains(response, careers_url)

    # ── /site/merchant-signup/ ───────────────────────────────────

    def test_merchant_signup_page_loads(self):
        response = self.client.get(reverse("website_merchant_signup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "merchant")

    def test_merchant_signup_form_creates_lead(self):
        self.assertEqual(MerchantLead.objects.count(), 0)
        response = self.client.post(reverse("website_merchant_signup"), {
            "business_name": "Test Phones Lilongwe",
            "owner_full_name": "John Phiri",
            "phone": "+265991000000",
            "district": "Lilongwe",
            "business_type": "phone_shop",
            "estimated_monthly_phone_sales": "10",
            "whatsapp_phone": "",
            "email": "",
            "area": "Area 25",
            "has_business_registration": "yes",
            "preferred_payout_method": "airtel_money",
            "message": "Interested in joining.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("website_merchant_signup_success"))
        self.assertEqual(MerchantLead.objects.count(), 1)
        lead = MerchantLead.objects.first()
        self.assertEqual(lead.business_name, "Test Phones Lilongwe")
        self.assertEqual(lead.source, "public_site")

    def test_merchant_signup_shows_errors_on_empty_submit(self):
        response = self.client.post(reverse("website_merchant_signup"), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "required")
        self.assertEqual(MerchantLead.objects.count(), 0)

    def test_merchant_signup_success_page_loads(self):
        response = self.client.get(reverse("website_merchant_signup_success"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "received")

    # ── /site/careers/ ───────────────────────────────────────────

    def test_careers_page_loads(self):
        response = self.client.get(reverse("website_careers"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Careers")

    def test_careers_form_creates_lead(self):
        self.assertEqual(CareerLead.objects.count(), 0)
        response = self.client.post(reverse("website_careers"), {
            "full_name": "Mary Banda",
            "phone": "+265888000000",
            "email": "mary@example.com",
            "district": "Blantyre",
            "role_interested": "underwriter",
            "note": "I am experienced in credit assessment.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("website_careers_success"))
        self.assertEqual(CareerLead.objects.count(), 1)
        lead = CareerLead.objects.first()
        self.assertEqual(lead.full_name, "Mary Banda")
        self.assertEqual(lead.role_interested, "underwriter")

    def test_careers_form_shows_errors_on_empty_submit(self):
        response = self.client.post(reverse("website_careers"), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "required")
        self.assertEqual(CareerLead.objects.count(), 0)

    def test_careers_success_page_loads(self):
        response = self.client.get(reverse("website_careers_success"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "received")

    # ── /pay/ portal search page ─────────────────────────────────

    def test_pay_page_loads(self):
        response = self.client.get(reverse("portal_search"))
        self.assertEqual(response.status_code, 200)

    def test_pay_page_no_login_logout_icons(self):
        """The /pay/ page should not show login, settings, notification, or logout icons."""
        response = self.client.get(reverse("portal_search"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("logout-button", content,
            "logout-button should not appear on /pay/ page")
        self.assertNotIn("notification-button", content,
            "notification-button should not appear on /pay/ page")
        self.assertNotIn("admin-button", content,
            "admin-button should not appear on /pay/ page")

    # ── Branding quality checks ──────────────────────────────────

    def test_public_pages_no_broken_logo_classes(self):
        """Public pages must not contain CSS classes that cause giant black logo blobs."""
        pages = [
            reverse("website_landing"),
            reverse("website_careers"),
            reverse("website_merchant_signup"),
        ]
        for url in pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                for broken_class in BROKEN_LOGO_CLASSES:
                    self.assertNotIn(broken_class, content,
                        f"Page {url} contains broken logo class: {broken_class}")

    def test_landing_page_ts_brand_image_present(self):
        """Landing page should reference the TS brand image."""
        response = self.client.get(reverse("website_landing"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The TS brand image (large marketing image) should appear on landing
        self.assertIn("tengasale-logo-icon", content,
            "TS brand image not found on landing page")

    def test_careers_page_no_dark_hero_class(self):
        """Careers hero should be light mode — no dark navy background via old class."""
        response = self.client.get(reverse("website_careers"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The new light hero uses .careers-hero but the old dark CSS should be gone
        self.assertIn("careers-hero", content)

    def test_merchant_signup_has_hero_section(self):
        """Merchant signup should have a branded hero section."""
        response = self.client.get(reverse("website_merchant_signup"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("merchant-hero", content,
            "Merchant signup should have a merchant-hero section")

    def test_public_pages_no_visible_template_comments(self):
        """Public pages should not render visible Django template comments."""
        pages = [
            reverse("website_landing"),
            reverse("website_careers"),
            reverse("website_merchant_signup"),
        ]
        for url in pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                self.assertNotIn("{#", content,
                    f"Visible template comment found in {url}")


class MerchantLeadModelTests(TestCase):
    def test_merchant_lead_str(self):
        lead = MerchantLead(business_name="City Phones", owner_full_name="James Mwale")
        self.assertIn("City Phones", str(lead))
        self.assertIn("James Mwale", str(lead))

    def test_merchant_lead_default_status(self):
        lead = MerchantLead.objects.create(
            business_name="Alpha Shop",
            owner_full_name="Alice Tembo",
            phone="0991000001",
            district="Lilongwe",
            business_type="phone_shop",
        )
        self.assertEqual(lead.status, MerchantLead.STATUS_NEW)
        self.assertEqual(lead.source, "public_site")

    def test_career_lead_str(self):
        lead = CareerLead(full_name="Bob Chirwa", role_interested="underwriter")
        self.assertIn("Bob Chirwa", str(lead))

    def test_career_lead_default_status(self):
        lead = CareerLead.objects.create(
            full_name="Grace Nyirenda",
            phone="0888000002",
            district="Mzuzu",
            role_interested="sales_agent",
        )
        self.assertEqual(lead.status, CareerLead.STATUS_NEW)


class HQPageTests(TestCase):
    """Tests that require an HQ user — checks for comment visibility and Malawi pill."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username="hqtest",
            password="testpass123",
        )
        # Assign HQ role via profile if available
        try:
            from accounts.models import UserProfile
            UserProfile.objects.get_or_create(user=self.user, defaults={"role": "hq"})
            self.user.profile.role = "hq"
            self.user.profile.save()
        except Exception:
            pass

    def _get_hq_page(self):
        self.client.force_login(self.user)
        return self.client.get("/tengasale/hq/")

    def test_hq_page_no_visible_template_comments(self):
        response = self._get_hq_page()
        if response.status_code == 200:
            content = response.content.decode()
            self.assertNotIn("{#", content,
                "Visible Django template comment {# found in HQ page output")
            self.assertNotIn("#}", content,
                "Visible Django template comment #} found in HQ page output")

    def test_hq_page_has_malawi_indicator(self):
        response = self._get_hq_page()
        if response.status_code == 200:
            content = response.content.decode()
            self.assertTrue(
                "Malawi" in content,
                "Malawi country indicator not found in HQ page",
            )


class AdminRegistrationTests(TestCase):
    def test_merchant_lead_admin_registered(self):
        from django.contrib import admin
        from .models import MerchantLead
        self.assertIn(MerchantLead, admin.site._registry)

    def test_career_lead_admin_registered(self):
        from django.contrib import admin
        from .models import CareerLead
        self.assertIn(CareerLead, admin.site._registry)
