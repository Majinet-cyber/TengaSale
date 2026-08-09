from .security import SESSION_KEY

class EarningsRelockMiddleware:
    protected_prefixes = ("/earnings/", "/sales/wallet/", "/sales/spin/")
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.user.is_authenticated and SESSION_KEY in request.session and not request.path.startswith(self.protected_prefixes):
            profile = getattr(request.user, "profile", None)
            if profile is None or profile.earnings_relock_minutes == 0:
                request.session.pop(SESSION_KEY, None)
        return self.get_response(request)
