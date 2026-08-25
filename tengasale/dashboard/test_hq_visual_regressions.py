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
        cls.collections = (base / "templates/dashboard/hq_payment_collections.html").read_text(encoding="utf-8")
        cls.sidebar = (base / "templates/dashboard/partials/hq_sidebar.html").read_text(encoding="utf-8")
        cls.theme = (base / "static/css/hq-sales-pulse.css").read_text(encoding="utf-8")
        cls.home_theme = (base / "static/css/hq-command-home.css").read_text(encoding="utf-8")
        cls.home_script = (base / "static/js/hq-command-home.js").read_text(encoding="utf-8")

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

    def test_hq_shell_is_full_viewport_and_live_graphs_have_honest_empty_states(self):
        self.assertIn("width:100%;max-width:none!important;min-height:100vh", self.theme)
        self.assertIn("border-radius:0!important", self.theme)
        self.assertNotIn("padding:22px;background:radial-gradient", self.theme)
        for marker in (
            'data-testid="hq-live-intelligence"',
            'data-testid="hq-collections-trend"',
            'data-testid="hq-applications-trend"',
            'data-testid="hq-portfolio-status"',
            "No paid collections in the last 7 days",
            "No submitted applications in the last 14 days",
            "No payment contracts yet",
        ):
            self.assertIn(marker, self.dashboard)

        for marker in (
            "payment_collections_trend",
            "payment_provider_split",
            "payment_status_breakdown",
            "payCollectionsChart",
            "No paid collections in the last 7 days",
        ):
            self.assertIn(marker, self.collections)

    def test_command_center_uses_one_normal_document_scroll_container(self):
        home_theme = (Path(settings.BASE_DIR) / "static/css/hq-command-home.css").read_text(encoding="utf-8")
        self.assertIn("overflow-y: auto", self.dashboard)
        self.assertIn("overflow: visible !important", self.dashboard)
        self.assertIn("height:auto!important;max-height:none!important", home_theme)
        self.assertIn("height:auto!important;max-height:none!important;overflow:visible!important", home_theme)
        self.assertIn("height:auto;margin:0;overflow:visible", self.theme)
        self.assertNotIn("body.role-hq .app-shell:has(.hq-topbar){height:100vh!important}", self.theme)

    def test_executive_kpis_use_weighted_grid_and_semantic_states(self):
        for marker in (
            "grid-template-columns:repeat(12,minmax(0,1fr))",
            ".hq-health-card--collections",
            ".hq-health-card--contracts",
            ".hq-health-card--risk.has-alert",
            ".hq-health-card--locks.has-alert",
            "order:3",
        ):
            self.assertIn(marker, self.theme)
        self.assertIn("Total portfolio", self.dashboard)
        self.assertIn("portfolio_collection_rate", self.dashboard)
        self.assertIn("Payments reconciled", self.dashboard)
        self.assertIn("mwk_short", self.dashboard)

    def test_command_home_has_required_mobile_first_hierarchy(self):
        for marker in (
            'data-testid="hq-command-home"',
            'data-testid="hq-primary-action-center"',
            'data-testid="hq-live-today-payments"',
            "{{ tenga_motto }}",
            "Portfolio today",
            "Portfolio Overview",
            "Payments Performance",
            "Portfolio Status",
            'id="hqPaymentPerformanceChart"',
            'id="hq-operations-title"',
        ):
            self.assertIn(marker, self.dashboard)
        self.assertLess(
            self.dashboard.index('data-testid="hq-primary-action-center"'),
            self.dashboard.index('data-testid="hq-live-today-payments"'),
        )

    def test_legacy_home_modules_are_not_rendered_below_command_home(self):
        self.assertIn(".hq-command-v2,.hq-command-v2~*{display:none!important}", self.home_theme)
        self.assertIn("grid-template-columns:1fr 1fr", self.home_theme)

    def test_primary_graphs_have_sales_pulse_scale_and_responsive_layout(self):
        for marker in (
            'class="hq-graph-grid"',
            "hq-home-performance hq-graph-card",
            "hq-home-status hq-graph-card",
        ):
            self.assertIn(marker, self.dashboard)
        for marker in (
            "grid-template-columns:minmax(0,1.15fr) minmax(0,.85fr)",
            "min-height:340px",
            "height:235px;min-height:235px",
            "@media(max-width:780px){.hq-graph-grid{grid-template-columns:1fr}}",
            "height:210px;min-height:210px",
        ):
            self.assertIn(marker, self.home_theme)

    def test_payment_chart_preserves_live_data_with_readable_chartjs_configuration(self):
        for marker in (
            "data.current",
            "data.previous",
            "data.trend",
            "maintainAspectRatio: false",
            "animation: { duration: 350 }",
            "barPercentage: .55",
            "categoryPercentage: .72",
            'color: "rgba(116,129,154,.12)"',
        ):
            self.assertIn(marker, self.home_script)
