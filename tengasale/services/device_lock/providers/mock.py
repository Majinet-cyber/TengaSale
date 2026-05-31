"""
Mock device lock provider.

Simulates all lock operations locally without calling any real API.
Safe for development, testing, and investor demos.
"""

import uuid
from services.device_lock.base import DeviceLockProvider, normalised_result

PROVIDER_NAME = "mock"


def _fake_device_id(lock_profile):
    """Return existing mock ID or generate a stable one from the IMEI."""
    if lock_profile.provider_device_id:
        return lock_profile.provider_device_id
    return f"MOCK-{lock_profile.imei}-{str(uuid.uuid4())[:8].upper()}"


class MockDeviceLockProvider(DeviceLockProvider):
    """
    Mock provider for local development and demos.
    All actions succeed immediately and are never sent to a real API.
    """

    provider_name = PROVIDER_NAME

    def enroll_device(self, lock_profile):
        device_id = _fake_device_id(lock_profile)
        return normalised_result(
            success=True,
            provider=self.provider_name,
            provider_device_id=device_id,
            status="enrolled",
            message=f"[MOCK] Device enrolled successfully. ID: {device_id}",
            raw_response={"mock": True, "action": "enroll", "imei": lock_profile.imei, "device_id": device_id},
        )

    def lock_device(self, lock_profile, reason=None):
        device_id = lock_profile.provider_device_id or _fake_device_id(lock_profile)
        return normalised_result(
            success=True,
            provider=self.provider_name,
            provider_device_id=device_id,
            status="locked",
            message=f"[MOCK] Device locked. Reason: {reason or 'not specified'}",
            raw_response={"mock": True, "action": "lock", "device_id": device_id, "reason": reason},
        )

    def unlock_device(self, lock_profile, reason=None):
        device_id = lock_profile.provider_device_id or _fake_device_id(lock_profile)
        return normalised_result(
            success=True,
            provider=self.provider_name,
            provider_device_id=device_id,
            status="unlocked",
            message=f"[MOCK] Device unlocked. Reason: {reason or 'not specified'}",
            raw_response={"mock": True, "action": "unlock", "device_id": device_id, "reason": reason},
        )

    def release_device(self, lock_profile, reason=None):
        device_id = lock_profile.provider_device_id or _fake_device_id(lock_profile)
        return normalised_result(
            success=True,
            provider=self.provider_name,
            provider_device_id=device_id,
            status="released",
            message=f"[MOCK] Device permanently released from lock management.",
            raw_response={"mock": True, "action": "release", "device_id": device_id},
        )

    def get_device_status(self, lock_profile):
        device_id = lock_profile.provider_device_id or _fake_device_id(lock_profile)
        mock_status = lock_profile.lock_status if lock_profile.lock_status != "not_enrolled" else "enrolled"
        return normalised_result(
            success=True,
            provider=self.provider_name,
            provider_device_id=device_id,
            status=mock_status,
            message=f"[MOCK] Status synced: {mock_status}",
            raw_response={"mock": True, "action": "status_check", "device_id": device_id, "status": mock_status},
        )
