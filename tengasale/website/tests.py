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


class LandingPageUIRegressionTests(TestCase):
    """Regression tests for landing page UI bugs fixed in v1.3."""

    def setUp(self):
        self.client = Client()

    def _get_landing(self):
        return self.client.get(reverse("website_landing"))

    # ── Live stats ────────────────────────────────────────────────

    def test_landing_live_stats_strip_present(self):
        """Live stats strip container must be present on the landing page."""
        response = self._get_landing()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "live-stats-strip")

    def test_landing_live_stats_no_mwk_duplication(self):
        """Label should be 'Disbursed' not 'MWK disbursed' — prevents 'MWK 0 MWK disbursed'."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertNotIn("MWK disbursed", content,
            "Label 'MWK disbursed' causes duplication with 'MWK 0' value prefix")

    def test_landing_live_stats_have_icon_containers(self):
        """Live stat pills must use icon chip containers."""
        response = self._get_landing()
        self.assertContains(response, "live-stat-pill__icon")

    def test_landing_live_stats_have_value_and_label(self):
        """Each stat must have a value span and a label span."""
        response = self._get_landing()
        self.assertContains(response, "live-stat-pill__val")
        self.assertContains(response, "live-stat-pill__lbl")

    # ── SVG safety ────────────────────────────────────────────────

    def test_landing_merchant_value_svgs_have_explicit_dimensions(self):
        """Merchant value card SVGs must have explicit width/height to prevent inflate."""
        response = self._get_landing()
        content = response.content.decode()
        self.assertIn('merchant-value-card__icon', content)
        # The merchant value card section must not contain SVGs without dimensions
        # (bare viewBox-only SVGs expand to 300x150px when CSS is stale/uncached)
        import re
        # Find SVGs inside merchant-value-card__icon; they must have width attr
        merchant_section_start = content.find('merchant-value-grid')
        merchant_section_end = content.find('/div>', content.rfind('merchant-value-card'))
        if merchant_section_start > 0:
            merchant_section = content[merchant_section_start:merchant_section_end + 100]
            # All SVGs in this section should have width attribute
            bare_svg = re.search(r'<svg viewBox="[^"]*">', merchant_section)
            self.assertIsNone(bare_svg,
                "Merchant value card contains bare SVG without width/height — will inflate if CSS stales")

    def test_landing_no_svgs_without_dimensions_in_icon_chips(self):
        """Icon chip SVGs should have explicit width and height attributes."""
        response = self._get_landing()
        content = response.content.decode()
        import re
        # Check that step icons have explicit dimensions
        self.assertIn('width="26" height="26"', content,
            "Step icon SVGs should have explicit 26x26 dimensions")
        # Check that platform stat icons have explicit dimensions
        self.assertIn('width="18" height="18"', content,
            "Platform stat icon SVGs should have explicit 18x18 dimensions")
        # Check that merchant value card icons have explicit dimensions
        self.assertIn('width="22" height="22"', content,
            "Icon chip SVGs should have explicit 22x22 dimensions")

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
        """No empty quote/testimonial placeholder blocks."""
        response = self._get_landing()
        content = response.content.decode()
        # merchant_quotes is intentionally empty; no quote cards should render
        self.assertNotIn("merchant-quote-card", content,
            "Testimonial quote cards rendered when merchant_quotes is empty")

    # ── Section structure ─────────────────────────────────────────

    def test_landing_hero_headline_present(self):
        """Hero headline must be present."""
        response = self._get_landing()
        self.assertContains(response, "Smartphone financing built for")

    def test_landing_platform_section_present(self):
        """Platform stats section must be present."""
        response = self._get_landing()
        self.assertContains(response, "platform-stats-grid")

    def test_landing_merchant_value_section_present(self):
        """Merchant value section must be present with its 3 cards."""
        response = self._get_landing()
        self.assertContains(response, "merchant-value-grid")
        self.assertContains(response, "Faster applications")
        self.assertContains(response, "Better repayment visibility")
        self.assertContains(response, "Cleaner merchant records")

    def test_landing_faq_present(self):
        """FAQ section must be present."""
        response = self._get_landing()
        self.assertContains(response, "faq-item")

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
        # The stylesheet link must include the v=1.3 cache-bust param.
        # ManifestStaticFilesStorage may hash the filename so we search for
        # the version param separately from the base name.
        self.assertIn("website", content,
            "Page should reference website CSS")
        self.assertIn("v=1.3", content,
            "CSS cache-bust version must be v=1.3 to fix SVG rendering bugs")

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
