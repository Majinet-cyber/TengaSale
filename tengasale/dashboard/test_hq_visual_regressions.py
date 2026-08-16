from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse


class HQSalesPulseVisualRegressionTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = Path(settings.BASE_DIR)
        cls.base_template = (base / "templates/base/base.html").read_text(encoding="utf-8")
        cls.dashboard = (base / "templates/dashboard/hq.html").read_text(encoding="utf-8")
        cls.sidebar = (base / "templates/dashboard/partials/hq_sidebar.html").read_text(encoding="utf-8")
        cls.theme = (base / "static/css/hq-sales-pulse.css").read_text(encoding="utf-8")

    def test_hq_loads_one_role_scoped_sales_pulse_theme(self):
        self.assertIn('current_tengasale_role == "hq"', self.base_template)
        self.assertIn("hq-sales-pulse.css", self.base_template)
        for token in ("--pulse-navy", "--pulse-gradient", "--pulse-cyan", "--pulse-orange", "--pulse-surface"):
            self.assertIn(token, self.theme)
        self.assertIn("body.role-hq", self.theme)

    def test_command_center_and_shared_shell_keep_operational_navigation(self):
        for label in ("Overview", "Applications", "Contracts", "Active Contracts", "Completed Contracts", "Underwriters", "Deals Catalog", "Devices", "Devices / Locking", "Repossession &amp; Resale", "Portfolio Management"):
            self.assertIn(label, self.dashboard)
        for label in ("Applications", "Deals Catalog", "Portfolio", "Devices", "Underwriters", "Payments", "Reconciliation", "Users & Settings"):
            self.assertIn(label, self.sidebar)

    def test_core_hq_routes_still_reverse(self):
        for route in ("hq_dashboard", "hq_applications", "hq_underwriter_queue", "hq_deals", "hq_devices", "hq_portfolio", "hq_payment_collections", "hq_commissions"):
            self.assertTrue(reverse(route).startswith("/"))

    def test_dense_tables_forms_and_command_hero_receive_shared_presentation(self):
        for selector in ("body.role-hq table", "input,select,textarea", ".hq-command-action", ".hq-command-hero,.hq-pulse"):
            self.assertIn(selector, self.theme)
