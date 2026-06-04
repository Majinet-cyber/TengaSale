import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.utils import assign_role
from applications.kyc_gates import apply_manual_kyc_override, check_didit_approval_gate
from applications.models import DiditWebhookEvent, FinancingApplication
from applications.test_helpers import attach_complete_pricing
from config.didit_validation import validate_didit_production_settings
from integrations.didit import (
    DiditAPIError,
    WORKFLOW_MISMATCH_USER_MESSAGE,
    build_didit_session_payload,
    canonicalize_didit_payload,
    create_didit_session,
    didit_user_facing_message,
    extract_didit_summary,
    is_uuid_like_workflow_id,
    map_didit_status,
    validate_didit_session_prerequisites,
    verify_didit_webhook_signature_v2,
)

VALID_DIDIT_WORKFLOW_UUID = "550e8400-e29b-41d4-a716-446655440000"


class DiditServiceTests(TestCase):
    def test_map_didit_status_known_and_unknown(self):
        self.assertEqual(map_didit_status("Approved"), "approved")
        self.assertEqual(map_didit_status("In Review"), "pending_review")
        self.assertEqual(map_didit_status("Weird Status"), "manual_review")

    def test_extract_didit_summary_empty_arrays(self):
        summary = extract_didit_summary({})
        self.assertIsNone(summary["document"]["first_name"])
        self.assertIsNone(summary["liveness"]["status"])
        self.assertIsNone(summary["face_match"]["status"])
        self.assertIsNone(summary["aml"]["status"])
        self.assertIsNone(summary["ip_device"]["ip_address"])

    def test_extract_didit_summary_v3_plural_arrays(self):
        decision = {
            "id_verifications": [{
                "first_name": "Chisomo",
                "last_name": "Banda",
                "document_type": "national_id",
                "document_number": "RQXFVZC9",
                "warnings": ["glare_detected"],
            }],
            "liveness_checks": [{"status": "passed", "score": 0.98, "method": "active"}],
            "face_matches": [{"status": "passed", "score": 0.91}],
            "aml_screenings": [{"status": "clear", "total_hits": 0}],
            "ip_analyses": [{
                "status": "ok",
                "ip_address": "102.23.1.1",
                "country": "MW",
                "vpn": False,
                "risk_score": 12,
            }],
        }
        summary = extract_didit_summary(decision)
        self.assertEqual(summary["document"]["first_name"], "Chisomo")
        self.assertEqual(summary["liveness"]["status"], "passed")
        self.assertEqual(summary["face_match"]["score"], 0.91)
        self.assertEqual(summary["aml"]["total_hits"], 0)
        self.assertEqual(summary["ip_device"]["country"], "MW")

    @override_settings(DIDIT_WEBHOOK_SECRET="test-secret", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=False)
    def test_signature_verification_success(self):
        payload = {"event_id": "evt-1", "status": "Approved", "amount": 1.0}
        body = json.dumps(payload).encode("utf-8")
        canonical = canonicalize_didit_payload(payload)
        signature = hmac.new(b"test-secret", canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        ts = str(int(time.time()))
        ok, err = verify_didit_webhook_signature_v2(body, signature, ts)
        self.assertTrue(ok)
        self.assertEqual(err, "")

    @override_settings(DIDIT_WEBHOOK_SECRET="test-secret", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=False)
    def test_signature_verification_failure(self):
        payload = {"event_id": "evt-2", "status": "Approved"}
        body = json.dumps(payload).encode("utf-8")
        ok, err = verify_didit_webhook_signature_v2(body, "bad-signature", str(int(time.time())))
        self.assertFalse(ok)
        self.assertIn("Invalid", err)

    @override_settings(DIDIT_WEBHOOK_SECRET="test-secret", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=False)
    def test_stale_timestamp_rejection(self):
        payload = {"event_id": "evt-3"}
        body = json.dumps(payload).encode("utf-8")
        canonical = canonicalize_didit_payload(payload)
        signature = hmac.new(b"test-secret", canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        stale = str(int(time.time()) - 400)
        ok, err = verify_didit_webhook_signature_v2(body, signature, stale)
        self.assertFalse(ok)
        self.assertIn("timestamp", err.lower())

    @override_settings(DEBUG=True, DIDIT_WEBHOOK_SECRET="", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=True)
    def test_unsigned_webhook_allowed_when_flag_true(self):
        ok, err = verify_didit_webhook_signature_v2(b'{"event_id":"x"}', None, None)
        self.assertTrue(ok)

    @override_settings(DIDIT_WEBHOOK_SECRET="", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=False)
    def test_unsigned_webhook_rejected_when_flag_false(self):
        ok, err = verify_didit_webhook_signature_v2(b'{"event_id":"x"}', None, None)
        self.assertFalse(ok)

    @override_settings(DEBUG=False, DIDIT_WEBHOOK_SECRET="", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=True)
    def test_production_rejects_unsigned_even_if_allow_flag_true(self):
        ok, err = verify_didit_webhook_signature_v2(b'{"event_id":"x"}', None, None)
        self.assertFalse(ok)
        self.assertIn("production", err.lower())

    def test_didit_api_403_user_message(self):
        msg = didit_user_facing_message(status_code=403, detail="Forbidden")
        self.assertIn("DIDIT_API_KEY", msg)

    def test_workflow_mismatch_user_message(self):
        msg = didit_user_facing_message(
            status_code=400,
            detail="invalid workflow",
            payload={"workflow_id": ["Invalid workflow"]},
        )
        self.assertEqual(msg, WORKFLOW_MISMATCH_USER_MESSAGE)

    def test_is_uuid_like_workflow_id(self):
        self.assertTrue(is_uuid_like_workflow_id(VALID_DIDIT_WORKFLOW_UUID))
        self.assertFalse(is_uuid_like_workflow_id("wf-1"))
        self.assertFalse(is_uuid_like_workflow_id(""))

    def test_env_example_has_empty_didit_secret_placeholders(self):
        root = Path(__file__).resolve().parents[2]
        env_example = root / "tengasale" / ".env.example"
        text = env_example.read_text(encoding="utf-8")
        for key in (
            "DIDIT_API_KEY",
            "DIDIT_WEBHOOK_SECRET",
        ):
            prefix = f"{key}="
            matching = [line for line in text.splitlines() if line.startswith(prefix)]
            self.assertTrue(matching, msg=f"{key} missing from .env.example")
            self.assertEqual(matching[0], prefix, msg=f"{key} must be empty in .env.example")

    def test_docs_use_placeholder_not_literal_api_key(self):
        root = Path(__file__).resolve().parents[2]
        docs = (root / "docs" / "DIDIT_INTEGRATION.md").read_text(encoding="utf-8")
        self.assertIn("<your-production-key>", docs)
        self.assertNotRegex(docs, r"DIDIT_API_KEY=[A-Za-z0-9_]{20,}")


class DiditProductionSettingsTests(TestCase):
    def test_debug_false_unsigned_raises(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            validate_didit_production_settings(
                debug=False,
                api_key="key",
                workflow_id="wf",
                webhook_secret="secret",
                webhook_url="https://tengasale.onrender.com/api/webhooks/didit/",
                callback_url="https://tengasale.onrender.com/kyc/didit/done/",
                allow_unsigned_webhooks=True,
            )
        self.assertIn("DIDIT_ALLOW_UNSIGNED_WEBHOOKS", str(ctx.exception))

    def test_debug_false_missing_webhook_secret_raises(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            validate_didit_production_settings(
                debug=False,
                api_key="key",
                workflow_id="wf",
                webhook_secret="",
                webhook_url="https://tengasale.onrender.com/api/webhooks/didit/",
                callback_url="https://tengasale.onrender.com/kyc/didit/done/",
                allow_unsigned_webhooks=False,
            )
        self.assertIn("DIDIT_WEBHOOK_SECRET", str(ctx.exception))

    def test_debug_false_http_callback_raises(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            validate_didit_production_settings(
                debug=False,
                api_key="key",
                workflow_id="wf",
                webhook_secret="secret",
                webhook_url="https://tengasale.onrender.com/api/webhooks/didit/",
                callback_url="http://tengasale.onrender.com/kyc/didit/done/",
                allow_unsigned_webhooks=False,
            )
        self.assertIn("HTTPS", str(ctx.exception))


class DiditWebhookIntegrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="didit-merchant", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            customer_name="Jane Banda",
            national_id="RQXFVZC9",
            customer_phone="991234567",
            status="customer_details",
        )

    def _post_webhook(self, payload, signed=True):
        body = json.dumps(payload).encode("utf-8")
        headers = {}
        if signed:
            with override_settings(DEBUG=True, DIDIT_WEBHOOK_SECRET="whsec", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=False):
                canonical = canonicalize_didit_payload(payload)
                headers["HTTP_X_SIGNATURE_V2"] = hmac.new(
                    b"whsec", canonical.encode("utf-8"), hashlib.sha256
                ).hexdigest()
                headers["HTTP_X_TIMESTAMP"] = str(int(time.time()))
                return self.client.post(
                    reverse("didit_webhook"),
                    data=body,
                    content_type="application/json",
                    **headers,
                )
        with override_settings(DEBUG=True, DIDIT_WEBHOOK_SECRET="", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=True):
            return self.client.post(
                reverse("didit_webhook"),
                data=body,
                content_type="application/json",
            )

    def test_duplicate_event_id_idempotency(self):
        payload = {
            "event_id": "dup-evt-1",
            "session_id": "sess-1",
            "status": "In Progress",
            "metadata": {"application_id": str(self.app.id)},
        }
        first = self._post_webhook(payload, signed=False)
        second = self._post_webhook(payload, signed=False)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(DiditWebhookEvent.objects.filter(event_id="dup-evt-1").count(), 1)

    def test_approved_webhook_updates_kyc_status(self):
        payload = {
            "event_id": "evt-approved-1",
            "session_id": "sess-approved",
            "status": "Approved",
            "vendor_data": str(self.app.id),
            "decision": {"id_verifications": [{"first_name": "Jane"}]},
        }
        self._post_webhook(payload, signed=False)
        self.app.refresh_from_db()
        self.assertEqual(self.app.kyc_status, "approved")
        self.assertIsNotNone(self.app.didit_verified_at)

    def test_declined_webhook_updates_kyc_status(self):
        payload = {
            "event_id": "evt-declined-1",
            "status": "Declined",
            "metadata": {"application_id": str(self.app.id)},
        }
        self._post_webhook(payload, signed=False)
        self.app.refresh_from_db()
        self.assertEqual(self.app.kyc_status, "declined")
        self.assertIsNotNone(self.app.didit_declined_at)

    def test_in_review_webhook_updates_pending_review(self):
        payload = {
            "event_id": "evt-review-1",
            "status": "In Review",
            "metadata": {"application_id": str(self.app.id)},
        }
        self._post_webhook(payload, signed=False)
        self.app.refresh_from_db()
        self.assertEqual(self.app.kyc_status, "pending_review")

    @override_settings(DEBUG=False, DIDIT_WEBHOOK_SECRET="whsec", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=True)
    def test_production_webhook_endpoint_rejects_unsigned(self):
        payload = {"event_id": "prod-unsigned-1", "status": "Approved", "metadata": {"application_id": str(self.app.id)}}
        body = json.dumps(payload).encode("utf-8")
        response = self.client.post(
            reverse("didit_webhook"),
            data=body,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(DEBUG=False, DIDIT_WEBHOOK_SECRET="whsec-prod", DIDIT_ALLOW_UNSIGNED_WEBHOOKS=False)
    def test_production_valid_signed_webhook_accepted(self):
        payload = {
            "event_id": "prod-signed-1",
            "status": "Approved",
            "metadata": {"application_id": str(self.app.id)},
        }
        body = json.dumps(payload).encode("utf-8")
        canonical = canonicalize_didit_payload(payload)
        signature = hmac.new(b"whsec-prod", canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        response = self.client.post(
            reverse("didit_webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE_V2=signature,
            HTTP_X_TIMESTAMP=str(int(time.time())),
        )
        self.assertEqual(response.status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.kyc_status, "approved")


class DiditApprovalGateTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.underwriter = User.objects.create_user(username="uw-didit", password="test-pass-123")
        assign_role(self.underwriter, "underwriter")
        self.hq = User.objects.create_user(username="hq-didit", password="test-pass-123")
        assign_role(self.hq, "hq")
        self.merchant = User.objects.create_user(username="m-didit", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        self.app = attach_complete_pricing(
            FinancingApplication.objects.create(
                created_by=self.merchant,
                customer_name="Test User",
                national_id="RQXFVZC9",
                status="under_review",
                claimed_by=self.underwriter,
                submitted_at=timezone.now(),
            )
        )

    @override_settings(REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=True)
    def test_approval_blocked_when_kyc_required_and_not_approved(self):
        gate = check_didit_approval_gate(self.app, self.underwriter)
        self.assertTrue(gate["blocked"])

    @override_settings(REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=True)
    def test_approval_allowed_when_kyc_approved(self):
        self.app.kyc_status = "approved"
        self.app.save(update_fields=["kyc_status"])
        gate = check_didit_approval_gate(self.app, self.underwriter)
        self.assertFalse(gate["blocked"])

    @override_settings(REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=False)
    def test_approval_warning_when_kyc_not_required(self):
        gate = check_didit_approval_gate(self.app, self.underwriter)
        self.assertFalse(gate["blocked"])
        self.assertIn("REQUIRE_DIDIT_KYC_BEFORE_APPROVAL", gate["warning"])

    @override_settings(REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=True)
    def test_confirm_approve_blocked_without_kyc(self):
        self.client.login(username="uw-didit", password="test-pass-123")
        response = self.client.post(reverse("underwriter_confirm_approve", args=[self.app.id]))
        self.assertEqual(response.status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "under_review")

    @override_settings(REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=False)
    def test_confirm_approve_shows_warning_when_kyc_not_approved(self):
        self.client.login(username="uw-didit", password="test-pass-123")
        response = self.client.get(reverse("underwriter_confirm_approve", args=[self.app.id]))
        self.assertContains(response, "REQUIRE_DIDIT_KYC_BEFORE_APPROVAL")

    @override_settings(REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=True)
    @patch("applications.kyc_gates.is_hq", return_value=True)
    def test_hq_override_requires_reason(self, _mock_hq):
        self.client.login(username="uw-didit", password="test-pass-123")
        response = self.client.post(reverse("underwriter_confirm_approve", args=[self.app.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "override reason", status_code=200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "under_review")

    @override_settings(REQUIRE_DIDIT_KYC_BEFORE_APPROVAL=True)
    def test_manual_override_persists_audit_fields(self):
        with self.assertRaises(ValueError):
            apply_manual_kyc_override(self.app, "", self.hq)
        apply_manual_kyc_override(self.app, "Customer verified in branch.", self.hq)
        self.app.save(
            update_fields=[
                "didit_manual_override",
                "didit_manual_override_reason",
                "didit_manual_override_by",
                "didit_manual_override_at",
                "kyc_status",
                "didit_status",
                "didit_verified_at",
            ]
        )
        self.app.refresh_from_db()
        self.assertTrue(self.app.didit_manual_override)
        self.assertEqual(self.app.didit_manual_override_by, self.hq)
        self.assertIsNotNone(self.app.didit_manual_override_at)


class DiditRefreshPermissionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="m-refresh", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        self.uw = User.objects.create_user(username="uw-refresh", password="test-pass-123")
        assign_role(self.uw, "underwriter")
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            didit_session_id="sess-refresh-1",
        )

    @patch("applications.didit_views.retrieve_didit_decision")
    def test_merchant_cannot_refresh_decision(self, mock_retrieve):
        self.client.login(username="m-refresh", password="test-pass-123")
        response = self.client.post(reverse("didit_refresh_decision", args=[self.app.id]))
        self.assertEqual(response.status_code, 403)
        mock_retrieve.assert_not_called()

    @patch("applications.didit_views.retrieve_didit_decision")
    def test_underwriter_can_refresh_decision(self, mock_retrieve):
        mock_retrieve.return_value = {"status": "Approved", "id_verifications": []}
        self.client.login(username="uw-refresh", password="test-pass-123")
        response = self.client.post(reverse("didit_refresh_decision", args=[self.app.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])


class DiditSessionPayloadTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="m-payload", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            customer_name="Jane Banda",
            national_id="RQXFVZC9",
        )

    @override_settings(
        DIDIT_WORKFLOW_ID=VALID_DIDIT_WORKFLOW_UUID,
        DIDIT_CALLBACK_URL="https://example.com/kyc/didit/done/",
        DIDIT_SEND_EXPECTED_DETAILS=False,
    )
    def test_minimal_payload_excludes_expected_details_by_default(self):
        payload = build_didit_session_payload(self.app)
        self.assertNotIn("expected_details", payload)
        self.assertEqual(payload["vendor_data"], str(self.app.pk))
        self.assertIsInstance(payload["vendor_data"], str)
        self.assertEqual(payload["metadata"]["application_id"], str(self.app.pk))

    @override_settings(
        DIDIT_WORKFLOW_ID=VALID_DIDIT_WORKFLOW_UUID,
        DIDIT_CALLBACK_URL="https://example.com/kyc/didit/done/",
        DIDIT_SEND_EXPECTED_DETAILS=True,
    )
    def test_expected_details_never_sent_even_when_flag_true(self):
        payload = build_didit_session_payload(self.app)
        self.assertNotIn("expected_details", payload)
        self.assertNotIn("contact_details", payload)
        self.assertEqual(set(payload.keys()), {"workflow_id", "vendor_data", "callback", "metadata"})

    def test_summarize_didit_field_errors(self):
        from integrations.didit import summarize_didit_api_error

        summary = summarize_didit_api_error({"workflow_id": ["Invalid workflow_id."]})
        self.assertIn("workflow_id", summary)
        self.assertIn("Invalid workflow_id", summary)

    def test_format_didit_session_error_includes_credits_detail(self):
        from integrations.didit import format_didit_session_error_message

        msg = format_didit_session_error_message(
            status_code=400,
            data={"detail": "You don't have enough credits to perform this request."},
        )
        self.assertIn("credits", msg.lower())
        self.assertIn("Didit rejected", msg)

    @override_settings(DIDIT_WORKFLOW_ID="not-a-uuid", DIDIT_CALLBACK_URL="https://example.com/cb/")
    def test_validate_rejects_non_uuid_workflow(self):
        with self.assertRaises(Exception) as ctx:
            validate_didit_session_prerequisites(self.app)
        self.assertIn("UUID", str(ctx.exception))


class DiditStartSessionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.merchant = User.objects.create_user(username="m-start", password="test-pass-123")
        assign_role(self.merchant, "merchant")
        self.app = FinancingApplication.objects.create(
            created_by=self.merchant,
            customer_name="Jane Banda",
            national_id="RQXFVZC9",
            customer_phone="991234567",
        )
        self.client = Client()
        self.client.login(username="m-start", password="test-pass-123")

    @override_settings(DIDIT_WORKFLOW_ID="")
    def test_start_missing_workflow_returns_error(self):
        response = self.client.post(reverse("didit_start_kyc", args=[self.app.id]))
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("DIDIT_WORKFLOW_ID", data["error"])

    @override_settings(
        DIDIT_WORKFLOW_ID="wf-1",
        DIDIT_CALLBACK_URL="https://example.com/kyc/didit/done/",
    )
    def test_start_invalid_workflow_uuid_returns_error(self):
        response = self.client.post(reverse("didit_start_kyc", args=[self.app.id]))
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("UUID", data["error"])

    @override_settings(
        DIDIT_API_KEY="key",
        DIDIT_WORKFLOW_ID=VALID_DIDIT_WORKFLOW_UUID,
        DIDIT_CALLBACK_URL="https://example.com/kyc/didit/done/",
    )
    @patch("applications.didit_views.create_didit_session")
    def test_start_session_success(self, mock_create):
        mock_create.return_value = {
            "session_id": "sess-abc",
            "url": "https://verify.didit.me/s/abc",
            "status": "In Progress",
            "workflow_id": "wf-1",
        }
        response = self.client.post(reverse("didit_start_kyc", args=[self.app.id]))
        data = response.json()
        self.assertTrue(data["success"])
        self.app.refresh_from_db()
        self.assertEqual(self.app.kyc_provider, "didit")
        self.assertEqual(self.app.didit_session_id, "sess-abc")

    @override_settings(
        DIDIT_API_KEY="key",
        DIDIT_WORKFLOW_ID=VALID_DIDIT_WORKFLOW_UUID,
        DIDIT_CALLBACK_URL="https://example.com/kyc/didit/done/",
    )
    @patch("applications.didit_views.create_didit_session")
    def test_start_session_didit_403_returns_clean_message(self, mock_create):
        mock_create.side_effect = DiditAPIError("Forbidden", status_code=403)
        response = self.client.post(reverse("didit_start_kyc", args=[self.app.id]))
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("DIDIT_API_KEY", data["error"])

    @override_settings(
        DIDIT_API_KEY="key",
        DIDIT_WORKFLOW_ID=VALID_DIDIT_WORKFLOW_UUID,
        DIDIT_CALLBACK_URL="https://example.com/kyc/didit/done/",
    )
    @patch("applications.didit_views.create_didit_session")
    def test_start_session_didit_400_returns_body_safely(self, mock_create):
        mock_create.side_effect = DiditAPIError(
            "Invalid workflow",
            status_code=400,
            payload={"workflow_id": ["Unknown workflow"]},
            request_payload_keys=["workflow_id", "vendor_data", "callback", "metadata"],
        )
        response = self.client.post(reverse("didit_start_kyc", args=[self.app.id]))
        data = response.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["didit_status"], 400)
        self.assertEqual(data["didit_detail"], {"workflow_id": ["Unknown workflow"]})
        self.assertIn(WORKFLOW_MISMATCH_USER_MESSAGE, data["error"])
        self.assertIn("workflow_id", data["error"])
        self.assertNotIn("DIDIT_API_KEY", json.dumps(data))
        self.assertIn("workflow_id", data["request_payload_keys"])
        self.assertIn("didit_error_text", data)

    @patch("integrations.didit.requests.request")
    @override_settings(
        DIDIT_API_KEY="test-key-not-logged",
        DIDIT_WORKFLOW_ID=VALID_DIDIT_WORKFLOW_UUID,
        DIDIT_CALLBACK_URL="https://example.com/kyc/didit/done/",
    )
    def test_create_session_didit_400_captured_from_http(self, mock_request):
        mock_response = mock_request.return_value
        mock_response.status_code = 400
        mock_response.content = b'{"detail":"bad workflow"}'
        mock_response.text = '{"detail":"bad workflow"}'
        mock_response.json.return_value = {"detail": "bad workflow"}
        with self.assertRaises(DiditAPIError) as ctx:
            create_didit_session(self.app)
        exc = ctx.exception
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.payload, {"detail": "bad workflow"})
        self.assertNotIn("test-key", str(exc.payload))
