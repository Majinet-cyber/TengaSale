from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render

from .utils import get_user_portal_role, is_hq, is_merchant, is_underwriter, role_redirect_url


class UserLoginView(LoginView):
    template_name = "accounts/login.html"

    def get_success_url(self):
        redirect_url = self.get_redirect_url()
        if redirect_url and self._redirect_matches_role(redirect_url):
            return redirect_url
        return role_redirect_url(self.request.user)

    def _redirect_matches_role(self, redirect_url):
        try:
            staff_role = getattr(self.request.user.profile.staff_role, "code", "")
        except Exception:
            staff_role = ""
        if staff_role.startswith("recommerce_"):
            return redirect_url.startswith("/recommerce/")
        if redirect_url.startswith("/admin/"):
            return self.request.user.is_staff or self.request.user.is_superuser
        if is_hq(self.request.user):
            return redirect_url.startswith("/tengasale/hq/")
        if is_underwriter(self.request.user):
            return redirect_url.startswith("/tengasale/underwriter/")
        if is_merchant(self.request.user):
            return (
                redirect_url.startswith("/tengasale/merchant/")
                or redirect_url.startswith("/applications/")
                or redirect_url.startswith("/earnings/")
                or redirect_url.startswith("/payments/")
                or redirect_url.startswith("/deals/")
                or redirect_url.startswith("/contracts/")
            )
        return False


class UserLogoutView(LogoutView):
    pass


def no_role(request):
    if get_user_portal_role(request.user):
        return redirect(role_redirect_url(request.user))
    return render(request, "accounts/no_role.html", status=403)
