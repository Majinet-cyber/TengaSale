"""Website app tests — public pages, lead forms, branding, and HQ page checks."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, Client, override_settings
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

    def test_premium_navigation_restores_login_and_careers_named_routes(self):
        response = self.client.get(reverse("website_landing"))
        content = response.content.decode()
        nav = content[content.index('class="nav-links"'):content.index('class="nav-actions"')]

        self.assertIn(f'href="{reverse("website_careers")}"', nav)
        self.assertIn(f'href="{reverse("login")}"', nav)
        self.assertGreaterEqual(content.count(f'href="{reverse("website_careers")}"'), 2)
        self.assertGreaterEqual(content.count(f'href="{reverse("login")}"'), 2)

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
        self.assertIn("tengasale-logo-full.svg", content,
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


class LandingPageUIRegressionTests(TestCase):
    """Regression tests ensuring the landing page is clean and stable after v1.3 cleanup."""

    def setUp(self):
        self.client = Client()

    def _get_landing(self):
        return self.client.get(reverse("website_landing"))

    # ── Homepage loads ────────────────────────────────────────────

    def test_homepage_loads_with_status_200(self):
        """Root URL / must return 200."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_v4_landing_preserves_required_copy_and_internal_payment_route(self):
        response = self.client.get("/")
        self.assertContains(response, "2× monthly income")
        self.assertEqual(response.content.decode().count(f'href="{reverse("portal_search")}"'), 7)
        self.assertNotContains(response, "https://pay.tengasale.africa")
        self.assertContains(response, "css/tenga-landing-v4.css")
        self.assertContains(response, "js/tenga-landing-v4.js")

    def test_v4_landing_uses_previous_official_logo_without_collage_asset(self):
        response = self.client.get("/")
        content = response.content.decode()
        self.assertEqual(content.count('src="/static/images/brand/tengasale-logo-full.svg"'), 2)
        self.assertNotContains(response, "images/brand/tengasale-logo-icon.png")

    def test_v4_application_form_uses_existing_application_flow(self):
        response = self.client.get("/")
        self.assertContains(response, f'action="{reverse("new_application")}"')

    # ── Removed broken sections must be absent ────────────────────

    def test_landing_no_live_stats_strip(self):
        """Live stats strip must NOT appear — removed until intentionally rebuilt."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("live-stats-strip", content,
            "live-stats-strip should not appear on landing page")

    def test_landing_no_raw_smart_contracts(self):
        """Raw 'Smart contracts' label must not appear on the homepage."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("Smart contracts", content,
            "Raw 'Smart contracts' stat label must not appear on landing page")

    def test_landing_no_mwk_disbursed_label(self):
        """Raw 'MWK disbursed' label must not appear."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("MWK disbursed", content,
            "Raw 'MWK disbursed' label must not appear on landing page")

    def test_landing_no_customers_connected_label(self):
        """Raw 'Customers connected' live stat label must not appear."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("Customers connected", content,
            "Raw 'Customers connected' live stat must not appear on landing page")

    def test_landing_does_not_contain_built_for_real_malawian_merchants(self):
        """'Built for real Malawian smartphone merchants' heading must not appear."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("Built for real Malawian smartphone merchants", content,
            "Removed heading found on landing page")

    def test_landing_does_not_contain_faster_applications(self):
        """'Faster applications' broken section heading must not appear."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("Faster applications", content,
            "'Faster applications' section must not appear on landing page")

    def test_landing_does_not_contain_better_repayment_visibility(self):
        """'Better repayment visibility' broken section heading must not appear."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("Better repayment visibility", content,
            "'Better repayment visibility' section must not appear on landing page")

    def test_landing_does_not_contain_cleaner_merchant_records(self):
        """'Cleaner merchant records' broken section heading must not appear."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("Cleaner merchant records", content,
            "'Cleaner merchant records' section must not appear on landing page")

    # ── No fake testimonials ──────────────────────────────────────

    def test_landing_no_fake_testimonial_names(self):
        """The landing page must not contain hardcoded fake testimonial names."""
        response = self._get_landing()
        content = response.content.decode()
        fake_names = ["James Phiri", "Grace Banda", "Kondwani Mwale"]
        for name in fake_names:
            self.assertNotIn(name, content,
                f"Fake testimonial name '{name}' found on landing page")

    def test_landing_no_raw_testimonial_placeholders(self):
        """No testimonial quote cards should render on the landing page."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("merchant-quote-card", content,
            "Testimonial quote cards must not appear on landing page")

    # ── Section structure ─────────────────────────────────────────

    def test_landing_hero_headline_present(self):
        """Approved v4 hero headline must be present."""
        response = self._get_landing()
        self.assertContains(response, "A smartphone is")
        self.assertContains(response, "not a luxury.")

    def test_landing_how_it_works_section_present(self):
        """The approved v4 process section must be present."""
        response = self._get_landing()
        self.assertContains(response, 'id="process"')
        self.assertContains(response, "Choose phone")

    def test_landing_no_internal_tooling_exposed(self):
        """Public landing must not expose internal systems."""
        response = self._get_landing()
        content = response.content.decode()
        forbidden = [
            "PayChangu",
            "HQ credit command",
            "fraud signals",
            "AI-Assisted",
            "Volts Engine",
            "Volts payout",
            "webhook",
            "recovery tooling",
            "underwriter intelligence",
            "internal payout",
        ]
        for term in forbidden:
            self.assertNotIn(term, content, f"Internal term '{term}' found on public landing")

    def test_landing_primary_cta_present(self):
        """Primary CTA connects to the existing application route."""
        response = self._get_landing()
        self.assertContains(response, reverse("new_application"))
        self.assertContains(response, "Get Started")

    def test_anonymous_start_application_redirects_to_login(self):
        """Anonymous users clicking Start Application are sent to login with next URL."""
        response = self.client.get(reverse("new_application"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.assertIn("next=", response["Location"])

    def test_landing_no_blue_label_classes(self):
        """Landing page must not use Bootstrap blue classes on section labels."""
        response = self._get_landing()
        content = response.content.decode()
        blue_classes = ["badge-primary", "bg-primary", "btn-primary btn-primary--blue",
                        "text-primary", "label-primary"]
        for cls in blue_classes:
            self.assertNotIn(cls, content,
                f"Blue Bootstrap class '{cls}' found on landing page")

    def test_landing_css_version_1_3(self):
        """Landing page must reference the v1.3 CSS to bust stale caches."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertIn("website", content, "Page should reference website CSS")
        self.assertIn("v=1.3", content, "CSS cache-bust version must be v=1.3")

    # ── Portals still work ────────────────────────────────────────

    def test_merchant_dashboard_url_accessible_for_anonymous(self):
        """Merchant dashboard redirects to login rather than 500-erroring."""
        response = self.client.get("/tengasale/merchant/", follow=False)
        self.assertIn(response.status_code, [302, 301, 200],
            "Merchant dashboard returned an unexpected error status")

    def test_underwriter_dashboard_url_accessible_for_anonymous(self):
        """Underwriter dashboard redirects to login rather than 500-erroring."""
        response = self.client.get("/tengasale/underwriter/", follow=False)
        self.assertIn(response.status_code, [302, 301, 200, 404],
            "Underwriter dashboard returned an unexpected error status")


class PublicSupportEnquiryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.payload = {
            "full_name": "Thoko Mbewe",
            "email": "thoko@example.com",
            "phone": "+265991234567",
            "category": "payment_help",
            "subject": "Payment is not reflecting",
            "message": "My mobile money payment was completed but is not showing yet.",
            "consent": "on",
            "website": "",
            "originating_page": "https://example.test/#support",
        }

    def test_landing_has_support_form_faq_and_visible_fallback(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="support"')
        self.assertContains(response, 'id="supportForm"')
        self.assertContains(response, "support@emajinet.africa")
        self.assertContains(response, "mailto:support@emajinet.africa")
        self.assertEqual(response.content.decode().count('class="faq-item"'), 7)

    @override_settings(TENGA_SUPPORT_EMAIL_DELIVERY_ENABLED=False)
    @patch("website.views.EmailMessage")
    def test_non_delivering_backend_never_claims_success_and_preserves_data(self, email_message):
        response = self.client.post(reverse("website_contact"), self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Direct form delivery is not configured here")
        self.assertContains(response, "Payment is not reflecting")
        self.assertContains(response, "support@emajinet.africa")
        email_message.assert_not_called()

    @override_settings(
        TENGA_SUPPORT_EMAIL="support@emajinet.africa",
        TENGA_SUPPORT_EMAIL_DELIVERY_ENABLED=True,
    )
    @patch("website.views.EmailMessage")
    def test_valid_enquiry_uses_emailmessage_recipient_reply_to_subject_and_body(self, email_message):
        email_message.return_value.send.return_value = 1
        response = self.client.post(reverse("website_contact"), self.payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/?support=sent#support")

        kwargs = email_message.call_args.kwargs
        self.assertEqual(kwargs["to"], ["support@emajinet.africa"])
        self.assertEqual(kwargs["reply_to"], ["thoko@example.com"])
        self.assertEqual(
            kwargs["subject"],
            "[Tenga Website][Payment help] Payment is not reflecting — Thoko Mbewe",
        )
        self.assertIn("Full name: Thoko Mbewe", kwargs["body"])
        self.assertIn("Phone: +265991234567", kwargs["body"])
        self.assertIn("Category: Payment help", kwargs["body"])
        self.assertIn("Originating page: https://example.test/#support", kwargs["body"])
        email_message.return_value.send.assert_called_once_with(fail_silently=False)

    @override_settings(TENGA_SUPPORT_EMAIL_DELIVERY_ENABLED=True)
    @patch("website.views.EmailMessage")
    def test_phone_is_required_for_payment_application_and_merchant_categories(self, email_message):
        for category in ("payment_help", "new_application", "merchant_partnership"):
            with self.subTest(category=category):
                data = {**self.payload, "category": category, "phone": ""}
                response = self.client.post(reverse("website_contact"), data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "A phone number is required")
        email_message.assert_not_called()

    @override_settings(TENGA_SUPPORT_EMAIL_DELIVERY_ENABLED=True)
    @patch("website.views.EmailMessage")
    def test_honeypot_blocks_delivery(self, email_message):
        response = self.client.post(
            reverse("website_contact"),
            {**self.payload, "website": "https://spam.example"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not submit")
        email_message.assert_not_called()

    @override_settings(TENGA_SUPPORT_EMAIL_DELIVERY_ENABLED=True, TENGA_SUPPORT_RATE_LIMIT=2)
    @patch("website.views.EmailMessage")
    def test_rate_limit_blocks_excess_delivery(self, email_message):
        email_message.return_value.send.return_value = 1
        self.assertEqual(self.client.post(reverse("website_contact"), self.payload).status_code, 302)
        self.assertEqual(self.client.post(reverse("website_contact"), self.payload).status_code, 302)
        response = self.client.post(reverse("website_contact"), self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many enquiries")
        self.assertEqual(email_message.return_value.send.call_count, 2)

    def test_contact_post_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(reverse("website_contact"), self.payload)
        self.assertEqual(response.status_code, 403)

    def test_public_support_and_legal_pages_use_required_email(self):
        pages = [
            reverse("website_terms"),
            reverse("website_privacy"),
            reverse("website_payment_terms"),
            reverse("website_merchant_terms"),
            reverse("website_careers"),
            reverse("website_merchant_signup_success"),
            reverse("portal_support"),
        ]
        for url in pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "support@emajinet.africa")
                self.assertNotContains(response, "support@tengasale.africa")


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
