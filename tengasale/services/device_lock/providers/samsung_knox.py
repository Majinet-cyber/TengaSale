"""
Samsung Knox MDM provider placeholder.

Fill in the implementation when Knox API credentials and documentation
are available. Do not change the interface or registration in registry.py.
"""

from django.conf import settings
from services.device_lock.base import DeviceLockProvider, normalised_result

PROVIDER_NAME = "samsung_knox"
_NOT_CONFIGURED_MSG = (
    "Samsung Knox provider is not yet configured. "
    "Set KNOX_API_KEY and KNOX_API_URL in settings and implement this class."
)


def _is_configured():
    return bool(getattr(settings, "KNOX_API_KEY", ""))


class SamsungKnoxProvider(DeviceLockProvider):
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
        # TODO: Implement Knox enrollment using self._knox_client()
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
