import json
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class CanonicalBrandIdentityTests(TestCase):
    def test_public_landing_uses_authenticated_canonical_t(self):
        response = self.client.get(reverse("public_home"))
        self.assertContains(response, "images/Tenga.png")
        self.assertContains(response, "Why wait.", count=2)
        self.assertNotContains(response, "Endless Possibilities")
        self.assertNotContains(response, 'brand-logo--wordmark" src="/static/images/brand/tengasale-logo-full.svg')

    def test_manifest_and_icon_derivatives_exist(self):
        static_root = Path(settings.BASE_DIR) / "static"
        manifest = json.loads((static_root / "manifest.webmanifest").read_text(encoding="utf-8"))
        sources = {item["src"].split("?")[0] for item in manifest["icons"]}
        self.assertIn("/static/images/icon-192.png", sources)
        self.assertIn("/static/images/icon-512.png", sources)
        for relative in ("favicon.ico", "images/icon-16.png", "images/icon-32.png", "images/apple-touch-icon.png", "images/icon-192.png", "images/icon-512.png", "images/Tenga.png"):
            self.assertTrue((static_root / relative).is_file(), relative)
