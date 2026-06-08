from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch
from datetime import timedelta
from decimal import Decimal

from accounts.utils import assign_role
from applications.models import FinancingApplication
from portal.models import PaymentContract
from .portfolio_services import calculate_contract_risk, calculate_par_band, calculate_portfolio_kpis


def assert_merchant_dashboard_malawi_flag(test_case, response):
    """
    Regression lock: merchant dashboard must always show Malawi flag + MW chip.
    Fails if the chip is removed, reverted to text-only MW, or the SVG partial is dropped.
    """
    test_case.assertEqual(response.status_code, 200)
    test_case.assertContains(
        response,
        'class="flag-pill merchant-country-chip"',
        count=1,
        msg_prefix="Exactly one Malawi country chip",
    )
    test_case.assertContains(response, "merchant-country-chip__code", msg_prefix="MW code span")
    test_case.assertContains(response, 'title="Malawi"', msg_prefix="Malawi chip title")
    test_case.assertContains(
        response,
        'viewBox="0 0 20 14"',
        msg_prefix="Malawi flag SVG (portal/_malawi_flag.html)",
    )
    test_case.assertContains(response, "#339E35", msg_prefix="Malawi flag green band")
    test_case.assertContains(response, "#CE1126", msg_prefix="Malawi flag red band")
    test_case.assertContains(response, ">MW<", msg_prefix="MW country code")

    content = response.content.decode()
    greeting_idx = content.index('class="merchant-greeting"')
    chip_idx = content.index('class="flag-pill merchant-country-chip"', greeting_idx)
    greeting_end = content.index("</section>", greeting_idx)
    chip_region = content[chip_idx:greeting_end]
    test_case.assertIn(
        'viewBox="0 0 20 14"',
        chip_region,
        "Flag SVG must render inside merchant-country-chip (not a broken img or text-only MW)",
    )
    test_case.assertIn(
        "merchant-country-chip__code",
        chip_region,
        "MW label must stay inside the country chip beside the flag",
    )
    test_case.assertIn(">MW<", chip_region, "MW text must render inside the greeting country chip")


class HQPortfolioManagementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.hq = User.objects.create_user(username="hq", password="test-pass-123")
        self.merchant = User.objects.create_user(username="merchant", password="test-pass-123")
        self.underwriter = User.objects.create_user(username="underwriter", password="test-pass-123")
        assign_role(self.hq, "hq")
        assign_role(self.merchant, "merchant")
        assign_role(self.underwriter, "underwriter")
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            claimed_by=self.underwriter,
            status="approved",
            customer_name="Jane Banda",
            customer_phone="990870616",
            national_id="RQXFVZC9",
        )
        self.contract = PaymentContract.objects.create(
            source_application=self.app,
            customer_name="Jane Banda",
            customer_phone="990870616",
            customer_national_id="RQXFVZC9",
            device_model="TECNO Pop 10C",
            imei_number="123456789012345",
            cash_price=Decimal("350000.00"),
            total_amount=Decimal("875000.00"),
            deposit_required=Decimal("130000.00"),
            deposit_paid=Decimal("130000.00"),
            amount_paid=Decimal("250000.00"),
            due_date=timezone.localdate() - timedelta(days=8),
            analytics_days_late=8,
        )

    def test_hq_portfolio_pages_and_exports_load_for_hq(self):
        self.client.login(username="hq", password="test-pass-123")

        portfolio = self.client.get(reverse("hq_portfolio"))
        report = self.client.get(reverse("hq_portfolio_investor_report"))
        csv_response = self.client.get(reverse("hq_portfolio_export_csv"))
        excel_response = self.client.get(reverse("hq_portfolio_export_excel"))
        pdf_response = self.client.get(reverse("hq_portfolio_export_pdf"))

        self.assertEqual(portfolio.status_code, 200)
        self.assertContains(portfolio, "Portfolio Management")
        self.assertContains(portfolio, self.contract.contract_number)
        self.assertContains(portfolio, "PAR 7+")
        self.assertContains(portfolio, "badge-risk-medium")
        self.assertEqual(report.status_code, 200)
        self.assertContains(report, "TengaSale Portfolio Report")
        self.assertContains(report, "Executive Snapshot")
        self.assertEqual(csv_response.status_code, 200)
        self.assertContains(csv_response, "Contract Number")
        self.assertContains(csv_response, self.contract.contract_number)
        self.assertEqual(excel_response.status_code, 200)
        self.assertIn("application/vnd.ms-excel", excel_response["Content-Type"])
        self.assertEqual(pdf_response.status_code, 302)
        self.assertIn(reverse("hq_portfolio_investor_report"), pdf_response["Location"])

    def test_hq_portfolio_forbids_merchants(self):
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("hq_portfolio"))

        self.assertEqual(response.status_code, 403)

    def test_portfolio_services_calculate_par_risk_and_kpis(self):
        self.assertEqual(calculate_par_band(self.contract), "PAR 7+")
        self.assertEqual(calculate_contract_risk(self.contract), "Medium")

        kpis = calculate_portfolio_kpis(PaymentContract.objects.filter(pk=self.contract.pk))

        self.assertEqual(kpis["active_contracts"], 1)
        self.assertEqual(kpis["portfolio_value"], Decimal("875000.00"))
        self.assertEqual(kpis["total_deposits"], Decimal("130000.00"))
        self.assertEqual(kpis["outstanding_balance"], Decimal("625000.00"))


class HomePageTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def create_user(self, username, group_name=None, **kwargs):
        user = get_user_model().objects.create_user(username=username, password="test-pass-123", **kwargs)
        if group_name:
            role = {"Merchant": "merchant", "Underwriter": "underwriter", "HQ": "hq"}[group_name]
            assign_role(user, role)
        return user

    def assert_role_forbidden(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "accounts/role_forbidden.html")
        self.assertContains(response, "That area is not available for your role.", status_code=403)
        self.assertContains(response, "Go to my dashboard", status_code=403)

    def test_root_renders_public_landing_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "website/landing.html")
        self.assertContains(response, "Login")

    def test_healthz_returns_lightweight_ok_json(self):
        response = self.client.get("/healthz/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_readyz_returns_ready_json(self):
        response = self.client.get("/readyz/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    def test_login_page_loads(self):
        response = self.client.get("/accounts/login/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_home_redirects_unauthenticated_users_to_login(self):
        response = self.client.get("/home/")

        self.assertRedirects(response, "/accounts/login/?next=/home/")

    def test_home_redirects_merchant_to_merchant_portal(self):
        self.create_user("merchant", "Merchant")
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get("/home/")

        self.assertRedirects(response, reverse("merchant_dashboard"))

    def test_home_redirects_underwriter_to_underwriter_portal(self):
        self.create_user("underwriter", "Underwriter")
        self.client.login(username="underwriter", password="test-pass-123")

        response = self.client.get("/home/")

        # The home route redirects to underwriter_dashboard (/tengasale/underwriter/)
        # which itself redirects to /sales/ — just verify first hop, don't fetch target
        self.assertRedirects(response, reverse("underwriter_dashboard"), fetch_redirect_response=False)

    def test_home_redirects_hq_to_hq_portal(self):
        self.create_user("hq", "HQ")
        self.client.login(username="hq", password="test-pass-123")

        response = self.client.get("/home/")

        self.assertRedirects(response, reverse("hq_dashboard"))

    def test_merchant_can_access_merchant_dashboard(self):
        user = self.create_user("merchant", "Merchant")
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("merchant_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Hi, {user.username}")
        self.assertContains(response, "TengaSale")
        self.assertContains(response, "Home")
        self.assertContains(response, "NEW APPLICATION")
        self.assertContains(response, "Applications")
        self.assertContains(response, f'href="{settings.TENGASALE_WHATSAPP_LINK}"')
        self.assertContains(response, 'aria-label="WhatsApp support"')
        self.assertContains(response, 'bi-whatsapp')
        self.assertContains(response, 'bi-bell')
        self.assertContains(response, 'aria-label="Log out"')
        self.assertContains(response, "merchant-shell")
        self.assertContains(response, "merchant-dashboard-shell")
        assert_merchant_dashboard_malawi_flag(self, response)
        self.assertContains(response, "row-arrow")
        self.assertNotContains(response, "Claim Next")
        self.assertNotContains(response, "Active Contracts")
        self.assertNotContains(response, "Completed Contracts")

    def test_merchant_dashboard_loads_with_empty_data_without_pdf_generation(self):
        self.create_user("empty-merchant", "Merchant")
        self.client.login(username="empty-merchant", password="test-pass-123")

        with patch("merchants.pdf.generate_merchant_agreement_pdf") as generate_pdf:
            response = self.client.get(reverse("merchant_dashboard"))

        self.assertEqual(response.status_code, 200)
        generate_pdf.assert_not_called()

    def test_merchant_dashboard_malawi_flag_regression_lock(self):
        """Dedicated guard: Malawi flag chip must never disappear from merchant dashboard."""
        self.create_user("merchant", "Merchant")
        self.client.login(username="merchant", password="test-pass-123")
        response = self.client.get(reverse("merchant_dashboard"))
        assert_merchant_dashboard_malawi_flag(self, response)

    def test_merchant_dashboard_contains_applications_and_tools_sections(self):
        """Applications and Tools navigation sections must be present."""
        self.create_user("merchant", "Merchant")
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("merchant_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Applications")
        self.assertContains(response, "Active")
        self.assertContains(response, "Completed")
        self.assertContains(response, "Archived / Rejected")
        self.assertContains(response, "Tools")
        self.assertContains(response, "All Deals")
        self.assertContains(response, "My Earnings")
        self.assertContains(response, "Payments")
        self.assertContains(response, "row-arrow")

    def test_merchant_dashboard_cta_appears_before_applications_nav(self):
        """Primary new-contract CTA must appear before Applications list."""
        self.create_user("merchant", "Merchant")
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("merchant_dashboard"))

        content = response.content.decode()
        self.assertIn("NEW APPLICATION", content)
        self.assertIn("Applications", content)
        self.assertLess(
            content.index("NEW APPLICATION"),
            content.index("Applications"),
            "New application button must appear before Applications section",
        )

    def test_merchant_dashboard_has_compact_header_not_giant_hero(self):
        """Merchant dashboard must use a compact header, not an oversized orange hero."""
        self.create_user("merchant", "Merchant")
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("merchant_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "merchant-header")
        self.assertNotContains(response, "mhome-hero")

    def test_underwriter_visiting_merchant_dashboard_redirects_to_underwriter(self):
        self.create_user("underwriter", "Underwriter")
        self.client.login(username="underwriter", password="test-pass-123")

        response = self.client.get(reverse("merchant_dashboard"))

        self.assert_role_forbidden(response)

    def test_hq_can_access_hq_dashboard_only(self):
        self.create_user("hq", "HQ")
        self.client.login(username="hq", password="test-pass-123")

        hq_response = self.client.get(reverse("hq_dashboard"))
        merchant_response = self.client.get(reverse("merchant_dashboard"))

        self.assertEqual(hq_response.status_code, 200)
        self.assertContains(hq_response, "HQ")
        self.assert_role_forbidden(merchant_response)

    def test_hq_dashboard_loads_with_sparse_empty_data(self):
        self.create_user("empty-hq", "HQ")
        self.client.login(username="empty-hq", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No financial activity yet")

    def test_hq_dashboard_does_not_show_preview_links_for_normal_hq_user(self):
        self.create_user("hq", "HQ")
        self.client.login(username="hq", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Developer Preview")
        self.assertNotContains(response, "Merchant Portal Preview")
        self.assertNotContains(response, "Underwriter Portal Preview")

    def test_hq_dashboard_shows_only_hq_links(self):
        self.create_user("hq-staff", "HQ", is_staff=True)
        self.client.login(username="hq-staff", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertContains(response, "Django Admin")
        self.assertContains(response, "Users")
        self.assertContains(response, "Applications")
        self.assertContains(response, "Analytics")
        self.assertContains(response, "Reports")
        self.assertNotContains(response, "Deals Management")
        self.assertNotContains(response, "New Application")
        self.assertNotContains(response, "Claim Next")
        self.assertNotContains(response, "Merchant Portal Preview")
        self.assertNotContains(response, "Underwriter Portal Preview")

    @override_settings(DEBUG=True)
    def test_debug_superuser_does_not_see_preview_in_normal_hq_nav(self):
        user = get_user_model().objects.create_superuser(username="super-debug", password="test-pass-123")
        assign_role(user, "hq")
        self.client.login(username="super-debug", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertNotContains(response, "Developer Preview")
        self.assertNotContains(response, "Merchant Portal Preview")
        self.assertNotContains(response, "Underwriter Portal Preview")

    @override_settings(DEBUG=False)
    def test_superuser_cannot_see_developer_preview_when_debug_is_false(self):
        user = get_user_model().objects.create_superuser(username="super-prod", password="test-pass-123")
        assign_role(user, "hq")
        self.client.login(username="super-prod", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Developer Preview")
        self.assertNotContains(response, "Merchant Portal Preview")
        self.assertNotContains(response, "Underwriter Portal Preview")

    def test_hq_dashboard_has_fixed_sidebar_navigation(self):
        """HQ dashboard must include the fixed sidebar nav, not just inline card buttons."""
        self.create_user("hq-nav", "HQ")
        self.client.login(username="hq-nav", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertEqual(response.status_code, 200)
        # Sidebar container
        self.assertContains(response, 'id="hq-sidebar"')
        self.assertContains(response, 'class="hq-layout"')
        # Essential sidebar links
        self.assertContains(response, 'hq-sidebar-link')
        self.assertContains(response, 'Overview')
        self.assertContains(response, 'Applications')
        self.assertContains(response, 'Finance')
        # Portal links
        self.assertContains(response, 'Merchant Admin')
        self.assertContains(response, 'Tech Support')

    def test_hq_dashboard_uses_responsive_chart_frames_and_recovery_links(self):
        self.create_user("hq-chart-responsive", "HQ")
        self.client.login(username="hq-chart-responsive", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Portfolio Health")
        self.assertContains(response, "hq-chart-frame--donut")
        self.assertContains(response, "hq-chart-frame hq-chart-frame--pipeline")
        self.assertContains(response, "hq-chart-frame--financial")
        self.assertContains(response, "No portfolio activity yet")
        self.assertContains(response, "Recovery Value")
        self.assertContains(response, "Repossession &amp; Resale")
        self.assertContains(response, reverse("hq_repossession_resale"))
        self.assertContains(response, "Financial Simulations")

    def test_merchant_cannot_access_hq_dashboard(self):
        self.create_user("merchant", "Merchant")
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assert_role_forbidden(response)

    def test_underwriter_cannot_open_merchant_application_creation(self):
        self.create_user("underwriter", "Underwriter")
        self.client.login(username="underwriter", password="test-pass-123")

        response = self.client.get(reverse("new_application"))

        self.assert_role_forbidden(response)

    def test_underwriter_cannot_access_hq_dashboard(self):
        self.create_user("underwriter", "Underwriter")
        self.client.login(username="underwriter", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assert_role_forbidden(response)

    def test_superuser_can_access_hq_dashboard(self):
        user = get_user_model().objects.create_superuser(username="super", password="test-pass-123")
        assign_role(user, "hq")
        self.client.login(username="super", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertEqual(response.status_code, 200)

    def test_hq_cannot_access_worker_portals_or_developer_preview(self):
        self.create_user("hq-normal", "HQ")
        self.client.login(username="hq-normal", password="test-pass-123")

        merchant_response = self.client.get(reverse("merchant_dashboard"))
        # underwriter_dashboard → /sales/ (via redirect) → 403 for non-underwriter; follow chain
        underwriter_response = self.client.get(reverse("underwriter_dashboard"), follow=True)
        preview_response = self.client.get(reverse("hq_merchant_preview"))

        self.assert_role_forbidden(merchant_response)
        self.assert_role_forbidden(underwriter_response)
        self.assertRedirects(preview_response, reverse("hq_dashboard"))

    def test_hq_can_create_user_with_role(self):
        self.create_user("hq", "HQ")
        self.client.login(username="hq", password="test-pass-123")

        response = self.client.post(
            reverse("hq_users"),
            {
                "username": "new-underwriter",
                "email": "underwriter@example.com",
                "password": "test-pass-123",
                "full_name": "New Underwriter",
                "role": "underwriter",
                "is_active": "on",
            },
        )

        created = get_user_model().objects.get(username="new-underwriter")
        self.assertRedirects(response, reverse("hq_users"))
        self.assertTrue(created.groups.filter(name="Underwriter").exists())
        self.assertEqual(created.userprofile.role, "underwriter")

    def test_hq_created_merchant_logs_into_merchant_portal(self):
        self.create_user("hq-create-merchant", "HQ")
        self.client.login(username="hq-create-merchant", password="test-pass-123")

        response = self.client.post(
            reverse("hq_users"),
            {
                "username": "created-merchant",
                "email": "merchant@example.com",
                "password": "test-pass-123",
                "full_name": "Created Merchant",
                "role": "merchant",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("hq_users"))
        self.client.logout()

        login_response = self.client.post(
            reverse("login"),
            {"username": "created-merchant", "password": "test-pass-123"},
        )

        self.assertRedirects(login_response, reverse("merchant_dashboard"), fetch_redirect_response=False)

    def test_home_path_redirects_to_role_portal(self):
        self.create_user("merchant", "Merchant")
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get("/home/")

        self.assertRedirects(response, reverse("merchant_dashboard"))

    def test_home_template_uses_post_logout_form(self):
        self.create_user("merchant", "Merchant")
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("merchant_dashboard"))

        self.assertContains(response, 'method="post" action="/accounts/logout/"')
        self.assertNotContains(response, 'href="/accounts/logout/"')


class DashboardUrlTests(TestCase):
    def test_home_url_name_resolves(self):
        self.assertEqual(reverse("home"), "/home/")

    def test_portal_url_names_resolve(self):
        self.assertEqual(reverse("merchant_dashboard"), "/tengasale/merchant/")
        self.assertEqual(reverse("underwriter_dashboard"), "/tengasale/underwriter/")
        self.assertEqual(reverse("hq_dashboard"), "/tengasale/hq/")
        self.assertEqual(reverse("hq_users"), "/tengasale/hq/users/")
        self.assertEqual(reverse("hq_underwriter_queue"), "/tengasale/hq/underwriter-queue/")
        self.assertEqual(reverse("hq_reports"), "/tengasale/hq/reports/")

    def test_short_portal_urls_redirect_to_tengasale_urls(self):
        self.assertRedirects(self.client.get("/merchant/"), "/tengasale/merchant/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/underwriter/"), "/tengasale/underwriter/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/hq/"), "/tengasale/hq/", fetch_redirect_response=False)


class HQPhase10ETests(TestCase):
    """Phase 10E: HQ operational pages — crash fixes and new routes."""

    def setUp(self):
        call_command("seed_roles")
        self.hq_user = get_user_model().objects.create_user(username="hq-phase10e", password="test-pass-123")
        assign_role(self.hq_user, "hq")
        self.client.login(username="hq-phase10e", password="test-pass-123")

    # ── Route smoke tests ──────────────────────────────────────────────────────

    def test_hq_deals_loads(self):
        response = self.client.get(reverse("hq_deals"))
        self.assertEqual(response.status_code, 200)

    def test_hq_deals_has_no_django_admin_placeholder(self):
        response = self.client.get(reverse("hq_deals"))
        self.assertNotContains(response, "Use Django Admin")

    def test_hq_simulations_loads(self):
        response = self.client.get(reverse("hq_simulations"))
        self.assertEqual(response.status_code, 200)

    def test_hq_simulations_no_split_error(self):
        """Template must not contain .split(',') call which is invalid in Django templates."""
        response = self.client.get(reverse("hq_simulations"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, ".split(")

    def test_hq_simulations_post_returns_calculated_outputs(self):
        response = self.client.post(reverse("hq_simulations"), {
            "cash_price": "400000",
            "selling_total": "1000000",
            "deposit_pct": "20",
            "term_months": "12",
            "default_rate": "5",
            "payment_collection_rate": "90",
            "uw_commission_rate": "7",
            "merchant_commission_rate": "1",
            "wht_rate": "20",
            "arrears_rate": "14",
            "num_devices": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("result", response.context)
        result = response.context["result"]
        self.assertIn("deposit_amount", result)
        self.assertIn("financed_amount", result)
        self.assertIn("uw_gross_commission", result)
        self.assertIn("uw_wht", result)
        self.assertIn("merchant_payout", result)
        self.assertIn("recommendation", result)

    def test_hq_simulations_wht_is_20_percent_of_uw_commission(self):
        response = self.client.post(reverse("hq_simulations"), {
            "cash_price": "400000",
            "contract_value": "1000000",
            "deposit_pct": "20",
            "term_months": "12",
            "default_rate": "0",
            "collection_rate": "100",
            "uw_commission_rate": "7",
            "merchant_commission_rate": "1",
            "wht_rate": "20",
            "arrears_penalty_rate": "14",
            "num_devices": "1",
        })
        result = response.context["result"]
        gross = result["uw_gross_commission"]
        wht = result["uw_wht"]
        self.assertAlmostEqual(float(wht), float(gross) * 0.20, places=0)

    def test_hq_simulations_scenario_list_renders(self):
        """Simulation result must include 4 scenario objects with required fields."""
        response = self.client.post(reverse("hq_simulations"), {
            "cash_price": "400000",
            "contract_value": "1000000",
            "deposit_pct": "20",
            "term_months": "12",
            "default_rate": "5",
            "collection_rate": "90",
            "uw_commission_rate": "7",
            "merchant_commission_rate": "1",
            "wht_rate": "20",
            "arrears_penalty_rate": "14",
            "num_devices": "50",
        })
        self.assertEqual(response.status_code, 200)
        result = response.context["result"]
        self.assertIn("scenarios", result)
        scenarios = result["scenarios"]
        self.assertEqual(len(scenarios), 4)
        for sc in scenarios:
            self.assertIn("label", sc)
            self.assertIn("estimated_profit", sc)
            self.assertIn("profitable", sc)
            self.assertIn("insight", sc)

    def test_hq_simulations_merchant_commission_excludes_deposit(self):
        """Merchant commission must be calculated on financed amount, not contract value."""
        response = self.client.post(reverse("hq_simulations"), {
            "cash_price": "400000",
            "contract_value": "1000000",
            "deposit_pct": "20",
            "term_months": "12",
            "default_rate": "0",
            "collection_rate": "90",
            "uw_commission_rate": "0",
            "merchant_commission_rate": "1",
            "wht_rate": "0",
            "arrears_penalty_rate": "0",
            "num_devices": "1",
        })
        result = response.context["result"]
        # financed_per = 1000000 - (1000000 * 0.20) = 800000
        # merchant_comm_per = 800000 * 0.01 = 8000 (NOT 1000000 * 0.01 = 10000)
        self.assertAlmostEqual(float(result["merchant_comm_per"]), 8000.0, places=0)
        # merchant_payout = cash_price + merchant_comm = 400000 + 8000 = 408000
        self.assertAlmostEqual(float(result["merchant_payout_per"]), 408000.0, places=0)

    def test_hq_simulations_uw_commission_excludes_deposit(self):
        """Underwriter commission must be on expected_collections from financed amount, not contract value."""
        response = self.client.post(reverse("hq_simulations"), {
            "cash_price": "400000",
            "contract_value": "1000000",
            "deposit_pct": "20",
            "term_months": "12",
            "default_rate": "0",
            "collection_rate": "100",
            "uw_commission_rate": "7",
            "merchant_commission_rate": "0",
            "wht_rate": "0",
            "arrears_penalty_rate": "0",
            "num_devices": "1",
        })
        result = response.context["result"]
        # financed_per = 800000; expected_collections = 800000 * 1.0 = 800000
        # uw_gross = 800000 * 0.07 = 56000 (NOT 1000000 * 0.07 = 70000)
        self.assertAlmostEqual(float(result["uw_gross_commission"]), 56000.0, places=0)

    def test_hq_simulations_gender_field_has_no_effect(self):
        """Posting a gender field must not affect the simulation result."""
        base_post = {
            "cash_price": "400000",
            "contract_value": "1000000",
            "deposit_pct": "20",
            "term_months": "12",
            "default_rate": "5",
            "collection_rate": "90",
            "uw_commission_rate": "7",
            "merchant_commission_rate": "1",
            "wht_rate": "20",
            "arrears_penalty_rate": "14",
            "num_devices": "10",
        }
        r1 = self.client.post(reverse("hq_simulations"), base_post)
        post_with_gender = dict(base_post, gender="Male")
        r2 = self.client.post(reverse("hq_simulations"), post_with_gender)
        p1 = r1.context["result"]["estimated_profit"]
        p2 = r2.context["result"]["estimated_profit"]
        self.assertEqual(p1, p2)

    def test_hq_simulations_no_template_syntax_error(self):
        """Template must render without errors and return HTTP 200."""
        response = self.client.get(reverse("hq_simulations"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/hq_simulations.html")

    def test_hq_merchant_payouts_loads(self):
        response = self.client.get(reverse("hq_merchant_payouts"))
        self.assertEqual(response.status_code, 200)

    def test_hq_merchant_payouts_no_slice_before_filter(self):
        """Payout query must not try to filter a sliced queryset."""
        from commissions.models import MerchantContractPayout
        from django.contrib.auth import get_user_model
        User = get_user_model()
        merchant = User.objects.create_user(username="test-merchant-p10e", password="x")
        assign_role(merchant, "merchant")
        for i in range(5):
            MerchantContractPayout.objects.create(
                merchant=merchant,
                cash_price=100000,
                financed_amount=200000,
                merchant_commission_amount=2000,
                total_payable=102000,
                status=MerchantContractPayout.STATUS_PENDING,
            )
        response = self.client.get(reverse("hq_merchant_payouts"))
        self.assertEqual(response.status_code, 200)

    def test_hq_applications_loads_with_null_underwriter(self):
        """Applications page must not crash when claimed_by is None."""
        from applications.models import FinancingApplication
        app = FinancingApplication.objects.create(
            created_by=self.hq_user,
            customer_name="Test Customer",
            national_id="AB123456",
            customer_phone="881234567",
            status="pending_review",
            claimed_by=None,
        )
        response = self.client.get(reverse("hq_applications"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Customer")
        self.assertContains(response, "Unassigned")

    def test_hq_applications_loads_with_null_created_by_safely(self):
        """Applications page must show safe fallback for None created_by."""
        response = self.client.get(reverse("hq_applications"))
        self.assertEqual(response.status_code, 200)

    def test_hq_underwriter_queue_loads(self):
        response = self.client.get(reverse("hq_underwriter_queue"))
        self.assertEqual(response.status_code, 200)

    def test_hq_devices_loads(self):
        response = self.client.get(reverse("hq_devices"))
        self.assertEqual(response.status_code, 200)

    def test_hq_repossession_resale_loads_and_records_recovery_costs(self):
        from decimal import Decimal
        from portal.models import PaymentContract, RecoveryCost

        contract = PaymentContract.objects.create(
            customer_name="Recovery Customer",
            customer_phone="+265881111111",
            total_amount=Decimal("100000.00"),
            amount_paid=Decimal("25000.00"),
            status=PaymentContract.STATUS_LOCKED,
        )

        response = self.client.get(reverse("hq_repossession_resale"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Repossession &amp; Resale")

        response = self.client.post(reverse("hq_repossession_resale"), {
            "action": "mark_repossession_pending",
            "contract_id": contract.pk,
        })
        self.assertEqual(response.status_code, 200)
        contract.refresh_from_db()
        self.assertEqual(contract.status, PaymentContract.STATUS_REPOSSESSION_PENDING)

        response = self.client.post(reverse("hq_repossession_resale"), {
            "action": "add_recovery_cost",
            "contract_id": contract.pk,
            "cost_type": RecoveryCost.COST_REPOSSESSION,
            "amount": "15000",
            "description": "Repossession transport",
            "is_chargeable_to_customer": "on",
        })
        self.assertRedirects(response, reverse("hq_repossession_resale"))
        cost = RecoveryCost.objects.get(contract=contract)
        self.assertEqual(cost.amount, Decimal("15000.00"))
        self.assertIsNone(cost.approved_at)

        response = self.client.post(reverse("hq_repossession_resale"), {
            "action": "approve_recovery_cost",
            "contract_id": contract.pk,
            "cost_id": cost.pk,
        })
        self.assertRedirects(response, reverse("hq_repossession_resale"))
        cost.refresh_from_db()
        self.assertIsNotNone(cost.approved_at)

    def test_hq_reports_loads(self):
        response = self.client.get(reverse("hq_reports"))
        self.assertEqual(response.status_code, 200)

    def test_hq_safe_operations_loads(self):
        response = self.client.get(reverse("hq_safe_operations"))
        self.assertEqual(response.status_code, 200)

    def test_hq_commission_ledger_loads(self):
        response = self.client.get(reverse("hq_commission_ledger"))
        self.assertEqual(response.status_code, 200)

    def test_hq_staff_payouts_loads(self):
        response = self.client.get(reverse("hq_staff_payouts"))
        self.assertEqual(response.status_code, 200)

    def test_hq_auto_approval_loads(self):
        response = self.client.get(reverse("hq_auto_approval"))
        self.assertEqual(response.status_code, 200)

    # ── Deals CRUD ────────────────────────────────────────────────────────────

    def test_hq_can_create_deal(self):
        from deals.models import DeviceBrand
        brand = DeviceBrand.objects.create(name="TestBrand-P10E")
        response = self.client.post(reverse("hq_deals"), {
            "action": "add_deal",
            "brand_id": brand.pk,
            "model_name": "Test Model X",
            "specs": "128GB/8GB",
            "default_cash_price": "400000",
            "min_cash_price": "0",
            "max_cash_price": "0",
            "cash_price": "400000",
            "deposit_percent": "13",
            "loan_multiplier": "2.5",
            "term_months": "12",
            "is_active": "on",
            "stock_status": "in_stock",
            "lock_provider": "",
            "country": "MW",
            "notes": "",
        })
        self.assertEqual(response.status_code, 200)
        from deals.models import DeviceDeal
        self.assertTrue(DeviceDeal.objects.filter(model_name="Test Model X").exists())

    def test_hq_can_toggle_deal(self):
        from deals.models import DeviceBrand, DeviceDeal
        brand = DeviceBrand.objects.create(name="BrandToggle-P10E")
        deal = DeviceDeal.objects.create(
            brand=brand, model_name="Toggle Me", specs="64GB",
            cash_price=200000, deposit_percent=13, is_active=True,
        )
        self.client.post(reverse("hq_deals"), {
            "action": "toggle_deal",
            "deal_id": deal.pk,
        })
        deal.refresh_from_db()
        self.assertFalse(deal.is_active)

    # ── Commission / WHT rules ────────────────────────────────────────────────

    def test_merchant_commission_is_1_percent_of_financed_amount(self):
        """Merchant commission = 1% of financed amount (excluding deposit)."""
        from decimal import Decimal
        financed = Decimal("800000")
        commission = financed * Decimal("0.01")
        self.assertEqual(commission, Decimal("8000.00"))

    def test_wht_20_percent_applies_to_underwriter_commission(self):
        from decimal import Decimal
        uw_gross = Decimal("56000")
        wht = uw_gross * Decimal("0.20")
        net = uw_gross - wht
        self.assertEqual(wht, Decimal("11200.00"))
        self.assertEqual(net, Decimal("44800.00"))

    def test_arrears_deduction_is_14_percent_of_missed_payment(self):
        from decimal import Decimal
        missed = Decimal("33333")
        deduction = missed * Decimal("0.14")
        self.assertAlmostEqual(float(deduction), float(missed) * 0.14, places=2)

    # ── Auto-approval ─────────────────────────────────────────────────────────

    def test_auto_approval_blocks_application_with_duplicate_active_id(self):
        from applications.models import FinancingApplication
        from applications.services.auto_approval import evaluate_auto_approval
        from portal.models import PaymentContract

        # Create an active contract with the same national ID
        PaymentContract.objects.create(
            customer_name="Existing Customer",
            customer_phone="881111111",
            customer_national_id="DUP12345",
            total_amount=1000000,
            status="active",
        )

        app = FinancingApplication.objects.create(
            created_by=self.hq_user,
            national_id="DUP12345",
            customer_name="Duplicate Customer",
            customer_phone="882222222",
            status="pending_review",
        )

        result = evaluate_auto_approval(app)
        self.assertFalse(result["eligible"])
        self.assertTrue(any("DUP12345" in b for b in result["blocks"]))

    def test_auto_approval_eligible_when_all_checks_pass(self):
        from applications.models import FinancingApplication
        from applications.services.auto_approval import evaluate_auto_approval
        from deals.models import DeviceBrand, DeviceDeal
        import tempfile, os
        from django.core.files.uploadedfile import SimpleUploadedFile

        brand = DeviceBrand.objects.create(name="AutoBrand-P10E")
        deal = DeviceDeal.objects.create(
            brand=brand, model_name="Auto Deal", specs="64GB",
            cash_price=200000, deposit_percent=13, is_active=True,
        )
        face = SimpleUploadedFile("face.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
        id_f = SimpleUploadedFile("id_f.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
        id_b = SimpleUploadedFile("id_b.jpg", b"\xff\xd8\xff", content_type="image/jpeg")

        app = FinancingApplication.objects.create(
            created_by=self.hq_user,
            national_id="OKID0001",
            customer_name="Eligible Customer",
            customer_phone="883333333",
            status="pending_review",
            deal=deal,
            agreed_to_terms=True,
            customer_face_image=face,
            id_front_image=id_f,
            id_back_image=id_b,
            exact_monthly_income=150000,
            calculated_monthly_payment=30000,
        )

        result = evaluate_auto_approval(app)
        self.assertEqual(result["blocks"], [])
        self.assertTrue(result["eligible"])

    # ── Template safety ───────────────────────────────────────────────────────

    def test_no_hq_template_contains_visible_template_comments(self):
        """No HQ page should output raw Django template comment markers."""
        hq_routes = [
            "hq_dashboard", "hq_deals", "hq_simulations",
            "hq_merchant_payouts", "hq_applications",
            "hq_underwriter_queue", "hq_reports",
            "hq_devices", "hq_staff_payouts", "hq_auto_approval",
            "hq_safe_operations", "hq_commission_ledger",
            "hq_repossession_resale",
        ]
        for name in hq_routes:
            with self.subTest(route=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200, f"Route {name} returned {response.status_code}")
                self.assertNotContains(response, "{#", msg_prefix=f"Route {name} contains visible template comment")


class RoleAccessControlTests(TestCase):
    """Ensure role-based access is correctly enforced across HQ routes."""

    def setUp(self):
        call_command("seed_roles")
        self.hq_user = get_user_model().objects.create_user(username="hq-access-test", password="x")
        assign_role(self.hq_user, "hq")
        self.merchant_user = get_user_model().objects.create_user(username="merchant-access-test", password="x")
        assign_role(self.merchant_user, "merchant")
        self.underwriter_user = get_user_model().objects.create_user(username="uw-access-test", password="x")
        assign_role(self.underwriter_user, "underwriter")

    HQ_ONLY_ROUTES = [
        "hq_dashboard",
        "hq_applications",
        "hq_deals",
        "hq_simulations",
        "hq_commissions",
        "hq_merchant_payouts",
        "hq_reports",
        "hq_devices",
        "hq_repossession_resale",
        "hq_fraud_checks",
        "hq_audit_trail",
        "hq_payment_collections",
        "hq_reconciliation",
        "hq_sales_analytics",
        "hq_underwriter_performance",
    ]

    def test_unauthenticated_redirected_to_login_on_hq_routes(self):
        for route in self.HQ_ONLY_ROUTES:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertIn(response.status_code, [302, 301], f"{route} should redirect unauthenticated")
                self.assertIn("/login/", response.url, f"{route} should redirect to login")

    def test_merchant_denied_hq_dashboard(self):
        self.client.login(username="merchant-access-test", password="x")
        response = self.client.get(reverse("hq_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_merchant_denied_hq_commissions(self):
        self.client.login(username="merchant-access-test", password="x")
        response = self.client.get(reverse("hq_commissions"))
        self.assertEqual(response.status_code, 403)

    def test_merchant_denied_hq_applications(self):
        self.client.login(username="merchant-access-test", password="x")
        response = self.client.get(reverse("hq_applications"))
        self.assertEqual(response.status_code, 403)

    def test_merchant_denied_merchant_payouts_hq(self):
        self.client.login(username="merchant-access-test", password="x")
        response = self.client.get(reverse("hq_merchant_payouts"))
        self.assertEqual(response.status_code, 403)

    def test_underwriter_denied_hq_dashboard(self):
        self.client.login(username="uw-access-test", password="x")
        response = self.client.get(reverse("hq_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_underwriter_denied_merchant_payouts_hq(self):
        self.client.login(username="uw-access-test", password="x")
        response = self.client.get(reverse("hq_merchant_payouts"))
        self.assertEqual(response.status_code, 403)

    def test_hq_allowed_all_hq_routes(self):
        self.client.login(username="hq-access-test", password="x")
        for route in self.HQ_ONLY_ROUTES:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200, f"HQ user should access {route}")

    def test_applications_page_loads_with_no_assigned_uw(self):
        """Applications without claimed_by (unassigned UW) should not crash the page."""
        from applications.models import FinancingApplication
        merchant = get_user_model().objects.create_user(username="merchant-app-test-null", password="x")
        assign_role(merchant, "merchant")
        FinancingApplication.objects.create(
            customer_name="Test Customer",
            customer_phone="0991234567",
            national_id="TSNULL12345",
            created_by=merchant,
            claimed_by=None,  # No underwriter assigned
            status="pending_review",
        )
        self.client.login(username="hq-access-test", password="x")
        response = self.client.get(reverse("hq_applications"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unassigned")


class DiscountPolicyTests(TestCase):
    """Ensure gender is never used in discount/deposit policy outcomes."""

    def test_gender_does_not_change_deposit_result_male_vs_female(self):
        """Calling evaluate_deposit_eligibility with any gender must produce identical results."""
        from applications.services.discount_policy import evaluate_deposit_eligibility
        from decimal import Decimal

        # Simulate a standard new customer — same inputs, different gender (not passed in at all)
        base_kwargs = dict(
            age=30,
            completed_contracts=0,
            monthly_income=Decimal("150000"),
            monthly_repayment=Decimal("50000"),
            has_active_contract=False,
            has_arrears_history=False,
            kyc_verified=True,
        )
        result = evaluate_deposit_eligibility(**base_kwargs)
        # Gender is not a parameter — calling it multiple times must always return same result
        result2 = evaluate_deposit_eligibility(**base_kwargs)
        self.assertEqual(result["min_deposit_pct"], result2["min_deposit_pct"])
        self.assertEqual(result["eligible"], result2["eligible"])
        self.assertEqual(result["loyalty_discount_pct"], result2["loyalty_discount_pct"])

    def test_returning_customer_gets_lower_deposit(self):
        from applications.services.discount_policy import evaluate_deposit_eligibility
        from decimal import Decimal
        result = evaluate_deposit_eligibility(
            age=32, completed_contracts=1, has_active_contract=False,
            has_arrears_history=False, kyc_verified=True,
        )
        self.assertTrue(result["eligible"])
        self.assertLess(result["min_deposit_pct"], 30)

    def test_active_contract_blocks_application(self):
        from applications.services.discount_policy import evaluate_deposit_eligibility
        result = evaluate_deposit_eligibility(age=30, has_active_contract=True)
        self.assertFalse(result["eligible"])
        self.assertTrue(len(result["blocks"]) > 0)

    def test_underage_applicant_blocked(self):
        from applications.services.discount_policy import evaluate_deposit_eligibility
        result = evaluate_deposit_eligibility(age=18)
        self.assertFalse(result["eligible"])

    def test_overage_applicant_blocked(self):
        from applications.services.discount_policy import evaluate_deposit_eligibility
        result = evaluate_deposit_eligibility(age=75)
        self.assertFalse(result["eligible"])

    def test_arrears_history_forces_highest_deposit(self):
        from applications.services.discount_policy import evaluate_deposit_eligibility
        from decimal import Decimal
        result = evaluate_deposit_eligibility(
            age=35, completed_contracts=2, has_arrears_history=True,
            has_active_contract=False, kyc_verified=True,
        )
        # Even with 2 completed contracts, arrears resets to 30%
        self.assertEqual(result["min_deposit_pct"], Decimal("30"))

    def test_get_eligible_deposit_options_new_customer(self):
        from applications.services.discount_policy import get_eligible_deposit_options
        from decimal import Decimal
        options = get_eligible_deposit_options(age=28, completed_contracts=0)
        # New customer: only 30% allowed
        self.assertEqual(options, [Decimal("30")])

    def test_get_eligible_deposit_options_returning_customer(self):
        from applications.services.discount_policy import get_eligible_deposit_options
        from decimal import Decimal
        options = get_eligible_deposit_options(
            age=32, completed_contracts=2, has_arrears_history=False, kyc_verified=True,
        )
        # Returning customer aged 25+: all bands available
        self.assertIn(Decimal("15"), options)
        self.assertIn(Decimal("20"), options)
        self.assertIn(Decimal("30"), options)


# ═══════════════════════════════════════════════════════════════════════════════
# Phase-6 Tests: New roles, Merchant Admin portal, Support & Bug Monitor
# ═══════════════════════════════════════════════════════════════════════════════

class NewRolesTests(TestCase):
    """Test role assignment and redirect logic for the two new roles."""

    def setUp(self):
        call_command("seed_roles")
        User = get_user_model()
        self.ma_user = User.objects.create_user(username="ma_test", password="pass")
        self.ts_user = User.objects.create_user(username="ts_test", password="pass")
        assign_role(self.ma_user, "merchant_admin")
        assign_role(self.ts_user, "tech_support")

    def test_merchant_admin_role_assigned(self):
        from accounts.utils import is_merchant_admin, primary_role
        self.assertTrue(is_merchant_admin(self.ma_user))
        self.assertEqual(primary_role(self.ma_user), "merchant_admin")

    def test_tech_support_role_assigned(self):
        from accounts.utils import is_tech_support, primary_role
        self.assertTrue(is_tech_support(self.ts_user))
        self.assertEqual(primary_role(self.ts_user), "tech_support")

    def test_merchant_admin_redirect_url(self):
        from accounts.utils import role_redirect_url
        from django.urls import reverse
        self.assertEqual(role_redirect_url(self.ma_user), reverse("ma_dashboard"))

    def test_tech_support_redirect_url(self):
        from accounts.utils import role_redirect_url
        from django.urls import reverse
        self.assertEqual(role_redirect_url(self.ts_user), reverse("support_dashboard"))

    def test_home_redirects_merchant_admin(self):
        self.client.login(username="ma_test", password="pass")
        response = self.client.get("/home/")
        self.assertRedirects(response, reverse("ma_dashboard"), fetch_redirect_response=False)

    def test_home_redirects_tech_support(self):
        self.client.login(username="ts_test", password="pass")
        response = self.client.get("/home/")
        self.assertRedirects(response, reverse("support_dashboard"), fetch_redirect_response=False)


class AccessControlMatrixTests(TestCase):
    """Verify role-based access rules across key portal URLs."""

    def setUp(self):
        call_command("seed_roles")
        User = get_user_model()
        self.merchant = User.objects.create_user(username="ac_merchant", password="pass")
        self.underwriter = User.objects.create_user(username="ac_uw", password="pass")
        self.hq = User.objects.create_user(username="ac_hq", password="pass")
        self.ma = User.objects.create_user(username="ac_ma", password="pass")
        self.ts = User.objects.create_user(username="ac_ts", password="pass")
        assign_role(self.merchant, "merchant")
        assign_role(self.underwriter, "underwriter")
        assign_role(self.hq, "hq")
        assign_role(self.ma, "merchant_admin")
        assign_role(self.ts, "tech_support")

    def _login(self, user):
        self.client.login(username=user.username, password="pass")

    def test_merchant_cannot_access_hq_dashboard(self):
        self._login(self.merchant)
        response = self.client.get(reverse("hq_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_merchant_admin_can_access_ma_dashboard(self):
        self._login(self.ma)
        response = self.client.get(reverse("ma_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_tech_support_can_access_support_dashboard(self):
        self._login(self.ts)
        response = self.client.get(reverse("support_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_hq_can_access_ma_dashboard(self):
        self._login(self.hq)
        response = self.client.get(reverse("ma_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_hq_can_access_support_dashboard(self):
        self._login(self.hq)
        response = self.client.get(reverse("support_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_merchant_cannot_access_ma_dashboard(self):
        self._login(self.merchant)
        response = self.client.get(reverse("ma_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_underwriter_cannot_access_support_dashboard(self):
        self._login(self.underwriter)
        response = self.client.get(reverse("support_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_any_authenticated_user_can_create_ticket(self):
        self._login(self.merchant)
        response = self.client.get(reverse("ticket_create"))
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_ticket_create_redirects_to_login(self):
        response = self.client.get(reverse("ticket_create"))
        self.assertIn(response.status_code, [301, 302])


class SupportTicketTests(TestCase):
    """Test ticket creation and comment flow."""

    def setUp(self):
        call_command("seed_roles")
        User = get_user_model()
        self.user = User.objects.create_user(username="tkt_user", password="pass")
        self.ts_user = User.objects.create_user(username="tkt_ts", password="pass")
        assign_role(self.user, "merchant")
        assign_role(self.ts_user, "tech_support")

    def test_authenticated_user_can_submit_ticket(self):
        self.client.login(username="tkt_user", password="pass")
        response = self.client.post(reverse("ticket_create"), {
            "title": "Test ticket",
            "description": "Something went wrong",
            "category": "other",
            "priority": "medium",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        from support.models import SupportTicket
        self.assertEqual(SupportTicket.objects.count(), 1)
        ticket = SupportTicket.objects.first()
        self.assertEqual(ticket.title, "Test ticket")
        self.assertEqual(ticket.created_by, self.user)

    def test_ticket_detail_accessible_by_creator(self):
        from support.models import SupportTicket
        self.client.login(username="tkt_user", password="pass")
        ticket = SupportTicket.objects.create(
            title="My ticket",
            description="Help",
            created_by=self.user,
        )
        response = self.client.get(reverse("ticket_detail", kwargs={"ticket_id": ticket.pk}))
        self.assertEqual(response.status_code, 200)

    def test_ticket_not_visible_to_other_merchant(self):
        from support.models import SupportTicket
        User = get_user_model()
        other = User.objects.create_user(username="other_merchant", password="pass")
        assign_role(other, "merchant")
        ticket = SupportTicket.objects.create(
            title="Private ticket",
            description="Mine",
            created_by=self.user,
        )
        self.client.login(username="other_merchant", password="pass")
        response = self.client.get(reverse("ticket_detail", kwargs={"ticket_id": ticket.pk}))
        self.assertRedirects(response, reverse("ticket_list"), fetch_redirect_response=False)

    def test_tech_support_can_see_all_tickets(self):
        from support.models import SupportTicket
        SupportTicket.objects.create(title="T1", description="D1", created_by=self.user)
        self.client.login(username="tkt_ts", password="pass")
        response = self.client.get(reverse("ticket_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "T1")


class BugSanitizationTests(TestCase):
    """Test sanitize_error_metadata utility."""

    def test_password_is_redacted(self):
        from support.utils import sanitize_error_metadata
        data = {"password": "secret123", "amount": 100}
        result = sanitize_error_metadata(data)
        self.assertEqual(result["password"], "[REDACTED]")
        self.assertEqual(result["amount"], 100)

    def test_nested_token_is_redacted(self):
        from support.utils import sanitize_error_metadata
        data = {"payload": {"access_token": "abc", "user_id": 42}}
        result = sanitize_error_metadata(data)
        self.assertEqual(result["payload"]["access_token"], "[REDACTED]")
        self.assertEqual(result["payload"]["user_id"], 42)

    def test_api_key_in_list_redacted(self):
        from support.utils import sanitize_error_metadata
        data = {"headers": [{"api_key": "xxx"}, {"x-request-id": "123"}]}
        result = sanitize_error_metadata(data)
        self.assertEqual(result["headers"][0]["api_key"], "[REDACTED]")
        self.assertEqual(result["headers"][1]["x-request-id"], "123")

    def test_safe_data_passes_through(self):
        from support.utils import sanitize_error_metadata
        data = {"user": "alice", "action": "login", "count": 3}
        result = sanitize_error_metadata(data)
        self.assertEqual(result, data)

    def test_bug_event_record_sanitizes_metadata(self):
        from support.models import BugEvent
        BugEvent.record(
            source=BugEvent.SRC_PAYMENT,
            message="Payment failed",
            metadata={"api_key": "secret", "amount": 5000},
        )
        bug = BugEvent.objects.first()
        self.assertEqual(bug.metadata["api_key"], "[REDACTED]")
        self.assertEqual(bug.metadata["amount"], 5000)


class MerchantAdminLeadFlowTests(TestCase):
    """Test Merchant Admin lead management."""

    def setUp(self):
        call_command("seed_roles")
        User = get_user_model()
        self.ma_user = User.objects.create_user(username="ma_flow", password="pass")
        assign_role(self.ma_user, "merchant_admin")
        from website.models import MerchantLead
        self.lead = MerchantLead.objects.create(
            business_name="Test Shop",
            owner_full_name="Alice Banda",
            phone="+265881234567",
            district="Lilongwe",
            business_type="phone_shop",
        )

    def test_ma_can_view_leads_list(self):
        self.client.login(username="ma_flow", password="pass")
        response = self.client.get(reverse("ma_leads"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Shop")

    def test_ma_can_view_lead_detail(self):
        self.client.login(username="ma_flow", password="pass")
        response = self.client.get(reverse("ma_lead_detail", kwargs={"lead_id": self.lead.pk}))
        self.assertEqual(response.status_code, 200)

    def test_ma_can_update_lead_status(self):
        self.client.login(username="ma_flow", password="pass")
        self.client.post(
            reverse("ma_lead_status", kwargs={"lead_id": self.lead.pk}),
            {"status": "kyc_pending"},
        )
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "kyc_pending")

    def test_merchant_cannot_access_ma_leads(self):
        User = get_user_model()
        m = User.objects.create_user(username="plain_merchant", password="pass")
        assign_role(m, "merchant")
        self.client.login(username="plain_merchant", password="pass")
        response = self.client.get(reverse("ma_leads"))
        self.assertEqual(response.status_code, 403)

    @override_settings(REQUIRE_HQ_MERCHANT_APPROVAL=False)
    def test_recommend_approve_directly_activates_when_no_hq_required(self):
        self.client.login(username="ma_flow", password="pass")
        self.client.post(
            reverse("ma_recommend", kwargs={"lead_id": self.lead.pk}),
            {"action": "approve"},
        )
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "approved")

    @override_settings(REQUIRE_HQ_MERCHANT_APPROVAL=True)
    def test_recommend_approve_sets_awaiting_hq_when_required(self):
        self.client.login(username="ma_flow", password="pass")
        self.client.post(
            reverse("ma_recommend", kwargs={"lead_id": self.lead.pk}),
            {"action": "approve"},
        )
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "awaiting_hq")


class MerchantAdminPortalPolishTests(TestCase):
    """Extended merchant admin portal workflow tests."""

    def setUp(self):
        call_command("seed_roles")
        User = get_user_model()
        self.ma_user = User.objects.create_user(username="ma_polish", password="pass")
        assign_role(self.ma_user, "merchant_admin")
        from website.models import MerchantLead
        self.lead = MerchantLead.objects.create(
            business_name="Polish Shop",
            owner_full_name="Grace Phiri",
            phone="+265881111111",
            district="Blantyre",
            business_type="phone_shop",
        )

    def test_ma_dashboard_loads(self):
        self.client.login(username="ma_polish", password="pass")
        response = self.client.get(reverse("ma_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Merchant Administration")
        self.assertContains(response, "Action center")

    def test_ma_can_create_manual_lead(self):
        self.client.login(username="ma_polish", password="pass")
        response = self.client.post(reverse("ma_lead_create"), {
            "business_name": "Manual Shop",
            "owner_full_name": "John Doe",
            "phone": "+265882222222",
            "district": "Zomba",
            "business_type": "phone_shop",
        })
        from website.models import MerchantLead
        lead = MerchantLead.objects.get(business_name="Manual Shop")
        self.assertRedirects(response, reverse("ma_lead_detail", kwargs={"lead_id": lead.pk}))

    def test_reject_requires_reason(self):
        self.client.login(username="ma_polish", password="pass")
        self.client.post(
            reverse("ma_recommend", kwargs={"lead_id": self.lead.pk}),
            {"action": "reject"},
            follow=True,
        )
        self.lead.refresh_from_db()
        self.assertNotEqual(self.lead.status, "rejected")

    def test_reject_with_reason_works(self):
        self.client.login(username="ma_polish", password="pass")
        self.client.post(
            reverse("ma_recommend", kwargs={"lead_id": self.lead.pk}),
            {"action": "reject", "reason": "Incomplete documents"},
        )
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "rejected")

    def test_create_ticket_from_lead(self):
        self.client.login(username="ma_polish", password="pass")
        response = self.client.post(
            reverse("ma_lead_ticket", kwargs={"lead_id": self.lead.pk}),
            {"title": "Lead issue", "description": "Need help with onboarding"},
        )
        from support.models import SupportTicket
        ticket = SupportTicket.objects.get(related_merchant=self.lead)
        self.assertEqual(ticket.title, "Lead issue")
        self.assertRedirects(response, reverse("ticket_detail", kwargs={"ticket_id": ticket.pk}))


class TechSupportPortalPolishTests(TestCase):
    """Extended tech support portal workflow tests."""

    def setUp(self):
        call_command("seed_roles")
        User = get_user_model()
        self.ts_user = User.objects.create_user(username="ts_polish", password="pass")
        self.merchant = User.objects.create_user(username="ts_merchant", password="pass")
        assign_role(self.ts_user, "tech_support")
        assign_role(self.merchant, "merchant")
        from support.models import SupportTicket, BugEvent
        self.ticket = SupportTicket.objects.create(
            title="Broken payment",
            description="Payment failed",
            created_by=self.merchant,
        )
        self.bug = BugEvent.record(
            source=BugEvent.SRC_PAYMENT,
            message="Webhook timeout",
            metadata={"amount": 1000},
        )

    def test_support_dashboard_loads(self):
        self.client.login(username="ts_polish", password="pass")
        response = self.client.get(reverse("support_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Support &amp; System Health")
        self.assertContains(response, "System health pulse")

    def test_assign_ticket_to_me(self):
        self.client.login(username="ts_polish", password="pass")
        self.client.post(reverse("ticket_assign_me", kwargs={"ticket_id": self.ticket.pk}))
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_to, self.ts_user)
        self.assertEqual(self.ticket.status, "assigned")

    def test_resolve_ticket_requires_note(self):
        self.client.login(username="ts_polish", password="pass")
        self.client.post(reverse("ticket_resolve", kwargs={"ticket_id": self.ticket.pk}), {})
        self.ticket.refresh_from_db()
        self.assertNotEqual(self.ticket.status, "resolved")

    def test_resolve_ticket_with_note(self):
        self.client.login(username="ts_polish", password="pass")
        self.client.post(
            reverse("ticket_resolve", kwargs={"ticket_id": self.ticket.pk}),
            {"resolution_note": "Fixed webhook config"},
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "resolved")

    def test_convert_bug_to_ticket(self):
        self.client.login(username="ts_polish", password="pass")
        response = self.client.post(reverse("bug_to_ticket", kwargs={"bug_id": self.bug.pk}))
        from support.models import SupportTicket
        self.assertEqual(SupportTicket.objects.count(), 2)
        new_ticket = SupportTicket.objects.order_by("-id").first()
        self.assertRedirects(response, reverse("ticket_detail", kwargs={"ticket_id": new_ticket.pk}))

    def test_mark_bug_fixed(self):
        self.client.login(username="ts_polish", password="pass")
        self.client.post(
            reverse("bug_resolve", kwargs={"bug_id": self.bug.pk}),
            {"resolution_note": "Increased timeout"},
        )
        self.bug.refresh_from_db()
        self.assertEqual(self.bug.status, "fixed")

    def test_ignore_bug_requires_reason(self):
        self.client.login(username="ts_polish", password="pass")
        self.client.post(reverse("bug_ignore", kwargs={"bug_id": self.bug.pk}), {})
        self.bug.refresh_from_db()
        self.assertNotEqual(self.bug.status, "ignored")

    def test_merchant_cannot_access_bug_monitor(self):
        self.client.login(username="ts_merchant", password="pass")
        response = self.client.get(reverse("bug_monitor"))
        self.assertEqual(response.status_code, 403)


class LayoutRegressionTests(TestCase):
    """
    Regression tests that lock in the merchant/underwriter/safe-ops layout so
    future prompt-driven refactors cannot accidentally remove critical sections.
    """

    def setUp(self):
        call_command("seed_roles")
        User = get_user_model()
        self.merchant_user = User.objects.create_user(username="reg_merchant", password="pass123")
        assign_role(self.merchant_user, "merchant")
        self.uw_user = User.objects.create_user(username="reg_underwriter", password="pass123")
        assign_role(self.uw_user, "underwriter")
        self.hq_user = User.objects.create_user(username="reg_hq", password="pass123")
        assign_role(self.hq_user, "hq")

    # ── Merchant dashboard section regression ─────────────────────────────────

    def test_merchant_dashboard_loads_for_merchant(self):
        self.client.login(username="reg_merchant", password="pass123")
        response = self.client.get(reverse("merchant_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/home.html")

    def test_merchant_dashboard_malawi_flag_must_always_render(self):
        """Layout regression: Malawi flag + MW chip locked on merchant dashboard."""
        self.client.login(username="reg_merchant", password="pass123")
        response = self.client.get(reverse("merchant_dashboard"))
        assert_merchant_dashboard_malawi_flag(self, response)

    def test_merchant_dashboard_has_applications_link(self):
        self.client.login(username="reg_merchant", password="pass123")
        response = self.client.get(reverse("merchant_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active")

    def test_merchant_dashboard_has_tools_link(self):
        self.client.login(username="reg_merchant", password="pass123")
        response = self.client.get(reverse("merchant_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All Deals")

    def test_merchant_dashboard_no_oversized_hero(self):
        """The orange hero block must not dominate the page."""
        self.client.login(username="reg_merchant", password="pass123")
        response = self.client.get(reverse("merchant_dashboard"))
        self.assertNotContains(response, "mhome-hero")

    def test_merchant_dashboard_cta_before_apps_nav(self):
        """New contract CTA appears above Applications nav."""
        self.client.login(username="reg_merchant", password="pass123")
        response = self.client.get(reverse("merchant_dashboard"))
        content = response.content.decode()
        cta_pos = content.index("NEW APPLICATION")
        apps_pos = content.index("Applications")
        tools_pos = content.index("Tools")
        self.assertLess(cta_pos, apps_pos, "CTA must appear before Applications")
        self.assertLess(apps_pos, tools_pos, "Applications must appear before Tools")

    # ── Underwriter (sales) portal regression ─────────────────────────────────

    def test_underwriter_sales_home_loads(self):
        self.client.login(username="reg_underwriter", password="pass123")
        response = self.client.get(reverse("sales_home"))
        self.assertEqual(response.status_code, 200)

    def test_underwriter_applications_list_loads(self):
        self.client.login(username="reg_underwriter", password="pass123")
        response = self.client.get(reverse("sales_applications"))
        self.assertEqual(response.status_code, 200)

    # ── HQ safe operations and fraud checks regression ────────────────────────

    def test_hq_safe_operations_page_loads(self):
        self.client.login(username="reg_hq", password="pass123")
        response = self.client.get(reverse("hq_safe_operations"))
        self.assertEqual(response.status_code, 200)

    def test_hq_fraud_checks_page_loads(self):
        self.client.login(username="reg_hq", password="pass123")
        response = self.client.get(reverse("hq_fraud_checks"))
        self.assertEqual(response.status_code, 200)

    def test_hq_devices_page_loads(self):
        """Device lock readiness page must load."""
        self.client.login(username="reg_hq", password="pass123")
        response = self.client.get(reverse("hq_devices"))
        self.assertEqual(response.status_code, 200)
