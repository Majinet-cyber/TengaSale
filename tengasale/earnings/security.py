from datetime import datetime, timedelta
from functools import wraps
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.utils import timezone

from accounts.models import UserProfile
from .models import EarningsRecoveryChallenge, EarningsSecurityEvent


SESSION_KEY = "earnings_unlocked"
OWNER_VERIFIED_KEY = "earnings_owner_verified"
MAX_ATTEMPTS = 5
UNLOCK_MAX_MINUTES = 15
WEAK_PINS = {
    "000000", "111111", "222222", "333333", "444444", "555555",
    "666666", "777777", "888888", "999999", "123456", "654321",
}


def profile_for(user):
    return UserProfile.objects.get_or_create(user=user)[0]


def audit(request, event_type):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    EarningsSecurityEvent.objects.create(
        user=request.user,
        event_type=event_type,
        ip_address=forwarded or request.META.get("REMOTE_ADDR") or None,
    )


def valid_pin(pin):
    if len(pin) != 6 or not pin.isdigit() or pin in WEAK_PINS:
        return False
    ascending = "0123456789"
    descending = ascending[::-1]
    return pin not in ascending and pin not in descending and len(set(pin)) > 2


def earnings_access_allowed(request):
    profile = profile_for(request.user)
    if not profile.earnings_access:
        return False
    if not profile.earnings_lock_enabled:
        return True
    grant = request.session.get(SESSION_KEY) or {}
    try:
        unlocked_at = datetime.fromisoformat(grant.get("at"))
        if timezone.is_naive(unlocked_at):
            unlocked_at = timezone.make_aware(unlocked_at)
    except (TypeError, ValueError):
        return False
    expiry = profile.earnings_relock_minutes or UNLOCK_MAX_MINUTES
    return (
        grant.get("user") == request.user.pk
        and grant.get("generation") == profile.earnings_security_generation
        and timezone.now() - unlocked_at <= timedelta(minutes=min(expiry, UNLOCK_MAX_MINUTES))
    )


def grant_access(request, profile):
    request.session[SESSION_KEY] = {
        "user": request.user.pk,
        "generation": profile.earnings_security_generation,
        "at": timezone.now().isoformat(),
    }


def earnings_lock_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        profile = profile_for(request.user)
        if not profile.earnings_access:
            return HttpResponseForbidden("You do not have permission to view Earnings.")
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
        # Progressive but bounded: 5, 10, 15 minutes. Never a permanent lockout.
        cooldown = min(15, 5 * (profile.earnings_failed_attempt_count - MAX_ATTEMPTS + 1))
        profile.earnings_locked_until = now + timedelta(minutes=cooldown)
    profile.save(update_fields=["earnings_failed_attempt_count", "earnings_locked_until"])
    return False, "incorrect"


def owner_phone(user):
    from merchants.models import Merchant
    merchant = Merchant.objects.filter(owner=user).order_by("-id").first()
    profile = profile_for(user)
    return (merchant.phone_number if merchant else "") or profile.earnings_recovery_phone or profile.phone_number or profile.phone


def mask_phone(phone):
    digits = "".join(character for character in str(phone or "") if character.isdigit())
    return f"+265 ••• ••• {digits[-3:]}" if len(digits) >= 3 else "Owner recovery contact"


def start_owner_challenge(request, purpose):
    from communications.services import normalize_malawi_phone, send_sms
    if purpose not in {"enable", "reset", "disable"}:
        return None
    phone = normalize_malawi_phone(owner_phone(request.user))
    if not phone or not cache.add(f"earnings-recovery:{request.user.pk}", 1, timeout=60):
        return None
    code = f"{secrets.randbelow(900000) + 100000:06d}"
    challenge = EarningsRecoveryChallenge.objects.create(
        user=request.user,
        purpose=purpose,
        code_hash=make_password(code),
        phone_mask=mask_phone(phone),
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    send_sms(phone, f"Your Tenga Earnings security code is {code}. It expires in 10 minutes. Do not share it.", purpose="otp")
    request.session["earnings_challenge_id"] = challenge.pk
    audit(request, "EARNINGS_RECOVERY_STARTED")
    return challenge


def verify_owner_challenge(request, code):
    challenge = EarningsRecoveryChallenge.objects.filter(
        pk=request.session.get("earnings_challenge_id"), user=request.user, verified_at=None,
    ).first()
    if not challenge or challenge.expires_at <= timezone.now() or challenge.attempts >= MAX_ATTEMPTS:
        return None
    challenge.attempts += 1
    if not check_password(code, challenge.code_hash):
        challenge.save(update_fields=["attempts"])
        return None
    challenge.verified_at = timezone.now()
    challenge.save(update_fields=["attempts", "verified_at"])
    request.session[OWNER_VERIFIED_KEY] = {"purpose": challenge.purpose, "at": timezone.now().isoformat()}
    audit(request, "EARNINGS_RECOVERY_COMPLETED")
    return challenge


def owner_verification_valid(request, purpose):
    state = request.session.get(OWNER_VERIFIED_KEY) or {}
    try:
        verified_at = datetime.fromisoformat(state.get("at"))
    except (TypeError, ValueError):
        return False
    if timezone.is_naive(verified_at):
        verified_at = timezone.make_aware(verified_at)
    return state.get("purpose") == purpose and timezone.now() - verified_at <= timedelta(minutes=10)
