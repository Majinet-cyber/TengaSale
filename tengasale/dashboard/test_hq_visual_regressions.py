"""HQ presentation contracts: real route coverage and shared accessible controls."""
import json
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.template.loader import get_template, render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import URLResolver, get_resolver, reverse
from django.contrib.auth import get_user_model
from accounts.utils import assign_role


class HQSalesPulseVisualRegressionTests(SimpleTestCase):
    def test_all_baseline_hq_named_routes_are_preserved(self):
        baseline = json.loads((Path(settings.BASE_DIR).parent / "docs/TENGA_HQ_ROUTE_AUDIT.json").read_text())
        def names(patterns):
            result = set()
            for pattern in patterns:
                if isinstance(pattern, URLResolver):
                    result.update(names(pattern.url_patterns))
                elif pattern.name and pattern.name.startswith("hq_"):
                    result.add(pattern.name)
            return result
        self.assertEqual(set(baseline["hq_named_routes"]), names(get_resolver().url_patterns))

    def test_every_original_hq_destination_remains_in_composed_templates(self):
        import re
        base = Path(settings.BASE_DIR) / "templates"
        audit = json.loads((base.parent.parent / "docs/TENGA_HQ_ROUTE_AUDIT.json").read_text())
        files = [base / "dashboard/hq.html", base / "base/base.html"]
        files += list((base / "dashboard/partials").glob("hq_*.html"))
        files += list((base / "partials").glob("tenga_*.html"))
        source = "\n".join(p.read_text(encoding="utf-8") for p in files)
        destinations = set(re.findall(r"{% url ['\"]([^'\"]+)", source))
        self.assertFalse(set(audit["hq_template_destinations"]) - destinations)

    def test_hq_composition_and_partials_compile(self):
        for path in ("dashboard/hq.html", "dashboard/partials/hq_sidebar.html", "dashboard/partials/hq_tool_directory.html", "dashboard/partials/hq_action_center.html", "dashboard/partials/hq_finance_summary.html", "partials/tenga_topbar.html"):
            get_template(path)

    def test_shared_actions_have_country_support_notification_and_csrf_logout_in_order(self):
        html = render_to_string("partials/tenga_topbar_actions.html", {
            "tengasale_whatsapp_link": "https://wa.me/265999000000",
            "unread_notification_count": 3, "csrf_token": "test-csrf-token",
        })
        markers = ["country-pill-mw", "topbar-whatsapp", "topbar-notifications", "topbar-logout"]
        self.assertEqual(sorted(html.index(m) for m in markers), [html.index(m) for m in markers])
        self.assertIn('aria-label="Malawi"', html)
        self.assertNotIn(">Malawi<", html)
        self.assertIn('method="post"', html)
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn("3 unread", html)
        self.assertIn(reverse("logout"), html)

    def test_empty_action_center_is_honest_and_pending_work_has_destination(self):
        clear = render_to_string("dashboard/partials/hq_action_center.html", {})
        self.assertIn("All clear", clear)
        pending = render_to_string("dashboard/partials/hq_action_center.html", {"waiting_count": 2})
        self.assertIn("2 applications waiting", pending)
        self.assertIn(reverse("hq_applications") + "?status=pending_review", pending)
        self.assertNotIn("All clear", pending)

    def test_staff_tools_keep_permission_gates(self):
        request = SimpleNamespace(resolver_match=SimpleNamespace(url_name="hq_dashboard"))
        restricted = render_to_string("dashboard/partials/hq_tool_directory.html", {"request": request, "staff_module_keys": []})
        self.assertNotIn(reverse("hq_staff_documents"), restricted)
        allowed = render_to_string("dashboard/partials/hq_tool_directory.html", {"request": request, "staff_module_keys": ["staff_documents"]})
        self.assertIn(reverse("hq_staff_documents"), allowed)


class HQSharedShellRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="ui-hq")
        assign_role(cls.user, "hq")

    def test_hq_operational_pages_render_with_shared_navigation(self):
        self.client.force_login(self.user)
        for route in ("hq_dashboard", "hq_users", "hq_applications", "hq_portfolio", "hq_devices", "hq_underwriter_queue", "hq_payment_collections", "hq_reports", "hq_reconciliation", "hq_deals"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="hq-all-tools"')
                self.assertContains(response, 'data-testid="topbar-logout"')
                self.assertContains(response, 'aria-label="Malawi"')

    def test_hq_routes_still_require_login(self):
        response = self.client.get(reverse("hq_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_authenticated_homes_have_one_shared_country_and_action_group(self):
        for role, path in (("hq", "/tengasale/hq/"), ("merchant", "/tengasale/merchant/"), ("underwriter", "/sales/")):
            with self.subTest(role=role):
                user = get_user_model().objects.create_user(username="shared-header-" + role)
                assign_role(user, role)
                self.client.force_login(user)
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                for marker in ("country-pill-mw", "topbar-whatsapp", "topbar-notifications", "topbar-logout"):
                    self.assertContains(response, f'data-testid="{marker}"', count=1)
                self.assertNotContains(response, ">Malawi<")
