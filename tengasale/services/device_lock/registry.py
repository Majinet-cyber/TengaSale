"""
Provider registry — maps provider names to provider instances.

Usage:
    from services.device_lock.registry import get_lock_provider
    provider = get_lock_provider("mock")
    result = provider.enroll_device(lock_profile)
"""

from django.conf import settings

_PROVIDER_CACHE = {}


def get_lock_provider(provider_name=None):
    """
    Return the appropriate DeviceLockProvider instance.

    Falls back to settings.DEVICE_LOCK_PROVIDER (default "mock") if
    provider_name is not given. Instances are cached after first creation.

    Raises ValueError for completely unknown provider names so callers
    know the registry needs updating — but all known placeholder providers
    fail gracefully with "not configured" errors rather than crashes.
    """
    if provider_name is None:
        provider_name = getattr(settings, "DEVICE_LOCK_PROVIDER", "mock")

    provider_name = provider_name.lower().strip()

    if provider_name in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[provider_name]

    provider = _build_provider(provider_name)
    _PROVIDER_CACHE[provider_name] = provider
    return provider


def _build_provider(name):
    if name == "mock":
        from services.device_lock.providers.mock import MockDeviceLockProvider
        return MockDeviceLockProvider()

    if name == "samsung_knox":
        from services.device_lock.providers.samsung_knox import SamsungKnoxProvider
        return SamsungKnoxProvider()

    if name == "trustonic":
        from services.device_lock.providers.trustonic import TrustonicProvider
        return TrustonicProvider()

    if name == "paytrigger":
        from services.device_lock.providers.paytrigger import PayTriggerProvider
        return PayTriggerProvider()

    if name == "none":
        from services.device_lock.base import DeviceLockProvider, normalised_result

        class NoOpProvider(DeviceLockProvider):
            provider_name = "none"

            def _noop(self, action):
                return normalised_result(
                    success=False,
                    provider="none",
                    message="No lock provider configured (provider=none).",
                    error="No provider",
                )

            enroll_device = lambda self, lp, **kw: self._noop("enroll")
            lock_device = lambda self, lp, **kw: self._noop("lock")
            unlock_device = lambda self, lp, **kw: self._noop("unlock")
            release_device = lambda self, lp, **kw: self._noop("release")
            get_device_status = lambda self, lp, **kw: self._noop("status")

        return NoOpProvider()

    raise ValueError(
        f"Unknown lock provider: '{name}'. "
        "Valid options: mock, samsung_knox, trustonic, paytrigger, none."
    )


def clear_provider_cache():
    """Clear the provider instance cache (useful in tests)."""
    _PROVIDER_CACHE.clear()
