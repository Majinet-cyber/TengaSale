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
