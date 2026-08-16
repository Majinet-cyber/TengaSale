from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ApplicationMobileLayoutRegressionTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = Path(settings.BASE_DIR)
        cls.styles = (base / "static/css/tenga-design-system.css").read_text(encoding="utf-8")
        cls.customer = (base / "templates/applications/customer_details.html").read_text(encoding="utf-8")
        cls.underwriter = (base / "templates/dashboard/underwriter_review_summary.html").read_text(encoding="utf-8")
        cls.sales_review = (base / "templates/sales/review_summary.html").read_text(encoding="utf-8")

    def test_merchant_application_shell_uses_border_box_and_zero_minimums(self):
        self.assertIn(".application-flow-page *::before", self.styles)
        self.assertIn("box-sizing:border-box", self.styles)
        self.assertIn("grid-template-columns:minmax(0,1fr)", self.styles)
        self.assertIn("overscroll-behavior-inline:contain", self.styles)
        self.assertIn("width:100% !important", self.styles)
        self.assertIn("tenga-page-header__eyebrow", self.customer)

    def test_underwriter_review_shell_shrinks_and_wraps_identifiers(self):
        self.assertIn("body.role-underwriter .uw-canvas *::before", self.styles)
        self.assertIn("body.role-underwriter .uw-header", self.styles)
        self.assertIn("overflow-wrap:anywhere", self.styles)
        self.assertIn('class="uw-canvas"', self.underwriter)
        self.assertIn("body.role-underwriter .ts-review-page", self.styles)
        self.assertIn('class="ts-review-page"', self.sales_review)
