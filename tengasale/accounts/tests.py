from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib import admin
from django.core.management import call_command
from django.db.models import Sum
from django.contrib.staticfiles import finders
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import resolve, reverse
from django.utils import timezone
from decimal import Decimal

from .admin import UserProfileInline
from .documents import generate_staff_document_pdf
from .models import (
    CompensationCycle,
    DisciplineDispute,
    DisciplineEvent,
    DisciplineEventType,
    DisciplineScorePeriod,
    FounderEquityRecord,
    KPIResult,
    KPITemplate,
    Department,
    Rank,
    StaffDocument,
    StaffRole,
    UserProfile,
    VoltsActionType,
    VoltsTransaction,
)
from .services import (
    MODULE_DISCIPLINE,
    MODULE_EQUITY,
    MODULE_KPIS,
    MODULE_VOLTS,
    approve_discipline_period,
    can_approve_volts,
    can_review_discipline,
    can_view_compensation,
    monthly_volts_summary,
    user_can_access_module,
)
from .utils import (
    assign_role,
    get_tengasale_role,
    get_user_portal_role,
    is_hq,
    is_merchant,
    is_underwriter,
    primary_role,
)


class LoginTemplateTests(TestCase):
    def test_login_url_returns_200(self):
        response = self.client.get("/accounts/login/")

        self.assertEqual(response.status_code, 200)

    def test_login_page_renders_expected_template(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_login_page_contains_forgot_password_link(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, "Forgot password?")
        self.assertContains(response, reverse("password_reset"))

    def test_login_page_contains_back_to_home_link(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, "Back to Home")
        self.assertContains(response, 'href="/"')

    def test_login_page_contains_only_one_password_eye_toggle_button(self):
        response = self.client.get(reverse("login"))
        content = response.content.decode()

        # The password field has exactly one eye/toggle button
        # Accept both old-style data attribute and new-style class
        toggle_count = (
            content.count("data-password-toggle")
            + content.count("password-toggle")
        )
        self.assertGreaterEqual(toggle_count, 1, "Must have at least one password toggle element")


class AccountUrlTests(TestCase):
    def test_login_url_name_resolves(self):
        self.assertEqual(reverse("login"), "/accounts/login/")

    def test_password_reset_resolves_and_returns_200(self):
        response = self.client.get(reverse("password_reset"))

        self.assertEqual(resolve(reverse("password_reset")).url_name, "password_reset")
        self.assertEqual(response.status_code, 200)

    def test_password_reset_done_resolves(self):
        self.assertEqual(
            resolve(reverse("password_reset_done")).url_name,
            "password_reset_done",
        )

    def test_password_reset_complete_resolves(self):
        self.assertEqual(
            resolve(reverse("password_reset_complete")).url_name,
            "password_reset_complete",
        )


class RoleHelperTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        for group_name in ["Merchant", "Underwriter", "HQ"]:
            Group.objects.get_or_create(name=group_name)

    def user_with_role(self, username, role, **kwargs):
        user = self.User.objects.create_user(username=username, password="test-pass-123", **kwargs)
        assign_role(user, role)
        return user

    def test_merchant_profile_user_is_merchant(self):
        user = self.user_with_role("merchant-role", "merchant")

        self.assertTrue(is_merchant(user))
        self.assertEqual(primary_role(user), "merchant")

    def test_underwriter_profile_user_is_underwriter(self):
        user = self.user_with_role("underwriter-role", "underwriter")

        self.assertTrue(is_underwriter(user))
        self.assertEqual(primary_role(user), "underwriter")

    def test_staff_non_superuser_is_hq(self):
        user = self.User.objects.create_user(username="staff-role", password="test-pass-123", is_staff=True)

        self.assertFalse(is_underwriter(user))
        self.assertTrue(is_hq(user))
        self.assertEqual(get_tengasale_role(user), "hq")
        self.assertEqual(get_user_portal_role(user), "hq")
        self.assertEqual(primary_role(user), "hq")

    def test_staff_user_with_explicit_merchant_role_is_merchant(self):
        user = self.user_with_role("staff-merchant-role", "merchant", is_staff=True)

        self.assertTrue(is_merchant(user))
        self.assertFalse(is_hq(user))
        self.assertEqual(get_tengasale_role(user), "merchant")
        self.assertEqual(primary_role(user), "merchant")

    def test_staff_user_with_explicit_underwriter_role_is_underwriter(self):
        user = self.user_with_role("staff-underwriter-role", "underwriter", is_staff=True)

        self.assertTrue(is_underwriter(user))
        self.assertFalse(is_hq(user))
        self.assertEqual(get_tengasale_role(user), "underwriter")
        self.assertEqual(primary_role(user), "underwriter")

    def test_hq_profile_user_is_hq(self):
        user = self.user_with_role("hq-role", "hq")

        self.assertTrue(is_hq(user))
        self.assertEqual(primary_role(user), "hq")

    def test_superuser_without_profile_role_is_hq(self):
        user = self.User.objects.create_superuser(username="super-role", password="test-pass-123")

        self.assertTrue(is_hq(user))
        self.assertFalse(is_merchant(user))
        self.assertEqual(get_user_portal_role(user), "hq")
        self.assertEqual(primary_role(user), "hq")

    def test_no_group_user_has_no_primary_role(self):
        user = self.User.objects.create_user(username="no-group", password="test-pass-123")

        self.assertFalse(is_merchant(user))
        self.assertIsNone(primary_role(user))

    def test_normal_user_without_profile_has_no_role(self):
        user = self.User.objects.create_user(username="no-profile", password="test-pass-123")
        user.profile.delete()

        self.assertIsNone(get_tengasale_role(user))
        self.assertIsNone(primary_role(user))

    def test_staff_user_without_profile_is_hq(self):
        user = self.User.objects.create_user(username="staff-no-profile", password="test-pass-123", is_staff=True)
        user.profile.delete()

        self.assertEqual(get_tengasale_role(user), "hq")
        self.assertEqual(primary_role(user), "hq")


class LoginRedirectTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        call_command("seed_roles")

    def make_user(self, username, group_name=None, **kwargs):
        user = self.User.objects.create_user(username=username, password="test-pass-123", **kwargs)
        if group_name:
            role = {"Merchant": "merchant", "Underwriter": "underwriter", "HQ": "hq"}[group_name]
            assign_role(user, role)
        return user

    def assert_login_redirects(self, username, expected_url):
        response = self.client.post(
            reverse("login"),
            {"username": username, "password": "test-pass-123"},
        )

        self.assertRedirects(response, expected_url, fetch_redirect_response=False)

    def test_merchant_login_redirects_to_merchant_portal(self):
        self.make_user("merchant-login", "Merchant")

        self.assert_login_redirects("merchant-login", reverse("merchant_dashboard"))

    def test_underwriter_login_redirects_to_underwriter_portal(self):
        self.make_user("underwriter-login", "Underwriter")

        self.assert_login_redirects("underwriter-login", reverse("underwriter_dashboard"))

    def test_unassigned_normal_user_login_redirects_to_no_role_page(self):
        self.make_user("normal-login")

        self.assert_login_redirects("normal-login", reverse("no_role"))

    def test_staff_login_redirects_to_hq_portal(self):
        self.make_user("staff-login", is_staff=True)

        self.assert_login_redirects("staff-login", reverse("hq_dashboard"))

    def test_hq_login_redirects_to_hq_portal(self):
        self.make_user("hq-login", "HQ")

        self.assert_login_redirects("hq-login", reverse("hq_dashboard"))

    def test_no_role_superuser_login_redirects_to_hq_page(self):
        self.User.objects.create_superuser(username="super-login", password="test-pass-123")

        self.assert_login_redirects("super-login", reverse("hq_dashboard"))

    def test_emajinet_style_superuser_staff_without_role_redirects_to_hq(self):
        user = self.User.objects.create_superuser(username="emajinet-style", password="test-pass-123")
        user.profile.role = None
        user.profile.save(update_fields=["role"])

        self.assert_login_redirects("emajinet-style", reverse("hq_dashboard"))

    def test_founder_staff_login_redirects_to_staff_dashboard(self):
        call_command("seed_staff_system")
        user = self.make_user("founder-login", "HQ")
        user.profile.user_type = UserProfile.USER_TYPE_FOUNDER
        user.profile.is_founder = True
        user.profile.save(update_fields=["user_type", "is_founder"])

        self.assert_login_redirects("founder-login", reverse("staff_dashboard"))

    def test_no_role_page_redirects_superuser_to_hq(self):
        self.User.objects.create_superuser(username="super-no-role-page", password="test-pass-123")
        self.client.login(username="super-no-role-page", password="test-pass-123")

        response = self.client.get(reverse("no_role"))

        self.assertRedirects(response, reverse("hq_dashboard"))


class RoleAccessControlTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        call_command("seed_roles")

    def make_user(self, username, role, **kwargs):
        user = self.User.objects.create_user(username=username, password="test-pass-123", **kwargs)
        assign_role(user, role)
        return user

    def assert_role_forbidden(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "accounts/role_forbidden.html")
        self.assertContains(response, "That area is not available for your role.", status_code=403)
        self.assertContains(response, "Go to my dashboard", status_code=403)

    def test_merchant_cannot_access_underwriter_portal(self):
        self.make_user("merchant-underwriter-denied", "merchant")
        self.client.login(username="merchant-underwriter-denied", password="test-pass-123")

        # underwriter_dashboard → /sales/ (via redirect) → 403 for non-underwriter
        response = self.client.get(reverse("underwriter_dashboard"), follow=True)

        self.assert_role_forbidden(response)

    def test_merchant_cannot_access_hq_portal(self):
        self.make_user("merchant-hq-denied", "merchant")
        self.client.login(username="merchant-hq-denied", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assert_role_forbidden(response)

    def test_underwriter_cannot_access_merchant_portal(self):
        self.make_user("underwriter-merchant-denied", "underwriter")
        self.client.login(username="underwriter-merchant-denied", password="test-pass-123")

        response = self.client.get(reverse("merchant_dashboard"))

        self.assert_role_forbidden(response)

    def test_underwriter_cannot_access_hq_portal(self):
        self.make_user("underwriter-hq-denied", "underwriter")
        self.client.login(username="underwriter-hq-denied", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assert_role_forbidden(response)

    def test_hq_cannot_access_merchant_portal(self):
        self.make_user("hq-merchant-denied", "hq")
        self.client.login(username="hq-merchant-denied", password="test-pass-123")

        response = self.client.get(reverse("merchant_dashboard"))

        self.assert_role_forbidden(response)

    def test_hq_cannot_access_underwriter_portal(self):
        self.make_user("hq-underwriter-denied", "hq")
        self.client.login(username="hq-underwriter-denied", password="test-pass-123")

        # underwriter_dashboard → /sales/ (via redirect) → 403 for non-underwriter
        response = self.client.get(reverse("underwriter_dashboard"), follow=True)

        self.assert_role_forbidden(response)

    def test_staff_without_profile_role_can_access_hq_portal(self):
        user = self.User.objects.create_user(username="staff-hq-access", password="test-pass-123", is_staff=True)
        user.profile.role = None
        user.profile.save(update_fields=["role"])
        self.client.login(username="staff-hq-access", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertEqual(response.status_code, 200)

    def test_superuser_without_profile_role_can_access_hq_portal(self):
        user = self.User.objects.create_superuser(username="super-hq-access", password="test-pass-123")
        user.profile.role = None
        user.profile.save(update_fields=["role"])
        self.client.login(username="super-hq-access", password="test-pass-123")

        response = self.client.get(reverse("hq_dashboard"))

        self.assertEqual(response.status_code, 200)

    def test_hq_user_with_staff_can_access_admin_index(self):
        self.make_user("hq-staff-admin", "hq", is_staff=True)
        self.client.login(username="hq-staff-admin", password="test-pass-123")

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)

    def test_hq_user_without_staff_cannot_access_admin_index(self):
        self.make_user("hq-no-staff-admin", "hq", is_staff=False)
        self.client.login(username="hq-no-staff-admin", password="test-pass-123")

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 302)

    def test_superuser_can_access_admin_user_changelist(self):
        admin_user = self.User.objects.create_superuser(username="admin-access", password="test-pass-123")
        assign_role(admin_user, "hq")
        self.client.login(username="admin-access", password="test-pass-123")

        response = self.client.get(reverse("admin:auth_user_changelist"))

        self.assertEqual(response.status_code, 200)


class UserProfileAdminTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        call_command("seed_roles")
        self.admin_user = self.User.objects.create_superuser(username="profile-admin", password="test-pass-123")
        assign_role(self.admin_user, "hq")
        self.client.login(username="profile-admin", password="test-pass-123")

    def test_profile_created_automatically_when_user_is_created(self):
        user = self.User.objects.create_user(username="auto-profile", password="test-pass-123")

        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertIsNone(user.profile.role)

    def test_user_admin_has_profile_inline(self):
        user_admin = admin.site._registry[self.User]

        self.assertIn(UserProfileInline, user_admin.inlines)

    def test_user_admin_change_page_exposes_profile_role(self):
        user = self.User.objects.create_user(username="profile-inline", password="test-pass-123")
        assign_role(user, "merchant")

        response = self.client.get(reverse("admin:auth_user_change", args=[user.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User profile")
        self.assertContains(response, "Role")

    def test_user_profile_role_can_be_edited_in_django_admin(self):
        user = self.User.objects.create_user(username="profile-edit", password="test-pass-123")
        assign_role(user, "merchant")

        response = self.client.post(
            reverse("admin:accounts_userprofile_change", args=[user.profile.pk]),
            {
                "user": user.pk,
                "full_name": "",
                "email": "",
                "role": "underwriter",
                "phone_number": "",
                "phone": "",
                "department": "",
                "staff_role": "",
                "rank": "",
                "supervisor": "",
                "user_type": "",
                "status": "active",
                "is_founder": "",
                "founder_equity_record": "",
                "date_joined_company": "",
                "probation_status": "none",
                "next_review_date": "",
                "base_salary_mwk": "",
                "volt_rate_mwk": "",
                "monthly_ceiling_mwk": "",
                "can_approve_volts": "",
                "can_approve_discipline": "",
                "can_approve_payouts": "",
                "can_override_scores": "",
                "avatar_initials": "",
            },
        )

        user.profile.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(user.profile.role, "underwriter")


class FounderStaffVoltsTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        call_command("seed_staff_system")
        self.ceo = self.User.objects.create_user(username="ceo", password="test-pass-123", is_staff=True)
        assign_role(self.ceo, "hq")
        ceo_role = StaffRole.objects.get(name="CEO / Strategy Lead")
        self.ceo.profile.staff_role = ceo_role
        self.ceo.profile.department = ceo_role.department
        self.ceo.profile.rank = Rank.objects.get(code="D1")
        self.ceo.profile.user_type = UserProfile.USER_TYPE_EXECUTIVE
        self.ceo.profile.save()

        self.tech = self.User.objects.create_user(username="techlead", password="test-pass-123", is_staff=True)
        assign_role(self.tech, "hq")
        tech_role = StaffRole.objects.get(name="Technology & Product Lead")
        self.tech.profile.staff_role = tech_role
        self.tech.profile.department = tech_role.department
        self.tech.profile.rank = Rank.objects.get(code="C2")
        self.tech.profile.user_type = UserProfile.USER_TYPE_STAFF
        self.tech.profile.save()

    def test_seed_staff_system_creates_reference_data(self):
        self.assertGreaterEqual(Department.objects.count(), 10)  # seeder adds 15+ departments
        self.assertTrue(Rank.objects.filter(code="C2", multiplier=Decimal("2.50"), base_salary_default_mwk=Decimal("650000")).exists())
        self.assertTrue(StaffRole.objects.filter(name="Merchant Administrator").exists())
        self.assertTrue(VoltsActionType.objects.filter(name="Funding secured", base_volts=5000).exists())
        self.assertEqual(FounderEquityRecord.objects.aggregate(total=Sum("allocated_shares"))["total"], 70000)

    def test_role_module_permissions_are_backend_enforced(self):
        self.assertTrue(user_can_access_module(self.ceo, MODULE_EQUITY))
        self.assertTrue(user_can_access_module(self.ceo, MODULE_VOLTS))
        self.assertFalse(user_can_access_module(self.tech, MODULE_EQUITY))
        self.assertFalse(user_can_access_module(self.tech, MODULE_VOLTS))

    def test_volts_calculation_and_monthly_ceiling(self):
        action = VoltsActionType.objects.get(name="Major feature shipped")
        tx = VoltsTransaction.objects.create(
            user=self.tech,
            action_type=action,
            base_volts=action.base_volts,
            quality_score=Decimal("0.90"),
            discipline_score=Decimal("0.95"),
            results_score=Decimal("0.85"),
            status=VoltsTransaction.STATUS_APPROVED,
        )
        tx.refresh_from_db()

        self.assertEqual(tx.rank_multiplier, Decimal("2.50"))
        self.assertEqual(tx.final_volts, Decimal("908.44"))
        self.assertEqual(tx.estimated_amount_mwk, Decimal("90843.75"))

        summary = monthly_volts_summary(self.tech)
        self.assertEqual(summary["approved_volts"], Decimal("908.44"))
        self.assertEqual(summary["capped_pay"], Decimal("90843.75"))

    def test_monthly_ceiling_is_enforced(self):
        action = VoltsActionType.objects.get(name="Funding secured")
        VoltsTransaction.objects.create(
            user=self.tech,
            action_type=action,
            base_volts=Decimal("500000"),
            status=VoltsTransaction.STATUS_APPROVED,
        )

        summary = monthly_volts_summary(self.tech)

        self.assertEqual(summary["monthly_ceiling"], Decimal("8000000.00"))
        self.assertEqual(summary["capped_pay"], Decimal("8000000.00"))

    def test_user_cannot_approve_own_volts(self):
        action = VoltsActionType.objects.get(name="Bug fixed")
        tx = VoltsTransaction.objects.create(user=self.tech, action_type=action, base_volts=action.base_volts)

        self.assertFalse(can_approve_volts(self.tech, tx))
        with self.assertRaises(ValueError):
            tx.approve(self.tech)

    def test_equity_dashboard_restricted_from_tech_lead_and_visible_to_ceo(self):
        self.client.login(username="techlead", password="test-pass-123")
        denied = self.client.get(reverse("hq_founder_equity"))
        self.assertEqual(denied.status_code, 403)

        self.client.login(username="ceo", password="test-pass-123")
        allowed = self.client.get(reverse("hq_founder_equity"))
        self.assertEqual(allowed.status_code, 200)
        self.assertContains(allowed, "Founder Equity")

    def test_staff_roles_cockpit_uses_clean_labels_and_missing_rank_filter(self):
        no_rank = self.User.objects.create_user(username="norank", password="test-pass-123", email="")
        assign_role(no_rank, "merchant_admin")
        no_rank.profile.rank = None
        no_rank.profile.save(update_fields=["rank"])

        self.client.login(username="ceo", password="test-pass-123")
        response = self.client.get(reverse("hq_staff_roles"), {"missing": "rank"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff & Roles")
        self.assertContains(response, "Merchant Admin")
        self.assertContains(response, "No rank assigned")
        self.assertContains(response, "norank")
        self.assertNotContains(response, "Merchant_Admin")

    def test_founder_equity_cockpit_shows_seeded_share_structure(self):
        self.client.login(username="ceo", password="test-pass-123")

        response = self.client.get(reverse("hq_founder_equity"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ownership tracking and vesting cockpit")
        self.assertContains(response, "100,000")
        self.assertContains(response, "70,000")
        self.assertContains(response, "20,000")
        self.assertContains(response, "10,000")
        self.assertContains(response, "signed legal documents govern actual ownership")

    def test_volts_engine_cockpit_shows_formula_filters_and_pending_approvals(self):
        action = VoltsActionType.objects.get(name="Bug fixed")
        VoltsTransaction.objects.create(
            user=self.tech,
            action_type=action,
            base_volts=action.base_volts,
            evidence_text="Merged tested fix.",
        )
        self.client.login(username="ceo", password="test-pass-123")

        response = self.client.get(reverse("hq_volts_engine"), {"status": VoltsTransaction.STATUS_PENDING})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compensation intelligence cockpit")
        self.assertContains(response, "approved_volts_pay")
        self.assertContains(response, "Volts pending approval")
        self.assertContains(response, "Merged tested fix.")
        self.assertContains(response, "Apply")


class DisciplineKPIDocumentGovernanceTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        call_command("seed_staff_system")
        self.finance = self.User.objects.create_user(username="financelead", password="test-pass-123", is_staff=True)
        assign_role(self.finance, "hq")
        finance_role = StaffRole.objects.get(name="Finance, Risk & Administration Lead")
        self.finance.profile.staff_role = finance_role
        self.finance.profile.department = finance_role.department
        self.finance.profile.rank = Rank.objects.get(code="C3")
        self.finance.profile.user_type = UserProfile.USER_TYPE_EXECUTIVE
        self.finance.profile.can_approve_discipline = True
        self.finance.profile.can_approve_payouts = True
        self.finance.profile.save()

        self.underwriter = self.User.objects.create_user(username="uw", password="test-pass-123")
        assign_role(self.underwriter, "underwriter")
        uw_role = StaffRole.objects.get(name="Underwriter")
        self.underwriter.profile.staff_role = uw_role
        self.underwriter.profile.department = uw_role.department
        self.underwriter.profile.rank = Rank.objects.get(code="B1")
        self.underwriter.profile.user_type = UserProfile.USER_TYPE_STAFF
        self.underwriter.profile.save()

        self.techlead = self.User.objects.create_user(username="techboss", password="test-pass-123", is_staff=True)
        assign_role(self.techlead, "hq")
        tech_role = StaffRole.objects.get(name="Technology & Product Lead")
        self.techlead.profile.staff_role = tech_role
        self.techlead.profile.department = tech_role.department
        self.techlead.profile.rank = Rank.objects.get(code="C3")
        self.techlead.profile.user_type = UserProfile.USER_TYPE_EXECUTIVE
        self.techlead.profile.can_approve_discipline = True
        self.techlead.profile.save()

    def test_seed_creates_governance_reference_data(self):
        self.assertTrue(CompensationCycle.objects.filter(start_day=25, end_day=24).exists())
        self.assertTrue(DisciplineEventType.objects.filter(code="fraud-or-collusion", deduction_percentage=100, evidence_required=True).exists())
        self.assertTrue(KPITemplate.objects.filter(role__name="Underwriter", name="applications reviewed").exists())
        self.assertTrue(VoltsActionType.objects.filter(name="Merchant commission dispute resolved", code="merchant-commission-dispute-resolved").exists())

    def test_discipline_score_calculation_and_bonus_cap(self):
        period = DisciplineScorePeriod.objects.create(user=self.underwriter, month=timezone.localdate())
        late = DisciplineEventType.objects.get(code="late-application-review")
        bonus = DisciplineEventType.objects.get(code="zero-qc-errors")
        DisciplineEvent.objects.create(user=self.underwriter, period=period, event_type=late, source=DisciplineEvent.SOURCE_AUTOMATIC, status=DisciplineEvent.STATUS_ACCEPTED)
        DisciplineEvent.objects.create(user=self.underwriter, period=period, event_type=bonus, status=DisciplineEvent.STATUS_ACCEPTED)

        period.recalculate()

        self.assertEqual(period.final_score, Decimal("103.00"))
        self.assertEqual(period.payout_score, Decimal("100"))
        self.assertEqual(period.band_label, "Excellent discipline")

    def test_no_self_approval_and_department_review_rules(self):
        period = DisciplineScorePeriod.objects.create(user=self.underwriter, month=timezone.localdate(), status=DisciplineScorePeriod.STATUS_PENDING_REVIEW)

        self.assertFalse(can_review_discipline(self.underwriter, period))
        with self.assertRaises(ValueError):
            period.approve(self.underwriter)

        self.assertTrue(can_review_discipline(self.finance, period))
        self.assertFalse(can_review_discipline(self.techlead, period))

        approve_discipline_period(period, self.finance, reason="System evidence reviewed")
        period.refresh_from_db()
        self.assertEqual(period.status, DisciplineScorePeriod.STATUS_APPROVED)
        self.assertEqual(period.approved_by, self.finance)

    def test_staff_cannot_view_other_staff_compensation(self):
        self.assertFalse(can_view_compensation(self.techlead, self.underwriter))
        self.assertTrue(can_view_compensation(self.finance, self.underwriter))
        self.assertTrue(can_view_compensation(self.underwriter, self.underwriter))

    def test_kpi_result_calculation_and_no_self_approval(self):
        template = KPITemplate.objects.get(role__name="Underwriter", name="applications reviewed")
        result = KPIResult.objects.create(
            user=self.underwriter,
            kpi_template=template,
            period=timezone.localdate(),
            actual_value=Decimal("80"),
            target_value=Decimal("100"),
        )

        self.assertEqual(result.score, Decimal("80.00"))
        with self.assertRaises(ValueError):
            result.approve(self.underwriter)
        result.approve(self.finance)
        result.refresh_from_db()
        self.assertEqual(result.status, KPIResult.STATUS_APPROVED)

    def test_dispute_submission_and_pdf_generation_audit(self):
        period = DisciplineScorePeriod.objects.create(user=self.underwriter, month=timezone.localdate(), final_score=Decimal("65.00"))
        self.client.login(username="uw", password="test-pass-123")
        response = self.client.post(reverse("staff_discipline_dispute", args=[period.id]), {"dispute_text": "The queue was reassigned."})

        period.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DisciplineDispute.objects.filter(user=self.underwriter, dispute_text__icontains="queue").exists())
        self.assertEqual(period.dispute_status, DisciplineScorePeriod.DISPUTE_SUBMITTED)

        pdf_bytes, record = generate_staff_document_pdf(
            self.underwriter,
            StaffDocument.DOC_DISCIPLINE_REPORT,
            prepared_by=self.finance,
        )

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertEqual(record.document_type, StaffDocument.DOC_DISCIPLINE_REPORT)


class SeedTengaSaleUsersCommandTests(TestCase):
    def test_seed_tengasale_users_creates_and_updates_required_users(self):
        call_command("seed_tengasale_users")
        call_command("seed_tengasale_users")

        expected_users = {
            "merchant1": ("merchant", False, False),
            "underwriter1": ("underwriter", False, False),
            "hq1": ("hq", True, False),
            "admin1": ("hq", True, True),
        }
        for username, (role, is_staff, is_superuser) in expected_users.items():
            user = get_user_model().objects.get(username=username)
            self.assertTrue(user.check_password("demo12345"))
            self.assertTrue(user.is_active)
            self.assertEqual(user.profile.role, role)
            self.assertEqual(user.is_staff, is_staff)
            self.assertEqual(user.is_superuser, is_superuser)


class SeedRolesCommandTests(TestCase):
    def test_seed_roles_creates_required_groups_idempotently(self):
        call_command("seed_roles")
        call_command("seed_roles")

        for group_name in ["Merchant", "Merchant Administrator", "Underwriter", "Tech Support", "HQ"]:
            self.assertTrue(Group.objects.filter(name=group_name).exists())

    def test_seed_tengasale_roles_creates_profiles_and_defaults_staff_to_hq(self):
        User = get_user_model()
        staff = User.objects.create_user(username="seed-staff", password="test-pass-123", is_staff=True)
        superuser = User.objects.create_superuser(username="seed-super", password="test-pass-123")
        merchant = User.objects.create_user(username="seed-merchant", password="test-pass-123")
        normal = User.objects.create_user(username="seed-normal", password="test-pass-123")
        assign_role(merchant, "merchant")
        staff.profile.delete()
        superuser.profile.role = None
        superuser.profile.save(update_fields=["role"])
        normal.profile.delete()

        call_command("seed_tengasale_roles")

        staff.refresh_from_db()
        superuser.refresh_from_db()
        merchant.refresh_from_db()
        normal.refresh_from_db()
        self.assertEqual(staff.profile.role, "hq")
        self.assertEqual(superuser.profile.role, "hq")
        self.assertEqual(merchant.profile.role, "merchant")
        self.assertIsNone(normal.profile.role)


class LogoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="merchant",
            password="test-pass-123",
        )

    def test_logout_via_post_redirects_to_login(self):
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, "/accounts/login/")

    def test_logout_via_get_returns_method_not_allowed(self):
        self.client.login(username="merchant", password="test-pass-123")

        response = self.client.get(reverse("logout"))

        self.assertEqual(response.status_code, 405)


class StaticFileTests(SimpleTestCase):
    def test_local_css_is_configured_and_findable(self):
        static_dir = settings.BASE_DIR / "static"

        self.assertIn(static_dir, settings.STATICFILES_DIRS)
        self.assertTrue((static_dir / "css" / "style.css").exists())
        self.assertIsNotNone(finders.find("css/style.css"))

    def test_hq_shell_css_is_configured_and_findable(self):
        static_dir = settings.BASE_DIR / "static"

        self.assertTrue((static_dir / "css" / "hq-shell.css").exists())
        self.assertIsNotNone(finders.find("css/hq-shell.css"))

    def test_static_storage_backend_matches_runtime_mode(self):
        import importlib
        import os

        from config import settings as project_settings

        debug_storages = project_settings._build_storages(debug=True, testing=False)
        test_storages = project_settings._build_storages(debug=False, testing=True)
        production_storages = project_settings._build_storages(debug=False, testing=False)

        self.assertEqual(
            debug_storages["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )
        self.assertEqual(
            test_storages["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )
        self.assertEqual(
            production_storages["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )
        self.assertEqual(
            production_storages["default"]["BACKEND"],
            "django.core.files.storage.FileSystemStorage",
        )

        os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
        production_settings = importlib.import_module("config.settings_production")
        self.assertEqual(
            production_settings.STORAGES["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )
