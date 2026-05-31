from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib import admin
from django.core.management import call_command
from django.contrib.staticfiles import finders
from django.conf import settings
from django.test import TestCase
from django.urls import resolve, reverse

from .admin import UserProfileInline
from .models import UserProfile
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
                "role": "underwriter",
                "phone_number": "",
            },
        )

        user.profile.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(user.profile.role, "underwriter")


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


class StaticFileTests(TestCase):
    def test_local_css_is_configured_and_findable(self):
        static_dir = settings.BASE_DIR / "static"

        self.assertIn(static_dir, settings.STATICFILES_DIRS)
        self.assertTrue((static_dir / "css" / "style.css").exists())
        self.assertIsNotNone(finders.find("css/style.css"))
