"""
Provider-agnostic base interface for device lock providers.

All concrete providers must inherit from DeviceLockProvider and implement
every method. Methods must return a normalised result dict (see RESULT_SCHEMA).
"""


RESULT_SCHEMA = {
    "success": False,
    "provider": "",
    "provider_device_id": None,
    "status": None,
    "message": "",
    "raw_response": {},
    "error": None,
}


def normalised_result(
    success=False,
    provider="",
    provider_device_id=None,
    status=None,
    message="",
    raw_response=None,
    error=None,
):
    """Return a clean, consistent result dict for every provider action."""
    return {
        "success": bool(success),
        "provider": provider,
        "provider_device_id": provider_device_id,
        "status": status,
        "message": message,
        "raw_response": raw_response or {},
        "error": error,
    }


class DeviceLockProvider:
    """
    Abstract base class for all device lock providers.

    Subclasses must set `provider_name` and implement every method.
    Never raise unhandled exceptions from implementations — catch them and
    return a failure result using `normalised_result(success=False, error=...)`.
    """

    provider_name = "base"

    def enroll_device(self, lock_profile):
        """
        Register the device with the provider so it can be locked later.

        Returns normalised_result dict.
        """
        raise NotImplementedError

    def lock_device(self, lock_profile, reason=None):
        """
        Send a lock command to the provider for the given profile.

        Returns normalised_result dict.
        """
        raise NotImplementedError

    def unlock_device(self, lock_profile, reason=None):
        """
        Send an unlock command to the provider for the given profile.

        Returns normalised_result dict.
        """
        raise NotImplementedError

    def release_device(self, lock_profile, reason=None):
        """
        Permanently release/unenroll the device from the provider
        (e.g. when contract is fully repaid and device ownership transfers).

        Returns normalised_result dict.
        """
        raise NotImplementedError

    def get_device_status(self, lock_profile):
        """
        Query the provider for the current device status.

        Returns normalised_result dict with `status` field populated.
        """
        raise NotImplementedError
