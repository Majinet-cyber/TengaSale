"""
PayTrigger device lock provider placeholder.

Fill in the implementation when PayTrigger API credentials are available.
"""

from django.conf import settings
from services.device_lock.base import DeviceLockProvider, normalised_result

PROVIDER_NAME = "paytrigger"
_NOT_CONFIGURED_MSG = (
    "PayTrigger provider is not yet configured. "
    "Set DEVICE_LOCK_API_KEY and DEVICE_LOCK_API_BASE_URL in settings."
)


def _is_configured():
    return bool(getattr(settings, "DEVICE_LOCK_API_KEY", ""))


class PayTriggerProvider(DeviceLockProvider):
    provider_name = PROVIDER_NAME

    def _fail(self, action):
        return normalised_result(
            success=False,
            provider=self.provider_name,
            message=_NOT_CONFIGURED_MSG,
            error=f"Provider not configured: {action}",
        )

    def enroll_device(self, lock_profile):
        if not _is_configured():
            return self._fail("enroll_device")
        return self._fail("enroll_device (not implemented)")

    def lock_device(self, lock_profile, reason=None):
        if not _is_configured():
            return self._fail("lock_device")
        return self._fail("lock_device (not implemented)")

    def unlock_device(self, lock_profile, reason=None):
        if not _is_configured():
            return self._fail("unlock_device")
        return self._fail("unlock_device (not implemented)")

    def release_device(self, lock_profile, reason=None):
        if not _is_configured():
            return self._fail("release_device")
        return self._fail("release_device (not implemented)")

    def get_device_status(self, lock_profile):
        if not _is_configured():
            return self._fail("get_device_status")
        return self._fail("get_device_status (not implemented)")
