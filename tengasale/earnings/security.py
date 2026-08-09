from datetime import timedelta
from functools import wraps
from django.contrib.auth.hashers import check_password, make_password
from django.shortcuts import redirect
from django.utils import timezone
from accounts.models import UserProfile
from .models import EarningsSecurityEvent

SESSION_KEY = "earnings_unlocked"
MAX_ATTEMPTS = 5
COOLDOWN_MINUTES = 15

def profile_for(user):
    return UserProfile.objects.get_or_create(user=user)[0]

def audit(request, event_type):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    EarningsSecurityEvent.objects.create(user=request.user, event_type=event_type, ip_address=forwarded or request.META.get("REMOTE_ADDR") or None)

def earnings_access_allowed(request):
    profile = profile_for(request.user)
    return not profile.earnings_lock_enabled or request.session.get(SESSION_KEY) == request.user.pk

def earnings_lock_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not earnings_access_allowed(request):
            request.session["earnings_next"] = request.get_full_path()
            return redirect("earnings_unlock")
        return view(request, *args, **kwargs)
    return wrapped

def set_pin(profile, pin):
    profile.earnings_pin_hash = make_password(pin)
    profile.earnings_pin_updated_at = timezone.now()
    profile.earnings_failed_attempt_count = 0
    profile.earnings_locked_until = None

def verify_pin(profile, pin):
    now = timezone.now()
    if profile.earnings_locked_until and profile.earnings_locked_until > now:
        return False, "cooldown"
    if check_password(pin, profile.earnings_pin_hash):
        profile.earnings_failed_attempt_count = 0
        profile.earnings_locked_until = None
        profile.save(update_fields=["earnings_failed_attempt_count", "earnings_locked_until"])
        return True, "ok"
    profile.earnings_failed_attempt_count += 1
    if profile.earnings_failed_attempt_count >= MAX_ATTEMPTS:
        profile.earnings_locked_until = now + timedelta(minutes=COOLDOWN_MINUTES)
    profile.save(update_fields=["earnings_failed_attempt_count", "earnings_locked_until"])
    return False, "incorrect"
