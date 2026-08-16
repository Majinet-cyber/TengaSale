from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class LandingPageRegressionGuardTests(SimpleTestCase):
    """Source-level guards for the public landing page's approved structure."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base = Path(settings.BASE_DIR)
        cls.template = (cls.base / "templates/website/landing.html").read_text(encoding="utf-8")
        cls.script = (cls.base / "static/js/tenga-landing-v4.js").read_text(encoding="utf-8")
        cls.styles = (cls.base / "static/css/tenga-landing-v4.css").read_text(encoding="utf-8")

    def test_landing_has_exactly_one_approved_map_and_no_retired_map(self):
        self.assertEqual(self.template.count('class="map-section'), 1)
        self.assertEqual(self.template.count('class="approved-market-map"'), 1)
        self.assertEqual(self.template.count("africa-map-approved-v4.png"), 1)
        self.assertNotIn("africa-expansion-map", self.template)
        self.assertNotIn('class="map-canvas"', self.template)
        self.assertNotIn('class="map-sidebar"', self.template)

    def test_approved_map_is_constrained_by_viewport_height_without_scaling_mobile(self):
        self.assertIn("calc((100svh - 190px) * 703 / 559)", self.styles)
        self.assertIn("(min-width:701px) and (max-height:900px)", self.styles)
        self.assertIn("(min-width:701px) and (max-height:760px)", self.styles)
        self.assertIn(".approved-market-map{aspect-ratio:4/5}", self.styles)
        self.assertNotIn(".approved-market-map{transform:scale", self.styles)

    def test_redmi_and_samsung_have_dedicated_non_overlapping_visuals(self):
        self.assertIn('brand.includes("redmi") || brand.includes("xiaomi")', self.script)
        self.assertIn('/static/images/phone-redmi-premium-v1.png', self.script)
        self.assertIn('brand.includes("samsung")', self.script)
        self.assertIn('/static/images/phone-samsung.png', self.script)
        self.assertNotEqual(
            "/static/images/phone-redmi-premium-v1.png",
            "/static/images/phone-samsung.png",
        )
        self.assertIn('data-phone-brand="${phone.brand}"', self.script)
        self.assertIn('data-phone-visual="${visualForPhone(phone).src}"', self.script)

    def test_phone_cards_have_no_external_time_badges(self):
        for source in (self.template, self.script, self.styles):
            self.assertNotIn("phone-art-time", source)

    def test_public_brand_navigation_and_hero_why_are_locked_in(self):
        self.assertGreaterEqual(self.template.count('<span class="brand-word">Tenga</span>'), 2)
        self.assertIn('class="nav-links"', self.template)
        self.assertIn("website_careers", self.template)
        self.assertIn("{% url 'login' %}", self.template)
        self.assertIn('section class="hero section-dark"', self.template)
        self.assertIn("A smartphone is <span>not a luxury.</span>", self.template)
        self.assertIn("Work, payments, school and daily life now depend on a smartphone.", self.template)
        self.assertIn("upfront cost stands in the way", self.template)

    def test_payment_preview_uses_a_real_phone_asset(self):
        self.assertIn('class="lock-device-photo"', self.template)
        self.assertIn("images/phone-realistic.png", self.template)
        self.assertIn('id="lockUi"', self.template)
        self.assertIn('id="lockButton"', self.template)

    def test_payment_preview_has_on_device_locked_and_unlocked_states(self):
        self.assertIn('data-testid="payment-simulation-phone"', self.template)
        self.assertIn('data-testid="on-device-lock-reminder"', self.template)
        self.assertIn('data-testid="unlocked-phone-content"', self.template)
        self.assertIn('data-state="locked"', self.template)
        self.assertIn('lockUi.dataset.state = "unlocked"', self.script)
        self.assertIn('unlockConfirmation.setAttribute("aria-hidden", "false")', self.script)
        self.assertIn(".lock-screen.paid", self.styles)

    def test_landing_footer_restores_complete_professional_sitemap(self):
        footer = self.template.split("{% block site_footer %}", 1)[1]
        self.assertIn("footer-layout--complete", footer)
        for test_id in ("footer-product", "footer-support", "footer-company", "footer-region", "footer-legal"):
            self.assertIn(f'data-testid="{test_id}"', footer)
        for route in ("website_terms", "website_privacy", "website_payment_terms", "website_merchant_terms"):
            self.assertIn(route, footer)
        for status in ("Malawi — Live", "Zambia — Next", "Zimbabwe — Planned", "Kenya — Explore"):
            self.assertIn(status, footer)

    def test_landing_has_no_public_apply_link_or_removed_clutter(self):
        self.assertNotIn(">Apply<", self.template)
        self.assertIn("Trade and upgrade intentionally removed", self.template)
        self.assertNotIn('href="#trade-upgrade"', self.template)
        self.assertNotIn('class="calculator-section', self.template)
        self.assertEqual(self.template.count('class="map-section'), 1)

    def test_access_section_keeps_the_approved_headline_and_refined_copy(self):
        self.assertEqual(self.template.count('id="why"'), 1)
        self.assertIn("data-insight-section", self.template)
        self.assertIn("The world went digital.<br />Access did not.", self.template)
        self.assertIn("Smartphones power modern life.", self.template)
        self.assertNotIn("The problem is simple:", self.template)

    def test_bottom_application_area_is_compact_and_not_a_duplicate_form(self):
        self.assertEqual(self.template.count('id="apply"'), 1)
        self.assertIn("apply-section--compact", self.template)
        self.assertNotIn('id="applyForm"', self.template)
        self.assertNotIn('id="deviceSelect"', self.template)
        self.assertEqual(self.template.count('id="protection"'), 1)
