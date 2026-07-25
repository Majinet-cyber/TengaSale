import errno
import hashlib
import hmac
import io
import json
import secrets
import socket
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from urllib3.exceptions import NewConnectionError, ProtocolError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib import admin
from django.db import DatabaseError
from django.contrib.staticfiles import finders
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from payments.airtel_client import AirtelClient, AirtelConfig, AirtelConfigurationError, sanitize_response_headers, extract_provider_trace_id
from payments.airtel_services import (
    AirtelCallbackService,
    AirtelCollectionService,
    AirtelTransactionEnquiryService,
    SIGNATURE_STATE_INVALID,
    SIGNATURE_STATE_MISSING,
    SIGNATURE_STATE_NOT_CHECKED,
    SIGNATURE_STATE_VERIFIED,
    classify_connection_error,
    confirmed_provider_reference,
    extract_airtel_amount,
    extract_airtel_references,
    extract_airtel_status,
    extract_airtel_subscriber,
    extract_payload_transaction_id,
    get_confirmed_enquiry_reference,
    normalize_airtel_status,
    parse_airtel_callback_body,
    store_confirmed_provider_references,
)
from payments.callback_diagnostics import extract_callback_source_ip
from payments.mobile_network import PROVIDER_AIRTEL, PROVIDER_TNM, normalize_malawi_msisdn as normalize_mobile_network
from payments.models import AirtelCallbackLog, AirtelTransaction
from payments.management.commands.airtel_preflight import validate_callback_url
from payments.management.commands.airtel_trace import HISTORICAL_INITIATION_NOTICE, build_trace
from portal.models import PaymentContract, PaymentTransaction, generate_contract_number, generate_payg_number


def make_contract(**kwargs):
    defaults = {
        "contract_number": generate_contract_number(),
        "payg_number": generate_payg_number(),
        "customer_name": "Airtel Customer",
        "customer_phone": "0991234567",
        "device_model": "Tecno Spark",
        "total_amount": Decimal("150000"),
        "deposit_required": Decimal("15000"),
        "deposit_paid": Decimal("0"),
        "amount_paid": Decimal("0"),
        "daily_price": Decimal("1000"),
        "thirty_day_price": Decimal("30000"),
        "deposit_access_days": 7,
        "status": PaymentContract.STATUS_ACTIVE,
    }
    defaults.update(kwargs)
    return PaymentContract.objects.create(**defaults)


def signed_payload(payload, key="testhash"):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {**payload, "hash": signature}


class AirtelStatusMappingTests(TestCase):
    def test_success_statuses(self):
        for value in ("TS", "SUCCESS", " SUCCESS ", "Success.", "SUCCESSFUL", "Transaction Successful"):
            self.assertEqual(normalize_airtel_status(value), AirtelTransaction.STATUS_SUCCESS)

    def test_pending_statuses(self):
        for value in ("TIP", "PENDING", "Pending.", "IN_PROGRESS"):
            self.assertEqual(normalize_airtel_status(value), AirtelTransaction.STATUS_PENDING)

    def test_expired_statuses(self):
        for value in ("TE", "EXPIRED"):
            self.assertEqual(normalize_airtel_status(value), AirtelTransaction.STATUS_EXPIRED)

    def test_failed_statuses(self):
        for value in ("FAILED", "Failed.", "TF", "DECLINED", "REJECTED"):
            self.assertEqual(normalize_airtel_status(value), AirtelTransaction.STATUS_FAILED)

    def test_unknown_status(self):
        self.assertEqual(normalize_airtel_status("SOMETHING_NEW"), AirtelTransaction.STATUS_UNKNOWN)
        self.assertEqual(normalize_airtel_status("400"), AirtelTransaction.STATUS_UNKNOWN)
        self.assertEqual(normalize_airtel_status(None), AirtelTransaction.STATUS_UNKNOWN)


class MalawiMobileNetworkTests(SimpleTestCase):
    def test_detects_airtel_numbers(self):
        normalized = normalize_mobile_network("099 123 4567")
        self.assertTrue(normalized.valid)
        self.assertEqual(normalized.provider, PROVIDER_AIRTEL)
        self.assertEqual(normalized.international, "+265991234567")

    def test_supported_airtel_formats_share_one_canonical_number(self):
        for value in ("0992304851", "992304851", "265992304851", "+265992304851", "0992 304 851", "0992-304-851", "(0992) 304 851"):
            with self.subTest(value=value):
                normalized = normalize_mobile_network(value)
                self.assertTrue(normalized.valid)
                self.assertEqual(normalized.national, "992304851")
                self.assertEqual(normalized.international, "+265992304851")
                self.assertEqual(normalized.display, "265992304851")

    def test_malformed_numbers_are_rejected(self):
        for value in ("abc0992304851", "265265992304851", "+266992304851", "099230485", "++265992304851"):
            with self.subTest(value=value):
                self.assertFalse(normalize_mobile_network(value).valid)

    def test_detects_tnm_numbers(self):
        normalized = normalize_mobile_network("+265 88 123 4567")
        self.assertTrue(normalized.valid)
        self.assertEqual(normalized.provider, PROVIDER_TNM)
        self.assertEqual(normalized.international, "+265881234567")

    def test_rejects_unknown_or_short_numbers(self):
        self.assertFalse(normalize_mobile_network("071234567").valid)
        self.assertFalse(normalize_mobile_network("099123").valid)


@override_settings(
    AIRTEL_ENV="uat",
    AIRTEL_ENVIRONMENT="staging",
    AIRTEL_PRODUCTION_ENABLED=False,
    AIRTEL_COLLECTIONS_ENABLED=True,
    AIRTEL_DRY_RUN=False,
    AIRTEL_TEST_MAX_AMOUNT="1000",
    AIRTEL_TEST_MIN_AMOUNT="100",
    AIRTEL_ALLOWED_TEST_MSISDNS="0991234567",
    AIRTEL_CALLBACK_AUTH_ENABLED=True,
    AIRTEL_CALLBACK_HASH_KEY="testhash",
    AIRTEL_PRIVATE_KEY="testhash",
    AIRTEL_CALLBACK_SECRET="",
    AIRTEL_AUTH_TOKEN="test-token",
    AIRTEL_CLIENT_ID="client-id",
    AIRTEL_CLIENT_SECRET="client-secret",
    AIRTEL_MERCHANT_CODE="merchant-code",
    AIRTEL_CALLBACK_URL="https://tengasale-api.onrender.com/api/payments/airtel/callback/",
)
class AirtelApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.contract = make_contract()

    def test_callback_url_resolves_and_get_returns_405(self):
        response = self.client.get("/api/payments/airtel/callback/")
        self.assertEqual(response.status_code, 405)

    def test_exact_public_routes_resolve_and_reverse(self):
        routes = {
            "airtel_health": "/api/payments/airtel/health/",
            "airtel_readiness": "/api/payments/airtel/readiness/",
            "airtel_callback": "/api/payments/airtel/callback/",
        }
        for name, route in routes.items():
            with self.subTest(route=route):
                self.assertEqual(reverse(name), route)
                self.assertEqual(resolve(route).url_name, name)

    def test_duplicated_api_payments_prefix_does_not_resolve(self):
        with self.assertRaises(Resolver404):
            resolve("/api/payments/api/payments/airtel/readiness/")

    def test_callback_post_is_public_and_never_redirects_to_login(self):
        response = self.client.post(
            reverse("airtel_callback"),
            data="{not-json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotEqual(response.status_code, 404)
        self.assertNotIn(response.status_code, (301, 302, 307, 308))

    def test_airtel_routes_command_lists_only_public_routes(self):
        output = io.StringIO()
        call_command("airtel_routes", stdout=output)
        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "GET  /api/payments/airtel/health/",
                "GET  /api/payments/airtel/readiness/",
                "POST /api/payments/airtel/callback/",
            ],
        )

    def test_health_endpoint_returns_safe_status(self):
        response = self.client.get("/api/payments/airtel/health/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["provider"], "airtel_money")
        self.assertEqual(data["environment"], "staging")
        self.assertTrue(data["callback_configured"])
        self.assertNotIn("testhash", response.content.decode("utf-8"))

    def test_readiness_endpoint_returns_200_when_ready(self):
        response = self.client.get("/api/payments/airtel/readiness/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["environment"], "staging")
        self.assertFalse(data["collections_enabled"] is None)
        self.assertTrue(data["callback_route_resolves"])
        self.assertTrue(data["client_id_configured"])
        self.assertNotIn("testhash", response.content.decode("utf-8"))

    @override_settings(AIRTEL_CLIENT_SECRET="", AIRTEL_PRIVATE_KEY="", AIRTEL_CALLBACK_HASH_KEY="")
    def test_readiness_endpoint_returns_503_when_incomplete(self):
        response = self.client.get("/api/payments/airtel/readiness/")
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data["status"], "not_ready")
        self.assertIn("CLIENT_SECRET", data["missing"])
        self.assertNotIn("testhash", response.content.decode("utf-8"))

    def test_callback_post_logs_raw_body(self):
        payload = signed_payload({"transaction": {"reference_id": "missing", "status_code": "TIP"}})
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        log = AirtelCallbackLog.objects.get()
        self.assertIn("reference_id", log.raw_body)
        self.assertEqual(log.parsed_body, payload)

    def test_invalid_hash_is_rejected(self):
        payload = {"transaction": {"reference_id": "missing", "status_code": "TS"}, "hash": "bad"}
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(AirtelCallbackLog.objects.get().signature_valid)
        self.assertEqual(AirtelCallbackLog.objects.get().processing_state,"AUTH_FAILED")
        self.assertEqual(AirtelCallbackLog.objects.get().signature_validation_result,"INVALID")

    def test_missing_signature_is_retained_and_never_posts(self):
        payload={"transaction":{"reference_id":"missing","status_code":"TS","amount":"100"}}
        response=self.client.post("/api/payments/airtel/callback/",data=json.dumps(payload),content_type="application/json")
        self.assertEqual(response.status_code,401)
        log=AirtelCallbackLog.objects.get();self.assertEqual(log.processing_state,"AUTH_FAILED");self.assertEqual(log.signature_validation_result,"MISSING")
        self.assertFalse(AirtelTransaction.objects.filter(repayment_posted=True).exists());self.assertEqual(PaymentTransaction.objects.count(),0)

    def test_malformed_json_returns_400(self):
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data="{not-json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_transaction_returns_400(self):
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(signed_payload({"status": "TS"})),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AirtelCallbackLog.objects.get().processing_state,"INVALID_PAYLOAD")

    def test_every_request_is_logged_before_method_or_payload_validation(self):
        response=self.client.get("/api/payments/airtel/callback/")
        self.assertEqual(response.status_code,405)
        put=self.client.put("/api/payments/airtel/callback/",data="{}",content_type="application/json")
        self.assertEqual(put.status_code,405)
        log=AirtelCallbackLog.objects.order_by("created_at").first()
        self.assertEqual(log.processing_state,"METHOD_NOT_ALLOWED")
        self.assertEqual(log.request_method,"GET")
        self.assertEqual(log.body_sha256,hashlib.sha256(b"").hexdigest())
        self.assertEqual(AirtelCallbackLog.objects.filter(processing_state="METHOD_NOT_ALLOWED").count(),2)

    def test_logging_failure_is_critical_and_stops_processing(self):
        with patch("payments.api_views.AirtelCallbackLog.objects.create",side_effect=DatabaseError("offline")), patch("payments.airtel_services.AirtelCallbackService.find_transaction_match") as match, self.assertLogs("payments.api_views",level="CRITICAL"):
            response=self.client.post("/api/payments/airtel/callback/",data="{}",content_type="application/json")
        self.assertEqual(response.status_code,503)
        match.assert_not_called()

    def test_evidence_exists_before_matching_and_financial_processing(self):
        tx=AirtelTransaction.objects.create(internal_reference="TENGAEVIDENCEORDER",environment="staging",customer_msisdn="+265991234567",amount=Decimal("100"),purpose=AirtelTransaction.PURPOSE_INSTALLMENT,status=AirtelTransaction.STATUS_PENDING,contract=self.contract)
        payload=signed_payload({"transaction":{"reference_id":tx.internal_reference,"status_code":"TS","amount":"100"}})
        original=AirtelCallbackService.find_transaction_match
        def assert_before_match(service,parsed):
            evidence=AirtelCallbackLog.objects.get()
            self.assertEqual(evidence.processing_state,"RECEIVED")
            self.assertTrue(evidence.raw_body)
            return original(service,parsed)
        def assert_before_post(service,matched):
            evidence=AirtelCallbackLog.objects.get()
            self.assertEqual(evidence.processing_state,"MATCHED")
            self.assertEqual(evidence.transaction_id,matched.pk)
        with patch.object(AirtelCallbackService,"find_transaction_match",autospec=True,side_effect=assert_before_match), patch.object(AirtelCallbackService,"apply_success",autospec=True,side_effect=assert_before_post):
            response=self.client.post("/api/payments/airtel/callback/",data=json.dumps(payload),content_type="application/json")
        self.assertEqual(response.status_code,200)

    def test_evidence_exists_before_parsing_and_authentication(self):
        payload=signed_payload({"transaction":{"reference_id":"missing","status_code":"TIP"}})
        def assert_before_parse(value):
            self.assertEqual(AirtelCallbackLog.objects.count(),1)
            self.assertEqual(AirtelCallbackLog.objects.values_list("processing_state",flat=True).get(),"RECEIVED")
            return parse_airtel_callback_body(value)
        with patch("payments.airtel_services.parse_airtel_callback_body",side_effect=assert_before_parse):
            parsed_response=self.client.post("/api/payments/airtel/callback/",data=json.dumps(payload),content_type="application/json")
        self.assertEqual(parsed_response.status_code,200)
        original_verify=AirtelCallbackService.verify_signature
        def assert_before_auth(service,raw,headers,parsed):
            self.assertEqual(AirtelCallbackLog.objects.count(),2)
            self.assertTrue(AirtelCallbackLog.objects.filter(processing_state="RECEIVED").exists())
            return original_verify(service,raw,headers,parsed)
        with patch.object(AirtelCallbackService,"verify_signature",autospec=True,side_effect=assert_before_auth):
            response=self.client.post("/api/payments/airtel/callback/",data=json.dumps(payload),content_type="application/json")
        self.assertEqual(response.status_code,200)

    def test_matching_exception_retains_processing_failed_evidence(self):
        payload=signed_payload({"transaction":{"reference_id":"missing","status_code":"TS"}})
        with patch.object(AirtelCallbackService,"find_transaction_match",side_effect=RuntimeError("matching failed")):
            response=self.client.post("/api/payments/airtel/callback/",data=json.dumps(payload),content_type="application/json")
        self.assertEqual(response.status_code,500)
        log=AirtelCallbackLog.objects.get();self.assertEqual(log.processing_state,"PROCESSING_FAILED");self.assertEqual(log.error_class,"RuntimeError")

    def test_posting_exception_retains_evidence_and_retry_posts_once(self):
        tx=AirtelTransaction.objects.create(internal_reference="TENGAPOSTFAILURE",environment="staging",customer_msisdn="+265991234567",amount=Decimal("100"),purpose=AirtelTransaction.PURPOSE_INSTALLMENT,status=AirtelTransaction.STATUS_PENDING,contract=self.contract)
        payload=signed_payload({"transaction":{"reference_id":tx.internal_reference,"status_code":"TS","amount":"100"}})
        with patch.object(AirtelCallbackService,"apply_success",side_effect=RuntimeError("posting failed")):
            first=self.client.post("/api/payments/airtel/callback/",data=json.dumps(payload),content_type="application/json")
        self.assertEqual(first.status_code,500);self.assertEqual(AirtelCallbackLog.objects.get().processing_state,"PROCESSING_FAILED")
        self.assertEqual(PaymentTransaction.objects.filter(internal_reference=tx.internal_reference).count(),0)
        second=self.client.post("/api/payments/airtel/callback/",data=json.dumps(payload),content_type="application/json")
        self.assertEqual(second.status_code,200)
        self.assertEqual(PaymentTransaction.objects.filter(internal_reference=tx.internal_reference).count(),1)

    def test_final_evidence_update_failure_is_critical_and_returns_503(self):
        original_save=AirtelCallbackLog.save
        def fail_final(instance,*args,**kwargs):
            if "response_status" in (kwargs.get("update_fields") or []): raise DatabaseError("final update unavailable")
            return original_save(instance,*args,**kwargs)
        with patch.object(AirtelCallbackLog,"save",autospec=True,side_effect=fail_final),self.assertLogs("payments.api_views",level="CRITICAL") as captured:
            response=self.client.post("/api/payments/airtel/callback/",data="{bad",content_type="application/json")
        self.assertEqual(response.status_code,503);self.assertEqual(AirtelCallbackLog.objects.count(),1)
        self.assertIn("CALLBACK_EVIDENCE_UPDATE_FAILED","\n".join(captured.output))

    def test_empty_and_wrong_content_type_callbacks_are_retained_without_matching(self):
        with patch.object(AirtelCallbackService,"find_transaction_match") as match:
            empty=self.client.generic("POST","/api/payments/airtel/callback/",data=b"",content_type="application/json")
            wrong=self.client.post("/api/payments/airtel/callback/",data="plain text",content_type="text/plain")
        self.assertEqual(empty.status_code,400);self.assertEqual(wrong.status_code,415);match.assert_not_called()
        self.assertEqual(set(AirtelCallbackLog.objects.values_list("processing_state",flat=True)),{"INVALID_PAYLOAD"})

    def test_structured_entry_log_precedes_database_insert(self):
        with patch("payments.api_views.AirtelCallbackLog.objects.create",side_effect=DatabaseError("offline")),self.assertLogs("payments.api_views",level="INFO") as captured:
            response=self.client.post("/api/payments/airtel/callback/",data="{}",content_type="application/json",HTTP_X_REQUEST_ID="entry-order")
        output="\n".join(captured.output)
        self.assertEqual(response.status_code,503);self.assertLess(output.index("CALLBACK_RECEIVED"),output.index("CALLBACK_EVIDENCE_INSERT_FAILED"));self.assertIn("entry-order",output)

    def test_raw_callback_evidence_is_immutable(self):
        self.client.post("/api/payments/airtel/callback/",data="{bad",content_type="application/json")
        log=AirtelCallbackLog.objects.get();log.raw_body="changed"
        with self.assertRaisesMessage(ValueError,"immutable"):
            log.save()

    def test_unknown_internal_transaction_is_logged_safely(self):
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(signed_payload({"transaction": {"id": "UNKNOWN-REF", "status_code": "TS"}})),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("No matching", AirtelCallbackLog.objects.get().processing_error)
        self.assertEqual(AirtelCallbackLog.objects.get().processing_state,"UNMATCHED")

    def test_collection_initiate_stores_airtel_transaction_without_token(self):
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "200"}, "transaction": {"status_code": "TIP"}}
            response = self.client.post(
                "/api/payments/airtel/collections/initiate/",
                data=json.dumps({
                    "msisdn": "0991234567",
                    "amount": 1000,
                    "purpose": "DEPOSIT",
                    "contract_id": self.contract.id,
                }),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["provider"], "airtel_money")
        tx = AirtelTransaction.objects.get(internal_reference=data["internal_reference"])
        self.assertEqual(tx.status, AirtelTransaction.STATUS_PENDING)
        self.assertEqual(tx.environment, "staging")
        self.assertEqual(tx.initiation_http_status, 200)
        self.assertEqual(tx.initiation_response, tx.raw_response)
        self.assertEqual(tx.customer_msisdn, "+265991234567")
        self.assertEqual(post.call_args.kwargs["json"]["subscriber"]["msisdn"], "991234567")
        self.assertIsNotNone(tx.payment_transaction)
        self.assertEqual(tx.payment_transaction.status, PaymentTransaction.STATUS_PENDING)

    def test_collection_initiate_success_code_still_waits_for_confirmation(self):
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "200"}, "transaction": {"status_code": "TS"}}
            response = self.client.post(
                "/api/payments/airtel/collections/initiate/",
                data=json.dumps({
                    "msisdn": "0991234567",
                    "amount": 500,
                    "purpose": "INSTALLMENT",
                    "contract_id": self.contract.id,
                }),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        tx = AirtelTransaction.objects.get(internal_reference=response.json()["internal_reference"])
        self.assertEqual(tx.status, AirtelTransaction.STATUS_PENDING)
        self.assertFalse(tx.repayment_posted)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.amount_paid, Decimal("0"))

    def test_portal_payment_submit_redirects_to_waiting_without_success(self):
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "200"}, "transaction": {"status_code": "TIP"}}
            response = self.client.post(
                f"/pay/contract/{self.contract.contract_number}/payment/",
                data={"phone": "0991234567", "amount": "500", "payment_type": "repayment"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/pay/payment/", response["Location"])
        tx = AirtelTransaction.objects.get()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_PENDING)
        self.assertIsNotNone(tx.payment_transaction)
        self.assertEqual(tx.payment_transaction.status, PaymentTransaction.STATUS_PENDING)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.amount_paid, Decimal("0"))
        self.assertEqual(self.contract.remaining_amount, Decimal("150000"))

    def test_portal_payment_blocks_tnm_until_integrated(self):
        response = self.client.post(
            f"/pay/contract/{self.contract.contract_number}/payment/",
            data={"phone": "0881234567", "amount": "500", "payment_type": "repayment"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AirtelTransaction.objects.count(), 0)
        self.assertEqual(PaymentTransaction.objects.count(), 0)

    def test_customer_status_endpoint_exposes_safe_pending_state(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260714-STATUS01",
            customer_msisdn="+265991234567",
            amount=Decimal("500"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        response = self.client.get(f"/api/payments/{tx.internal_reference}/status/?token={tx.status_token}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "pending_customer_approval")
        self.assertFalse(data["final"])
        self.assertEqual(data["masked_phone"], "+26599***4567")
        self.assertNotIn("raw_response", data)

    def test_collection_initiate_resolves_contract_query(self):
        self.contract.customer_national_id = "VB78NNRU"
        self.contract.save(update_fields=["customer_national_id"])
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "200"}, "transaction": {"status_code": "TIP"}}
            response = self.client.post(
                "/api/payments/airtel/collections/initiate/",
                data=json.dumps({
                    "msisdn": "0991234567",
                    "amount": 500,
                    "purpose": "INSTALLMENT",
                    "contract_query": "VB78NNRU",
                }),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        tx = AirtelTransaction.objects.get(internal_reference=response.json()["internal_reference"])
        self.assertEqual(tx.contract, self.contract)
        self.assertEqual(tx.payment_transaction.status, PaymentTransaction.STATUS_PENDING)

    def test_duplicate_pending_collection_is_blocked(self):
        AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260714-DUPL1234",
            customer_msisdn="+265991234567",
            amount=Decimal("500"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        response = self.client.post(
            "/api/payments/airtel/collections/initiate/",
            data=json.dumps({
                "msisdn": "0991234567",
                "amount": 500,
                "purpose": "INSTALLMENT",
                "contract_id": self.contract.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["internal_reference"], AirtelTransaction.objects.get().internal_reference)
        self.assertEqual(AirtelTransaction.objects.count(), 1)

    @override_settings(AIRTEL_COLLECTIONS_ENABLED=False)
    def test_collections_disabled_blocks_requests(self):
        response = self.client.post(
            "/api/payments/airtel/collections/initiate/",
            data=json.dumps({
                "msisdn": "0991234567",
                "amount": 1000,
                "purpose": "DEPOSIT",
                "contract_id": self.contract.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("disabled", response.json()["error"])
        self.assertEqual(AirtelTransaction.objects.count(), 0)

    @override_settings(AIRTEL_DRY_RUN=True)
    def test_dry_run_records_without_network_or_repayment(self):
        with patch("payments.airtel_client.requests.post") as post:
            response = self.client.post(
                "/api/payments/airtel/collections/initiate/",
                data=json.dumps({
                    "msisdn": "0991234567",
                    "amount": 500,
                    "purpose": "DEPOSIT",
                    "contract_id": self.contract.id,
                }),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["dry_run"])
        post.assert_not_called()
        tx = AirtelTransaction.objects.get()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_DRY_RUN)
        self.assertFalse(tx.repayment_posted)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.deposit_paid, Decimal("0"))

    def test_staging_amount_limit_blocks_large_collection(self):
        response = self.client.post(
            "/api/payments/airtel/collections/initiate/",
            data=json.dumps({"msisdn": "0991234567", "amount": 1001, "purpose": "DEPOSIT", "contract_id": self.contract.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("limited", response.json()["error"])

    def test_staging_accepts_mwk_100_and_keeps_it_pending(self):
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "200"}, "transaction": {"status_code": "TIP"}}
            response = self.client.post(
                "/api/payments/airtel/collections/initiate/",
                data=json.dumps({"msisdn": "0991234567", "amount": 100, "purpose": "INSTALLMENT", "contract_id": self.contract.id}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        tx = AirtelTransaction.objects.get()
        self.assertEqual(tx.amount, Decimal("100"))
        self.assertEqual(tx.status, AirtelTransaction.STATUS_PENDING)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.amount_paid, Decimal("0"))

    def test_staging_rejects_amounts_below_mwk_100(self):
        for amount in (99, 0, -1):
            with self.subTest(amount=amount):
                response = self.client.post(
                    "/api/payments/airtel/collections/initiate/",
                    data=json.dumps({"msisdn": "0991234567", "amount": amount, "purpose": "INSTALLMENT", "contract_id": self.contract.id}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)

    def test_confirmed_mwk_100_is_partial_and_callback_is_idempotent(self):
        self.contract.deposit_paid = self.contract.deposit_required
        self.contract.save(update_fields=["deposit_paid"])
        original_due_date = self.contract.due_date
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-PARTIAL-100",
            environment="staging",
            customer_msisdn="+265991234567",
            amount=Decimal("100"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        payload = signed_payload({"transaction": {"reference_id": tx.internal_reference, "id": "AT-PARTIAL-100", "status_code": "TS", "amount": "100"}})
        for _ in range(2):
            response = self.client.post("/api/payments/airtel/callback/", data=json.dumps(payload), content_type="application/json")
            self.assertEqual(response.status_code, 200)
        self.contract.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(self.contract.amount_paid, Decimal("100"))
        self.assertEqual(self.contract.partial_repayment_credit, Decimal("100"))
        self.assertEqual(tx.full_repayment_days_covered, 0)
        self.assertEqual(tx.partial_credit_balance, Decimal("100"))
        self.assertEqual(self.contract.due_date, original_due_date)
        self.assertEqual(PaymentTransaction.objects.filter(payment_contract=self.contract, status=PaymentTransaction.STATUS_PAID).count(), 1)

    def test_payment_form_labels_mwk_100_as_zero_day_partial_payment(self):
        self.contract.deposit_paid = self.contract.deposit_required
        self.contract.save(update_fields=["deposit_paid"])
        response = self.client.get(f"/pay/contract/{self.contract.contract_number}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'min="100"')
        self.assertContains(response, "Partial payment — 0 full repayment days covered")
        self.assertNotContains(response, "MWK 100 — 1 day paid")

    def test_payment_form_exposes_semantic_network_detection_states(self):
        response = self.client.get(f"/pay/contract/{self.contract.contract_number}/")
        self.assertEqual(response.status_code, 200)
        for expected in (
            "Airtel Money detected.",
            "TNM Mpamba is not available yet.",
            "Enter a valid Malawi mobile money number.",
            "cpd-network-hint",
            "is-airtel",
            "is-tnm",
            "is-invalid",
        ):
            self.assertContains(response, expected)
        self.assertNotContains(response, "Airtel payment successful")

    def _status_page_transaction(self, status=AirtelTransaction.STATUS_PENDING, **kwargs):
        defaults = {
            "internal_reference": f"TENGA-AIRTEL-UI-{status}",
            "environment": "staging",
            "customer_msisdn": "+265991234567",
            "amount": Decimal("100"),
            "purpose": AirtelTransaction.PURPOSE_INSTALLMENT,
            "direction": AirtelTransaction.DIRECTION_COLLECTION,
            "status": status,
            "contract": self.contract,
        }
        defaults.update(kwargs)
        return AirtelTransaction.objects.create(**defaults)

    def test_pending_status_page_matches_approved_structure(self):
        tx = self._status_page_transaction()
        response = self.client.get(f"/pay/payment/{tx.internal_reference}/?token={tx.status_token}")
        self.assertEqual(response.status_code, 200)
        for expected in (
            "Airtel Money request sent", "MWK 100", "+26599***4567",
            tx.internal_reference, self.contract.contract_number, self.contract.payg_number,
            # Premium three-stage progress labels (Request sent -> Customer action -> Payment confirmed)
            "Request sent", "Customer action", "Payment confirmed",
            "Return to contract", "Check again", "Your data is protected",
        ):
            self.assertContains(response, expected)
        self.assertContains(response, "css/payment-status.css")
        self.assertContains(response, "img/brands/airtel.svg")
        self.assertContains(response, "data-provider-id-row")
        self.assertContains(response, "data-balance-row")
        self.assertContains(response, "is-payment-pending")
        self.assertContains(response, "pay-status-live-ring")
        self.assertContains(response, "Checking Airtel securely")
        self.assertNotContains(response, "provider callback")
        self.assertNotContains(response, "transaction enquiry")
        self.assertNotContains(response, "C:\\Users\\")

    def test_dry_run_customer_page_does_not_claim_prompt_sent(self):
        tx = self._status_page_transaction(status=AirtelTransaction.STATUS_DRY_RUN)
        response = self.client.get(f"/pay/payment/{tx.internal_reference}/?token={tx.status_token}")
        self.assertContains(response, "Sending payment request")
        self.assertNotContains(response, "dry-run mode")
        self.assertNotContains(response, "test confirmation")
        self.assertNotContains(response, "prompt has been sent")

    def test_status_pages_render_final_states_accurately(self):
        cases = (
            (AirtelTransaction.STATUS_FAILED, "Payment failed"),
            (AirtelTransaction.STATUS_EXPIRED, "Payment request expired"),
            (AirtelTransaction.STATUS_REVERSED, "Payment reversed"),
        )
        for index, (status, heading) in enumerate(cases):
            tx = self._status_page_transaction(status=status, internal_reference=f"TENGA-AIRTEL-UI-FINAL-{index}")
            response = self.client.get(f"/pay/payment/{tx.internal_reference}/?token={tx.status_token}")
            self.assertContains(response, heading)
            self.assertContains(response, "is-payment-failed")
            self.assertNotContains(response, 'class="pay-status-page is-payment-pending"')
            self.assertNotContains(response, "Check your phone")

    def test_confirmed_status_page_renders_success(self):
        tx = self._status_page_transaction(
            status=AirtelTransaction.STATUS_SUCCESS,
            processed_success_at=timezone.now(),
            completed_at=timezone.now(),
            airtel_money_id="AM-UI-100",
        )
        response = self.client.get(f"/pay/payment/{tx.internal_reference}/?token={tx.status_token}")
        self.assertContains(response, "Payment confirmed")
        self.assertContains(response, "AM-UI-100")
        self.assertContains(response, "is-payment-success")
        self.assertNotContains(response, 'class="pay-status-page is-payment-pending"')

    def test_invalid_agent_business_response_is_failed_not_pin_pending(self):
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "400", "message": "Invalid agent code"}}
            response = self.client.post(
                "/api/payments/airtel/collections/initiate/",
                data=json.dumps({"msisdn": "0991234567", "amount": 100, "purpose": "INSTALLMENT", "contract_id": self.contract.id}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        tx = AirtelTransaction.objects.get()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_FAILED)
        page = self.client.get(f"/pay/payment/{tx.internal_reference}/?token={tx.status_token}")
        self.assertContains(page, "Payment failed")
        self.assertNotContains(page, "Check your phone")
        self.assertNotContains(page, "Invalid agent code")

    def test_polling_script_only_calls_internal_status_endpoint_and_is_bounded(self):
        tx = self._status_page_transaction()
        response = self.client.get(f"/pay/payment/{tx.internal_reference}/?token={tx.status_token}")
        content = response.content.decode("utf-8")
        self.assertIn(f"/api/payments/{tx.internal_reference}/status/", content)
        self.assertIn("maxAttempts=8", content)
        self.assertIn("delayMs=5000", content)
        self.assertIn("if(inFlight)return", content)
        self.assertIn("enquire=1", content)
        self.assertNotIn("collections/initiate", content)

    def test_payment_status_stylesheet_is_discoverable(self):
        self.assertIsNotNone(finders.find("css/payment-status.css"))
        with open(finders.find("css/payment-status.css"), encoding="utf-8") as css_file:
            stylesheet = css_file.read()
        self.assertIn("prefers-reduced-motion:reduce", stylesheet)
        self.assertIn(".pay-status-provider-logo", stylesheet)
        self.assertIn("height:18px", stylesheet)
        self.assertIn("payStatusRing", stylesheet)
        self.assertIn("is-polling-stopped", stylesheet)
        self.assertNotIn("C:\\Users\\", stylesheet)

    def test_unapproved_test_phone_number_is_blocked(self):
        response = self.client.post(
            "/api/payments/airtel/collections/initiate/",
            data=json.dumps({"msisdn": "0999999999", "amount": 500, "purpose": "DEPOSIT", "contract_id": self.contract.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("allowlisted", response.json()["error"])

    def test_successful_deposit_callback_grants_7_day_access_and_is_idempotent(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260611-ABC12345",
            customer_msisdn="+265991234567",
            amount=Decimal("15000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        payload = signed_payload({
            "transaction": {
                "reference_id": tx.internal_reference,
                "id": "ATX-123",
                "status_code": "TS",
                "message": "Paid MWK 15,000",
                "airtel_money_id": "MP210603.1234.L06941",
            }
        })
        before = timezone.now()
        for _ in range(2):
            response = self.client.post(
                "/api/payments/airtel/callback/",
                data=json.dumps(payload),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        self.contract.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_SUCCESS)
        self.assertEqual(tx.airtel_money_id, "MP210603.1234.L06941")
        self.assertIsNotNone(tx.completed_at)
        self.assertEqual(self.contract.deposit_paid, Decimal("15000"))
        self.assertEqual(self.contract.status, PaymentContract.STATUS_ACTIVE)
        self.assertEqual(self.contract.deposit_access_days, 7)
        self.assertIsNotNone(self.contract.access_expires_at)
        self.assertGreaterEqual((self.contract.access_expires_at - before).days, 6)
        self.assertEqual(PaymentTransaction.objects.filter(internal_reference=tx.internal_reference).count(), 1)
        self.assertEqual(PaymentTransaction.objects.filter(provider_reference="MP210603.1234.L06941").count(), 1)
        self.assertEqual(AirtelCallbackLog.objects.filter(duplicate=True).count(), 1)
        self.assertEqual(set(AirtelCallbackLog.objects.values_list("processing_state",flat=True)),{"PROCESSED","DUPLICATE"})
        self.assertTrue(AirtelTransaction.objects.get(pk=tx.pk).repayment_posted)
        self.assertTrue(AirtelTransaction.objects.get(pk=tx.pk).duplicate_callback)

    def test_amount_mismatch_is_rejected_without_posting_repayment(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260611-MISMATCH",
            customer_msisdn="+265991234567",
            amount=Decimal("15000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        payload = signed_payload({
            "transaction": {
                "reference_id": tx.internal_reference,
                "status_code": "TS",
                "message": "Paid MWK 5,000",
            }
        })
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.contract.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(self.contract.deposit_paid, Decimal("0"))
        self.assertEqual(PaymentTransaction.objects.count(), 0)
        self.assertIn("mismatch", tx.failure_reason.lower())

    def test_failed_deposit_does_not_activate_contract(self):
        contract = make_contract(status=PaymentContract.STATUS_OVERDUE)
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260611-FAIL0001",
            customer_msisdn="+265991234567",
            amount=Decimal("15000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            status=AirtelTransaction.STATUS_PENDING,
            contract=contract,
        )
        payload = signed_payload({"transaction": {"reference_id": tx.internal_reference, "status_code": "TF", "message": "Failed"}})
        response = self.client.post(
            "/api/payments/airtel/callback/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        contract.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(contract.deposit_paid, Decimal("0"))
        self.assertEqual(contract.status, PaymentContract.STATUS_OVERDUE)
        self.assertEqual(tx.status, AirtelTransaction.STATUS_FAILED)
        self.assertEqual(PaymentTransaction.objects.count(), 0)
        self.assertEqual(AirtelCallbackLog.objects.get().processing_state,"PROCESSED")

    @override_settings(AIRTEL_ENVIRONMENT="staging", AIRTEL_BASE_URL="https://openapiuat.airtel.mw")
    def test_staging_base_url_is_selected_from_environment(self):
        response = self.client.get("/api/payments/airtel/health/")
        self.assertEqual(response.json()["environment"], "staging")

    @override_settings(AIRTEL_ENVIRONMENT="production", AIRTEL_PRODUCTION_ENABLED=True, AIRTEL_BASE_URL="https://openapi.airtel.mw")
    def test_production_url_selected_only_when_configured(self):
        response = self.client.get("/api/payments/airtel/health/")
        self.assertEqual(response.json()["environment"], "production")

    @override_settings(AIRTEL_ENVIRONMENT="invalid")
    def test_invalid_environment_fails_safely(self):
        with self.assertRaises(AirtelConfigurationError):
            AirtelConfig.from_settings()

    @override_settings(AIRTEL_ENVIRONMENT="production", AIRTEL_PRODUCTION_ENABLED=False)
    def test_production_requires_explicit_enablement(self):
        with self.assertRaises(AirtelConfigurationError):
            AirtelConfig.from_settings().assert_production_allowed()

    @override_settings(
        AIRTEL_AUTH_TOKEN="",
        AIRTEL_CLIENT_ID="client-id",
        AIRTEL_CLIENT_SECRET="client-secret",
        AIRTEL_TOKEN_PATH="/auth/oauth2/token",
    )
    def test_access_token_is_requested_and_cached(self):
        AirtelClient._cached_access_token = ""
        AirtelClient._cached_access_token_expires_at = 0
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"access_token": "token-123", "expires_in": 3600}
        with patch("payments.airtel_client.requests.post", return_value=response) as post:
            client = AirtelClient()
            self.assertEqual(client.get_access_token(), "token-123")
            self.assertEqual(client.get_access_token(), "token-123")
        post.assert_called_once()

    @override_settings(AIRTEL_AUTH_TOKEN="", AIRTEL_CLIENT_ID="client-id", AIRTEL_CLIENT_SECRET="client-secret")
    def test_access_token_errors_are_handled(self):
        AirtelClient._cached_access_token = ""
        AirtelClient._cached_access_token_expires_at = 0
        response = Mock()
        response.status_code = 500
        response.json.return_value = {"error": "down"}
        with patch("payments.airtel_client.requests.post", return_value=response):
            with self.assertRaises(RuntimeError):
                AirtelClient().get_access_token()

    def test_simulation_requires_authorization(self):
        response = self.client.post(
            "/api/payments/airtel/test/simulate-callback/",
            data=json.dumps(signed_payload({"transaction": {"id": "UNKNOWN-REF", "status_code": "TS"}})),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(AIRTEL_SIMULATION_SECRET="sim-secret")
    def test_simulation_uses_callback_service_when_authorized(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260611-SIM00001",
            customer_msisdn="+265991234567",
            amount=Decimal("1000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        payload = signed_payload({"transaction": {"reference_id": tx.internal_reference, "status_code": "TS", "amount": "1000"}})
        response = self.client.post(
            "/api/payments/airtel/test/simulate-callback/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_AIRTEL_TEST_SECRET="sim-secret",
        )
        self.assertEqual(response.status_code, 200)
        tx.refresh_from_db()
        self.assertTrue(tx.repayment_posted)

    @override_settings(AIRTEL_ENVIRONMENT="production", AIRTEL_PRODUCTION_ENABLED=True, AIRTEL_SIMULATION_SECRET="sim-secret")
    def test_simulation_disabled_in_production(self):
        response = self.client.post(
            "/api/payments/airtel/test/simulate-callback/",
            data=json.dumps(signed_payload({"transaction": {"id": "UNKNOWN-REF", "status_code": "TS"}})),
            content_type="application/json",
            HTTP_X_AIRTEL_TEST_SECRET="sim-secret",
        )
        self.assertEqual(response.status_code, 403)

    def test_transaction_enquiry_success_updates_and_posts_once(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-20260611-ENQUIRY1",
            customer_msisdn="+265991234567",
            amount=Decimal("15000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
            # airtel_reference_id is only a safe enquiry candidate once it is
            # explicitly marked as confirmed provider-owned (see
            # get_confirmed_enquiry_reference) — it receives no looser gating
            # than airtel_money_id/airtel_transaction_id/provider_reference.
            airtel_reference_id="ENQ123",
            provider_id_confirmed=True,
            initiation_response={"body":{"status":{"message":"Accepted"}},"http_status":200},
            initiation_http_status=200,
        )
        fake_client = SimpleNamespace(
            is_configured_for_api_calls=True,
            config=SimpleNamespace(enquiry_path_template="/standard/v1/payments/{reference}"),
            get=lambda path: {
                "ok": True,
                "http_status": 200,
                "body": {"transaction": {"reference_id": "ENQ123", "id": "ENQ-123", "status_code": "TS"}},
            },
        )
        AirtelTransactionEnquiryService(client=fake_client).enquire(tx.internal_reference)
        AirtelTransactionEnquiryService(client=fake_client).enquire(tx.internal_reference)
        tx.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_SUCCESS)
        self.assertEqual(self.contract.deposit_paid, Decimal("15000"))
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertEqual(tx.initiation_response["body"]["status"]["message"],"Accepted")
        self.assertEqual(tx.last_enquiry_http_status,200)
        self.assertEqual(tx.enquiry_logs.count(),2)

    def test_historical_trace_does_not_mislabel_enquiry_as_initiation(self):
        tx=self._status_page_transaction()
        tx.raw_response={"message":"Transaction Not Found"};tx.last_enquiry_response={"message":"Transaction Not Found"};tx.save(update_fields=["raw_response","last_enquiry_response"])
        trace=build_trace(tx)
        self.assertTrue(trace["initiation_response"]["unavailable"])
        self.assertIn(HISTORICAL_INITIATION_NOTICE,trace["warnings"])
        self.assertEqual(trace["last_enquiry"]["response"],{"message":"Transaction Not Found"})

    @override_settings(
        AIRTEL_CALLBACK_URL="https://tengasale.onrender.com/api/payments/airtel/callback/",
        AIRTEL_CALLBACK_AUTH_ENABLED=False,
        AIRTEL_CALLBACK_HASH_KEY="configured-but-disabled",
    )
    def test_preflight_passes_when_uat_config_is_complete(self):
        self.assertFalse(AirtelConfig.from_settings().callback_auth_enabled)
        output = io.StringIO()
        call_command("airtel_preflight", stdout=output)
        text = output.getvalue()
        self.assertIn("PASS", text)
        self.assertIn("Airtel preflight passed", text)
        self.assertIn("WARNING: Airtel callback authentication is explicitly disabled for UAT; callbacks are not cryptographically verified.", text)
        self.assertNotIn("FAIL: Callback authentication is enabled.", text)
        self.assertNotIn("testhash", text)

    def test_callback_url_validation_uses_runtime_host_and_normalizes_slash(self):
        for value in (
            "https://tengasale.onrender.com/api/payments/airtel/callback/",
            " https://tengasale.onrender.com/api/payments/airtel/callback ",
        ):
            ok, host, normalized = validate_callback_url(value)
            self.assertTrue(ok)
            self.assertEqual(host, "tengasale.onrender.com")
            self.assertEqual(normalized, "https://tengasale.onrender.com/api/payments/airtel/callback/")

    def test_callback_url_validation_rejects_unsafe_or_wrong_urls(self):
        for value in ("", "http://tengasale.onrender.com/api/payments/airtel/callback/", "https:///api/payments/airtel/callback/", "https://tengasale.onrender.com/wrong/", "https://localhost/api/payments/airtel/callback/"):
            with self.subTest(value=value):
                self.assertFalse(validate_callback_url(value)[0])

    def test_strict_callback_host_rejects_old_hostname(self):
        ok, _, detail = validate_callback_url(
            "https://tengasale-api.onrender.com/api/payments/airtel/callback/",
            expected_host="tengasale.onrender.com",
        )
        self.assertFalse(ok)
        self.assertIn("host must be tengasale.onrender.com", detail)

    @override_settings(
        AIRTEL_ENVIRONMENT="production",
        AIRTEL_PRODUCTION_ENABLED=True,
        AIRTEL_BASE_URL="https://openapi.airtel.mw",
        AIRTEL_CALLBACK_URL="https://tengasale.onrender.com/api/payments/airtel/callback/",
        AIRTEL_CALLBACK_AUTH_ENABLED=False,
    )
    def test_preflight_fails_when_production_callback_auth_is_disabled(self):
        output = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("airtel_preflight", stdout=output)
        self.assertIn("FAIL: Callback authentication is required in production.", output.getvalue())

    @override_settings(AIRTEL_CLIENT_ID="")
    def test_preflight_fails_when_required_config_missing(self):
        with self.assertRaises(CommandError):
            call_command("airtel_preflight", stdout=io.StringIO())

    def test_status_endpoint_requires_non_guessable_token(self):
        tx = self._status_page_transaction()
        self.assertEqual(self.client.get(f"/api/payments/{tx.internal_reference}/status/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/payments/{tx.internal_reference}/status/?token={tx.status_token}").status_code, 200)

    def test_callback_evidence_records_unsupported_method_and_response(self):
        response = self.client.get("/api/payments/airtel/callback/", HTTP_X_REQUEST_ID="render-123")
        self.assertEqual(response.status_code, 405)
        log = AirtelCallbackLog.objects.get()
        self.assertEqual(log.processing_state, "METHOD_NOT_ALLOWED")
        self.assertEqual(log.response_status, 405)
        self.assertEqual(log.request_id, "render-123")
        self.assertEqual(len(log.body_sha256), 64)

    def test_callback_health_never_alters_payments(self):
        response = self.client.get("/api/payments/airtel/callback-health/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["request_id"])
        self.assertEqual(AirtelTransaction.objects.count(), 0)

    def test_repeated_idempotency_key_returns_one_transaction(self):
        payload={"msisdn":"0991234567","amount":500,"purpose":"INSTALLMENT","contract_id":self.contract.id,"idempotency_key":"browser-attempt-1"}
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code=200;post.return_value.json.return_value={"status":{"code":"200"},"transaction":{"status_code":"TIP"}}
            first=self.client.post("/api/payments/airtel/collections/initiate/",data=json.dumps(payload),content_type="application/json")
            second=self.client.post("/api/payments/airtel/collections/initiate/",data=json.dumps(payload),content_type="application/json")
        self.assertEqual(first.json()["internal_reference"],second.json()["internal_reference"])
        self.assertEqual(AirtelTransaction.objects.count(),1)
        self.assertEqual(post.call_count,1)

    def test_callback_10_remains_investigation_only(self):
        payload={"transaction":{"status_code":"TS","code":"DP00800001001","airtel_money_id":"BP260720.0957.360303","id":"1784534223","message":"Your transaction has been successfully processed with MWK 111.00 from 992304851 : Airtel Money Test"}}
        self.assertEqual(extract_airtel_status(payload),AirtelTransaction.STATUS_SUCCESS)
        self.assertEqual(extract_airtel_amount(payload),Decimal("111.00"))
        self.assertEqual(extract_airtel_subscriber(payload),"992304851")
        self.assertEqual(extract_airtel_references(payload)["airtel_money_id"],"BP260720.0957.360303")
        self.assertEqual(extract_payload_transaction_id(payload),"1784534223")
        self.contract.deposit_paid=self.contract.deposit_required;self.contract.save(update_fields=["deposit_paid"])
        second=make_contract(customer_phone="+265992304851")
        for index,contract in enumerate((self.contract,second)):
            AirtelTransaction.objects.create(internal_reference=f"TENGACANDIDATE{index}",environment="staging",customer_msisdn="+265992304851",amount=Decimal("100"),purpose=AirtelTransaction.PURPOSE_INSTALLMENT,status=AirtelTransaction.STATUS_PENDING,contract=contract)
        response=self.client.post("/api/payments/airtel/callback/",data=json.dumps(signed_payload(payload)),content_type="application/json",HTTP_CF_CONNECTING_IP="41.78.57.4",HTTP_X_FORWARDED_FOR="172.26.131.47, 41.78.57.4")
        self.assertEqual(response.status_code,200)
        log=AirtelCallbackLog.objects.get()
        self.assertIsNone(log.transaction_id)
        self.assertEqual(log.processing_state,"AMBIGUOUS")
        self.assertEqual(log.source_ip,"41.78.57.4")
        self.assertEqual(log.extracted_status,AirtelTransaction.STATUS_SUCCESS)
        self.assertEqual(log.extracted_amount,Decimal("111.00"))
        self.assertEqual(log.extracted_subscriber,"992304851")
        self.assertEqual(log.extracted_airtel_money_id,"BP260720.0957.360303")
        self.assertEqual(log.provider_transaction_id,"1784534223")
        self.assertFalse(any(value.startswith("TENGA") for value in log.candidate_identifiers))
        self.assertEqual(len(log.candidate_suggestions),2)
        self.contract.refresh_from_db();second.refresh_from_db()
        self.assertEqual(self.contract.amount_paid,Decimal("0"));self.assertEqual(second.amount_paid,Decimal("0"))
        self.assertFalse(AirtelTransaction.objects.filter(repayment_posted=True).exists())
        output=io.StringIO();call_command("airtel_callbacks",since_minutes=30,latest=True,stdout=output)
        self.assertIn("BP260720.0957.360303",output.getvalue());self.assertIn("1784534223",output.getvalue());self.assertIn("*****4851",output.getvalue())
        search_fields=admin.site._registry[AirtelCallbackLog].search_fields
        self.assertIn("extracted_airtel_money_id",search_fields);self.assertIn("provider_transaction_id",search_fields);self.assertIn("extracted_subscriber",search_fields)

    def test_proxy_source_ip_prefers_proven_public_headers(self):
        self.assertEqual(extract_callback_source_ip({"X-Forwarded-For":"172.26.131.47, 41.78.57.4"}),"41.78.57.4")
        self.assertEqual(extract_callback_source_ip({"X-Forwarded-For":"172.26.131.47, 41.78.57.4","True-Client-IP":"41.78.57.5","CF-Connecting-IP":"41.78.57.4"}),"41.78.57.4")


@override_settings(
    AIRTEL_ENV="uat",
    AIRTEL_ENVIRONMENT="staging",
    AIRTEL_PRODUCTION_ENABLED=False,
    AIRTEL_COLLECTIONS_ENABLED=True,
    AIRTEL_DRY_RUN=False,
    AIRTEL_TEST_MAX_AMOUNT="20000",
    AIRTEL_TEST_MIN_AMOUNT="100",
    AIRTEL_ALLOWED_TEST_MSISDNS="0991234567",
    AIRTEL_CALLBACK_AUTH_ENABLED=True,
    AIRTEL_CALLBACK_HASH_KEY="testhash",
    AIRTEL_PRIVATE_KEY="testhash",
    AIRTEL_CALLBACK_SECRET="",
    AIRTEL_AUTH_TOKEN="test-token",
    AIRTEL_CLIENT_ID="client-id",
    AIRTEL_CLIENT_SECRET="client-secret",
    AIRTEL_MERCHANT_CODE="merchant-code",
    AIRTEL_CALLBACK_URL="https://tengasale-api.onrender.com/api/payments/airtel/callback/",
    AIRTEL_PENDING_EXPIRY_MINUTES=15,
)
class AirtelReliabilityTests(TestCase):
    """Covers the 12 critical-blocker fixes: ambiguous network outcomes, retry/
    supersession chains, signature tri-state semantics, no-contract callback
    handling, expiry cascades, confirmed provider references, and header
    sanitization. No test in this class performs a real network call."""

    def setUp(self):
        self.client = Client()
        self.contract = make_contract()

    def _initiate(self, **overrides):
        payload = {"msisdn": "0991234567", "amount": 1000, "purpose": "DEPOSIT", "contract_id": self.contract.id}
        payload.update(overrides)
        return self.client.post(
            "/api/payments/airtel/collections/initiate/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    # --- 1 & 2: ambiguous network outcomes must not become definite FAILED ---

    def test_timeout_during_initiation_is_ambiguous_and_portal_matches(self):
        with patch("payments.airtel_client.requests.post", side_effect=requests.Timeout("timed out")):
            response = self._initiate()
        self.assertEqual(response.status_code, 200)
        tx = AirtelTransaction.objects.get()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_UNKNOWN)
        self.assertTrue(tx.reconciliation_required)
        self.assertEqual(tx.payment_transaction.status, PaymentTransaction.STATUS_PROCESSING)

    def test_ambiguous_connection_error_is_not_treated_as_definite_failure(self):
        exc = requests.ConnectionError(
            "Connection aborted.: RemoteDisconnected('Remote end closed connection without response')"
        )
        with patch("payments.airtel_client.requests.post", side_effect=exc):
            response = self._initiate()
        self.assertEqual(response.status_code, 200)
        tx = AirtelTransaction.objects.get()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_UNKNOWN)
        self.assertTrue(tx.reconciliation_required)
        self.assertEqual(tx.payment_transaction.status, PaymentTransaction.STATUS_PROCESSING)
        self.assertFalse(classify_connection_error(exc))

    def test_presend_connection_error_is_a_definite_failure(self):
        exc = requests.ConnectionError(
            "HTTPSConnectionPool(host='openapiuat.airtel.mw', port=443): Max retries exceeded: "
            "Failed to establish a new connection: [Errno 111] Connection refused"
        )
        with patch("payments.airtel_client.requests.post", side_effect=exc):
            response = self._initiate()
        self.assertEqual(response.status_code, 200)
        tx = AirtelTransaction.objects.get()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_FAILED)
        self.assertFalse(tx.reconciliation_required)
        self.assertEqual(tx.payment_transaction.status, PaymentTransaction.STATUS_FAILED)
        self.assertTrue(classify_connection_error(exc))

    # --- 3, 6 & 7: retry/supersession relationships and cascading expiry ---
    # Retry chains are now ONLY created from an explicit customer-action identity
    # (an explicit retry_of reference tied to a specific "retry" click) — never
    # inferred merely because a prior attempt for the same contract/purpose has
    # gone stale. See test_unrelated_installment_with_same_amount_is_not_auto_linked
    # below for the corresponding negative case.

    def test_explicit_retry_of_stale_attempt_expires_it_and_links_the_replacement(self):
        old = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-STALE-0001",
            customer_msisdn="+265991234567",
            amount=Decimal("1000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        portal_tx = PaymentTransaction.objects.create(
            payment_contract=self.contract,
            provider=PaymentTransaction.PROVIDER_AIRTEL,
            payment_type=PaymentTransaction.TYPE_DEPOSIT,
            amount=Decimal("1000"),
            phone="+265991234567",
            network=PaymentTransaction.NETWORK_AIRTEL,
            status=PaymentTransaction.STATUS_PENDING,
        )
        old.payment_transaction = portal_tx
        old.save(update_fields=["payment_transaction"])
        AirtelTransaction.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(minutes=30))

        # A plain new attempt (no retry_of) must NOT auto-link to the stale one,
        # even though it shares contract/purpose/phone/amount.
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "200"}, "transaction": {"status_code": "TIP"}}
            unrelated_response = self._initiate()
        self.assertEqual(unrelated_response.status_code, 200)
        unrelated_tx = AirtelTransaction.objects.get(internal_reference=unrelated_response.json()["internal_reference"])
        self.assertIsNone(unrelated_tx.retry_of_id)
        old.refresh_from_db()
        self.assertEqual(old.status, AirtelTransaction.STATUS_PENDING)  # still untouched, not auto-expired

        # Only an explicit retry_of reference (the customer clicking "Retry" on
        # this specific attempt) creates the link and expires the original.
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "200"}, "transaction": {"status_code": "TIP"}}
            response = self._initiate(retry_of=old.internal_reference)
        self.assertEqual(response.status_code, 200)

        new_ref = response.json()["internal_reference"]
        new_tx = AirtelTransaction.objects.get(internal_reference=new_ref)
        old.refresh_from_db()
        portal_tx.refresh_from_db()

        self.assertNotEqual(new_tx.pk, old.pk)
        self.assertNotEqual(new_tx.internal_reference, old.internal_reference)
        self.assertEqual(old.status, AirtelTransaction.STATUS_EXPIRED)
        self.assertIsNotNone(old.expired_at)
        self.assertIsNotNone(old.superseded_at)
        self.assertEqual(old.superseded_by_id, new_tx.pk)
        self.assertEqual(new_tx.retry_of_id, old.pk)
        # The linked portal transaction must not be left PENDING after expiry.
        self.assertEqual(portal_tx.status, PaymentTransaction.STATUS_EXPIRED)

    def test_unrelated_installment_with_same_amount_is_not_auto_linked(self):
        """Two legitimate equal-amount installments for the same contract/purpose/
        phone must never end up in the same retry chain just because they match
        on those attributes — only an explicit retry_of reference may link them.
        This covers both a completed prior attempt and a stale (never-confirmed,
        never explicitly retried) prior attempt."""
        completed = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-INSTALLMENT-DONE",
            customer_msisdn="+265991234567",
            amount=Decimal("1000"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_SUCCESS,
            processed_success_at=timezone.now() - timedelta(days=2),
            repayment_posted=True,
            contract=self.contract,
        )
        stale = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-INSTALLMENT-STALE",
            customer_msisdn="+265991234567",
            amount=Decimal("1000"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        AirtelTransaction.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(minutes=30))

        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "200"}, "transaction": {"status_code": "TIP"}}
            response = self._initiate(amount=1000, purpose="INSTALLMENT")  # same contract, purpose, phone, and amount
        self.assertEqual(response.status_code, 200)
        new_installment = AirtelTransaction.objects.get(internal_reference=response.json()["internal_reference"])

        self.assertNotIn(new_installment.pk, {completed.pk, stale.pk})
        self.assertIsNone(new_installment.retry_of_id)
        completed.refresh_from_db()
        stale.refresh_from_db()
        self.assertIsNone(completed.superseded_by_id)
        self.assertEqual(completed.status, AirtelTransaction.STATUS_SUCCESS)  # untouched
        self.assertIsNone(stale.superseded_by_id)
        self.assertEqual(stale.status, AirtelTransaction.STATUS_PENDING)  # untouched, never auto-expired
        self.assertEqual(
            AirtelTransaction.objects.filter(purpose=AirtelTransaction.PURPOSE_INSTALLMENT, contract=self.contract).count(),
            3,
        )

    def test_retry_of_unknown_reference_is_rejected(self):
        response = self._initiate(retry_of="TENGA-AIRTEL-DOES-NOT-EXIST")
        self.assertEqual(response.status_code, 400)
        self.assertIn("could not be found", response.json()["error"])
        self.assertEqual(AirtelTransaction.objects.count(), 0)

    def test_retry_of_still_fresh_attempt_is_rejected(self):
        fresh = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-FRESH-0001",
            customer_msisdn="+265991234567",
            amount=Decimal("1000"),
            purpose=AirtelTransaction.PURPOSE_DEPOSIT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        response = self._initiate(retry_of=fresh.internal_reference)
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be retried", response.json()["error"])
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, AirtelTransaction.STATUS_PENDING)
        self.assertEqual(AirtelTransaction.objects.count(), 1)

    def test_expire_for_retry_cascades_to_linked_portal_transaction_directly(self):
        portal_tx = PaymentTransaction.objects.create(
            payment_contract=self.contract,
            provider=PaymentTransaction.PROVIDER_AIRTEL,
            payment_type=PaymentTransaction.TYPE_REPAYMENT,
            amount=Decimal("500"),
            phone="+265991234567",
            network=PaymentTransaction.NETWORK_AIRTEL,
            status=PaymentTransaction.STATUS_TENGA_PROCESSING,
        )
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-EXPIRE-DIRECT",
            customer_msisdn="+265991234567",
            amount=Decimal("500"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_INITIATED,
            contract=self.contract,
            payment_transaction=portal_tx,
        )
        tx.expire_for_retry()
        tx.refresh_from_db()
        portal_tx.refresh_from_db()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_EXPIRED)
        self.assertIsNotNone(tx.expired_at)
        self.assertTrue(tx.reconciliation_required)
        self.assertEqual(portal_tx.status, PaymentTransaction.STATUS_EXPIRED)

    # --- 3: exactly-once posting across a retry chain ---

    def _make_chain_pair(self, amount=Decimal("2000")):
        old = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-CHAIN-OLD",
            customer_msisdn="+265991234567",
            amount=amount,
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_EXPIRED,
            contract=self.contract,
        )
        new = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-CHAIN-NEW",
            customer_msisdn="+265991234567",
            amount=amount,
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
            retry_of=old,
        )
        old.superseded_by = new
        old.save(update_fields=["superseded_by"])
        return old, new

    def test_late_success_for_superseded_attempt_does_not_double_credit(self):
        old, new = self._make_chain_pair()
        AirtelCallbackService().apply_success(new)
        new.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertTrue(new.repayment_posted)
        paid_after_first = self.contract.amount_paid

        # A late success for the superseded (old) attempt must not double-credit.
        AirtelCallbackService().apply_success(old)
        old.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertFalse(old.repayment_posted)
        self.assertTrue(old.potential_overpayment)
        self.assertTrue(old.reconciliation_required)
        self.assertEqual(self.contract.amount_paid, paid_after_first)

    def test_only_one_retry_chain_member_posts_regardless_of_order(self):
        old, new = self._make_chain_pair()
        # Old posts first (e.g. a delayed callback arriving after the retry).
        AirtelCallbackService().apply_success(old)
        old.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertTrue(old.repayment_posted)
        paid_after_first = self.contract.amount_paid

        AirtelCallbackService().apply_success(new)
        new.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertFalse(new.repayment_posted)
        self.assertTrue(new.potential_overpayment)
        self.assertEqual(self.contract.amount_paid, paid_after_first)

    # --- 5: no-contract DEPOSIT/INSTALLMENT success must not be PROCESSED ---

    def test_no_contract_success_callback_is_not_marked_processed(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-NOCONTRACT",
            customer_msisdn="+265991234567",
            amount=Decimal("500"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=None,
        )
        payload = signed_payload({"transaction": {"reference_id": tx.internal_reference, "status_code": "TS", "amount": "500"}})
        response = self.client.post("/api/payments/airtel/callback/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        log = AirtelCallbackLog.objects.get()
        self.assertEqual(log.processing_state, "RECONCILIATION_REQUIRED")
        tx.refresh_from_db()
        self.assertTrue(tx.reconciliation_required)
        self.assertIsNone(tx.processed_success_at)
        self.assertFalse(tx.repayment_posted)

    # --- 4: NOT_CHECKED signature semantics must be explicit and consistent ---

    @override_settings(AIRTEL_CALLBACK_AUTH_ENABLED=False)
    def test_not_checked_signature_semantics_never_present_as_verified(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-NOTCHECKED",
            customer_msisdn="+265991234567",
            amount=Decimal("500"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        payload = {"transaction": {"reference_id": tx.internal_reference, "status_code": "TS", "amount": "500"}}
        response = self.client.post("/api/payments/airtel/callback/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        log = AirtelCallbackLog.objects.get()
        self.assertEqual(log.signature_validation_result, SIGNATURE_STATE_NOT_CHECKED)
        self.assertFalse(log.signature_valid)
        tx.refresh_from_db()
        self.assertFalse(tx.callback_verified)

    def test_verify_signature_tri_state_transitions(self):
        service = AirtelCallbackService()
        body = json.dumps({"transaction": {"a": 1}}, sort_keys=True, separators=(",", ":")).encode("utf-8")

        with override_settings(AIRTEL_CALLBACK_AUTH_ENABLED=False):
            state, found = AirtelCallbackService().verify_signature(body, {})
            self.assertEqual(state, SIGNATURE_STATE_NOT_CHECKED)
            self.assertFalse(found)

        with override_settings(AIRTEL_CALLBACK_AUTH_ENABLED=True, AIRTEL_CALLBACK_HASH_KEY="testhash", AIRTEL_PRIVATE_KEY="testhash"):
            state, found = AirtelCallbackService().verify_signature(body, {})
            self.assertEqual(state, SIGNATURE_STATE_MISSING)
            self.assertFalse(found)

            state, found = AirtelCallbackService().verify_signature(body, {"X-Signature": "not-a-real-signature"})
            self.assertEqual(state, SIGNATURE_STATE_INVALID)
            self.assertTrue(found)

            digest = hmac.new(b"testhash", body, hashlib.sha256).hexdigest()
            state, found = AirtelCallbackService().verify_signature(body, {"X-Signature": digest})
            self.assertEqual(state, SIGNATURE_STATE_VERIFIED)
            self.assertTrue(found)

    # --- 9: confirmed-provider-reference helper never echoes internal_reference ---

    def test_confirmed_provider_reference_never_echoes_internal_reference(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-PROVREF",
            customer_msisdn="+265991234567",
            amount=Decimal("500"),
            purpose=AirtelTransaction.PURPOSE_TEST,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
        )
        self.assertEqual(confirmed_provider_reference(tx), "")
        tx.provider_reference = tx.internal_reference
        tx.provider_id_confirmed = True
        tx.save(update_fields=["provider_reference", "provider_id_confirmed"])
        self.assertEqual(confirmed_provider_reference(tx), "")
        tx.airtel_money_id = "AM-CONFIRMED-123"
        tx.save(update_fields=["airtel_money_id"])
        self.assertEqual(confirmed_provider_reference(tx), "AM-CONFIRMED-123")

    def test_store_confirmed_provider_references_ignores_merchant_echo(self):
        tx = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-ECHO",
            customer_msisdn="+265991234567",
            amount=Decimal("500"),
            purpose=AirtelTransaction.PURPOSE_TEST,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
        )
        changed = store_confirmed_provider_references(tx, {"transaction": {"provider_reference": tx.internal_reference}})
        self.assertEqual(changed, [])
        self.assertFalse(tx.provider_id_confirmed)
        self.assertFalse(tx.provider_reference)

    # --- 8: response header sanitization ---

    def test_response_header_sanitization_excludes_secrets(self):
        headers = {
            "Authorization": "Bearer secret-token",
            "Set-Cookie": "session=abc123",
            "X-Request-Id": "req-123",
            "X-Correlation-Id": "corr-456",
            "X-Api-Key": "super-secret-key",
            "Content-Type": "application/json",
        }
        safe = sanitize_response_headers(headers)
        self.assertNotIn("Authorization", safe)
        self.assertNotIn("Set-Cookie", safe)
        self.assertNotIn("X-Api-Key", safe)
        self.assertNotIn("Content-Type", safe)
        self.assertEqual(safe.get("X-Request-Id"), "req-123")
        self.assertEqual(safe.get("X-Correlation-Id"), "corr-456")

    def test_extract_provider_trace_id_uses_only_safe_headers(self):
        self.assertEqual(extract_provider_trace_id({"X-Request-Id": "req-999", "Authorization": "secret"}), "req-999")
        self.assertEqual(extract_provider_trace_id({"Authorization": "secret"}), "")

    def test_decode_response_attaches_sanitized_headers(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"ok": True}
        response.headers = {"Authorization": "Bearer x", "X-Request-Id": "abc-123"}
        decoded = AirtelClient._decode_response(response)
        self.assertNotIn("Authorization", decoded["sanitized_headers"])
        self.assertEqual(decoded["sanitized_headers"].get("X-Request-Id"), "abc-123")
        self.assertEqual(decoded["provider_trace_id"], "abc-123")


class ConfirmedEnquiryReferenceSafetyTests(TestCase):
    """get_confirmed_enquiry_reference() is the single central helper every
    enquiry candidate must pass through. A value is only ever safe to use when
    it (1) came from an explicitly provider-owned response/callback field, (2)
    is marked confirmed, and (3) is not equal to TengaSale's own internal
    reference. airtel_reference_id receives no looser treatment than the other
    three provider-owned fields."""

    def _tx(self, **kwargs):
        defaults = dict(
            internal_reference="TENGA-AIRTEL-ENQREF-" + secrets.token_hex(4).upper(),
            customer_msisdn="+265991234567",
            amount=Decimal("500"),
            purpose=AirtelTransaction.PURPOSE_TEST,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
        )
        defaults.update(kwargs)
        return AirtelTransaction.objects.create(**defaults)

    def test_confirmed_airtel_money_id_is_usable_for_enquiry(self):
        tx = self._tx(airtel_money_id="MP210603.1234.L06941", provider_id_confirmed=True)
        self.assertEqual(get_confirmed_enquiry_reference(tx), "MP210603.1234.L06941")

    def test_confirmed_provider_transaction_id_is_usable_for_enquiry(self):
        tx = self._tx(airtel_transaction_id="ATX-CONFIRMED-1", provider_id_confirmed=True)
        self.assertEqual(get_confirmed_enquiry_reference(tx), "ATX-CONFIRMED-1")

    def test_confirmed_airtel_reference_id_is_usable_for_enquiry(self):
        # airtel_reference_id receives no looser gating than the other three
        # provider-owned fields — it is only usable when explicitly confirmed.
        tx = self._tx(airtel_reference_id="REF-CONFIRMED-1", provider_id_confirmed=True)
        self.assertEqual(get_confirmed_enquiry_reference(tx), "REF-CONFIRMED-1")

    def test_unconfirmed_reference_id_is_never_usable_for_enquiry(self):
        tx = self._tx(airtel_reference_id="REF-UNCONFIRMED", provider_id_confirmed=False)
        self.assertEqual(get_confirmed_enquiry_reference(tx), "")

    def test_reference_equal_to_internal_reference_is_never_usable(self):
        tx = self._tx(provider_id_confirmed=True)
        tx.airtel_transaction_id = tx.internal_reference
        tx.save(update_fields=["airtel_transaction_id"])
        self.assertEqual(get_confirmed_enquiry_reference(tx), "")

    def test_legacy_merchant_echo_is_never_usable_even_if_marked_confirmed(self):
        # Simulates a legacy record where provider_id_confirmed was set True by
        # older code even though the value stored is just an echo of what
        # TengaSale itself sent at initiation (provider_reference == internal
        # reference). This must still be rejected — the safety check is on the
        # *value*, not merely the confirmed flag.
        tx = self._tx(provider_id_confirmed=True)
        tx.provider_reference = tx.internal_reference
        tx.save(update_fields=["provider_reference"])
        self.assertEqual(get_confirmed_enquiry_reference(tx), "")

    def test_confirmed_flag_alone_without_any_field_value_yields_nothing(self):
        tx = self._tx(provider_id_confirmed=True)
        self.assertEqual(get_confirmed_enquiry_reference(tx), "")

    def test_get_confirmed_enquiry_reference_is_the_same_function_used_by_the_alias(self):
        self.assertIs(confirmed_provider_reference, get_confirmed_enquiry_reference)


class ConnectionErrorClassificationTests(SimpleTestCase):
    """classify_connection_error() must default every uncertain outcome to
    ambiguous (False -> STATUS_UNKNOWN / reconciliation_required=True /
    external_processing). Only a structured, deterministic pre-send condition
    may justify a definite FAILED classification."""

    def test_dns_resolution_failure_is_a_definite_failure(self):
        exc = requests.ConnectionError(socket.gaierror(-2, "Name or service not known"))
        self.assertTrue(classify_connection_error(exc))

    def test_connection_refused_before_transmission_is_a_definite_failure(self):
        refused = OSError(errno.ECONNREFUSED, "Connection refused")
        exc = requests.ConnectionError(refused)
        self.assertTrue(classify_connection_error(exc))

    def test_connection_reset_is_ambiguous_not_a_definite_failure(self):
        reset = OSError(errno.ECONNRESET, "Connection reset by peer")
        exc = requests.ConnectionError(reset)
        self.assertFalse(classify_connection_error(exc))

    def test_remote_disconnect_is_ambiguous_not_a_definite_failure(self):
        exc = requests.ConnectionError(
            "Connection aborted.: RemoteDisconnected('Remote end closed connection without response')"
        )
        self.assertFalse(classify_connection_error(exc))

    def test_proxy_error_is_ambiguous_not_a_definite_failure(self):
        exc = requests.ConnectionError(
            "ProxyError('Cannot connect to proxy.', OSError('Tunnel connection failed'))"
        )
        self.assertFalse(classify_connection_error(exc))

    def test_tls_handshake_error_is_ambiguous_not_a_definite_failure(self):
        exc = requests.ConnectionError(
            "SSLError(SSLCertVerificationError('[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed during handshake'))"
        )
        self.assertFalse(classify_connection_error(exc))

    def test_unknown_connection_error_defaults_to_ambiguous(self):
        exc = requests.ConnectionError("An undocumented transport failure occurred")
        self.assertFalse(classify_connection_error(exc))

    def test_wrapped_urllib3_new_connection_error_is_a_definite_failure(self):
        exc = requests.ConnectionError(
            NewConnectionError(None, "Failed to establish a new connection: [Errno 111] Connection refused")
        )
        self.assertTrue(classify_connection_error(exc))

    def test_wrapped_urllib3_protocol_error_is_ambiguous_not_a_definite_failure(self):
        # A ProtocolError means a connection was already established when the
        # failure occurred — the request may have reached Airtel.
        exc = requests.ConnectionError(ProtocolError("Connection aborted."))
        self.assertFalse(classify_connection_error(exc))


@override_settings(
    AIRTEL_ENV="uat",
    AIRTEL_ENVIRONMENT="staging",
    AIRTEL_PRODUCTION_ENABLED=False,
    AIRTEL_COLLECTIONS_ENABLED=True,
    AIRTEL_DRY_RUN=False,
    AIRTEL_PENDING_EXPIRY_MINUTES=15,
    AIRTEL_PENDING_RECONCILIATION_MINUTES=10,
)
class PortalPaymentStatusBehaviourTests(TestCase):
    """Customer-facing behaviour for every new terminal/ambiguous status must
    match the required rules exactly: EXPIRED lets the customer start fresh
    (old evidence stays visible to operators); UNKNOWN/external_processing
    shows confirmation-in-progress without ever inviting a second payment or
    an unsafe immediate retry; FAILED allows retry; POTENTIAL_OVERPAYMENT /
    MANUAL_REVIEW tells the customer it is under review and never invites a
    second payment; SUCCESS shows confirmed-and-applied with the 7-day
    deposit access policy intact."""

    def setUp(self):
        self.client = Client()
        self.contract = make_contract()

    def _tx(self, **kwargs):
        defaults = dict(
            internal_reference="TENGA-AIRTEL-PORTALSTATUS-" + secrets.token_hex(4).upper(),
            customer_msisdn="+265991234567",
            amount=Decimal("100"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        defaults.update(kwargs)
        return AirtelTransaction.objects.create(**defaults)

    def _wait_page(self, tx):
        return self.client.get(f"/pay/payment/{tx.internal_reference}/?token={tx.status_token}")

    def _status_api(self, tx):
        return self.client.get(f"/api/payments/{tx.internal_reference}/status/?token={tx.status_token}")

    # --- EXPIRED ---

    def test_expired_attempt_is_not_shown_as_active_and_allows_fresh_attempt(self):
        tx = self._tx(status=AirtelTransaction.STATUS_EXPIRED, expired_at=timezone.now())
        page = self._wait_page(tx)
        self.assertContains(page, "Payment request expired")
        self.assertNotContains(page, 'class="pay-status-page is-payment-pending"')
        data = self._status_api(tx).json()
        self.assertEqual(data["status"], "expired")
        self.assertTrue(data["final"])
        self.assertTrue(data["can_retry"])  # customer may create a fresh attempt
        self.assertContains(page, "Retry this payment")
        # Old evidence remains fully visible to operators (via trace) after expiry.
        from payments.management.commands.airtel_trace import build_trace
        trace = build_trace(tx)
        self.assertEqual(trace["status"], AirtelTransaction.STATUS_EXPIRED)
        self.assertIsNotNone(trace["retry"]["expired_at"])

    def test_expired_deposit_does_not_permanently_block_the_contract_page(self):
        self.contract.deposit_required = Decimal("100")
        self.contract.save(update_fields=["deposit_required"])
        portal_tx = PaymentTransaction.objects.create(
            payment_contract=self.contract, provider=PaymentTransaction.PROVIDER_AIRTEL,
            payment_type=PaymentTransaction.TYPE_DEPOSIT, amount=Decimal("100"),
            phone="+265991234567", network=PaymentTransaction.NETWORK_AIRTEL,
            status=PaymentTransaction.STATUS_EXPIRED,
        )
        self._tx(
            purpose=AirtelTransaction.PURPOSE_DEPOSIT, amount=Decimal("100"),
            status=AirtelTransaction.STATUS_EXPIRED, expired_at=timezone.now(),
            payment_transaction=portal_tx,
        )
        response = self.client.get(f"/pay/contract/{self.contract.contract_number}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Deposit confirmation pending")

    # --- UNKNOWN / external_processing ---

    def test_unknown_status_shows_checking_in_progress_and_blocks_retry(self):
        tx = self._tx(status=AirtelTransaction.STATUS_UNKNOWN, reconciliation_required=True)
        data = self._status_api(tx).json()
        self.assertFalse(data["final"])
        self.assertFalse(data["can_retry"])  # unsafe immediate retry must be blocked
        self.assertIn("do not pay again", data["message"].lower())
        page = self._wait_page(tx)
        self.assertNotContains(page, "Retry this payment")

    def test_unknown_portal_transaction_status_is_external_processing(self):
        portal_tx = PaymentTransaction.objects.create(
            payment_contract=self.contract, provider=PaymentTransaction.PROVIDER_AIRTEL,
            payment_type=PaymentTransaction.TYPE_REPAYMENT, amount=Decimal("100"),
            phone="+265991234567", network=PaymentTransaction.NETWORK_AIRTEL,
            status=PaymentTransaction.STATUS_PROCESSING,
        )
        self.assertEqual(portal_tx.status, "external_processing")

    # --- FAILED ---

    def test_failed_status_allows_retry(self):
        tx = self._tx(status=AirtelTransaction.STATUS_FAILED)
        data = self._status_api(tx).json()
        self.assertEqual(data["status"], "failed")
        self.assertTrue(data["final"])
        self.assertTrue(data["can_retry"])
        page = self._wait_page(tx)
        self.assertContains(page, "Retry this payment")

    # --- POTENTIAL_OVERPAYMENT / MANUAL_REVIEW ---

    def test_potential_overpayment_shows_manual_review_and_blocks_retry(self):
        tx = self._tx(
            status=AirtelTransaction.STATUS_SUCCESS,
            potential_overpayment=True,
            reconciliation_required=True,
        )
        data = self._status_api(tx).json()
        self.assertEqual(data["status"], "manual_review")
        self.assertFalse(data["final"])
        self.assertFalse(data["can_retry"])
        self.assertIn("review", data["message"].lower())
        self.assertNotIn("pay again to", data["message"].lower())
        page = self._wait_page(tx)
        self.assertNotContains(page, "Retry this payment")

    # --- SUCCESS ---

    def test_success_status_shows_confirmed_and_applied_with_7_day_access(self):
        self.contract.deposit_required = Decimal("100")
        self.contract.deposit_paid = Decimal("100")
        self.contract.deposit_access_days = 7
        self.contract.access_expires_at = timezone.now() + timedelta(days=7)
        self.contract.save(update_fields=["deposit_required", "deposit_paid", "deposit_access_days", "access_expires_at"])
        portal_tx = PaymentTransaction.objects.create(
            payment_contract=self.contract, provider=PaymentTransaction.PROVIDER_AIRTEL,
            payment_type=PaymentTransaction.TYPE_DEPOSIT, amount=Decimal("100"),
            phone="+265991234567", network=PaymentTransaction.NETWORK_AIRTEL,
            status=PaymentTransaction.STATUS_PAID, paid_at=timezone.now(),
            balance_before=Decimal("100"), balance_after=Decimal("0"),
        )
        tx = self._tx(
            purpose=AirtelTransaction.PURPOSE_DEPOSIT, amount=Decimal("100"),
            status=AirtelTransaction.STATUS_SUCCESS, processed_success_at=timezone.now(),
            completed_at=timezone.now(), repayment_posted=True, payment_transaction=portal_tx,
        )
        data = self._status_api(tx).json()
        self.assertEqual(data["status"], "successful")
        self.assertTrue(data["final"])
        self.assertEqual(data["portal_transaction_status"], PaymentTransaction.STATUS_PAID)
        page = self._wait_page(tx)
        self.assertContains(page, "Payment confirmed")
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.deposit_access_days, 7)


@override_settings(
    AIRTEL_ENV="uat",
    AIRTEL_ENVIRONMENT="staging",
    AIRTEL_PRODUCTION_ENABLED=False,
    AIRTEL_COLLECTIONS_ENABLED=True,
    AIRTEL_DRY_RUN=False,
    AIRTEL_TEST_MAX_AMOUNT="1000",
    AIRTEL_TEST_MIN_AMOUNT="100",
    AIRTEL_ALLOWED_TEST_MSISDNS="0991234567",
    # "Allowed UAT mode": callback authentication is explicitly disabled, so
    # every callback's authentication state must read NOT_CHECKED — never
    # VERIFIED — even though the callback is otherwise fully processed.
    AIRTEL_CALLBACK_AUTH_ENABLED=False,
    AIRTEL_CALLBACK_HASH_KEY="testhash",
    AIRTEL_AUTH_TOKEN="test-token",
    AIRTEL_CLIENT_ID="client-id",
    AIRTEL_CLIENT_SECRET="client-secret",
    AIRTEL_MERCHANT_CODE="merchant-code",
    AIRTEL_CALLBACK_URL="https://tengasale-api.onrender.com/api/payments/airtel/callback/",
)
class AirtelEndToEndDepositAcceptanceTest(TestCase):
    """One complete mocked Airtel MWK 100 deposit lifecycle exercised entirely
    through public service/view boundaries: initiation -> first (successful)
    callback -> duplicate callback -> airtel_trace. No real network request is
    ever made — payments.airtel_client.requests.post is mocked throughout."""

    def setUp(self):
        self.client = Client()
        self.contract = make_contract(deposit_required=Decimal("100"), deposit_paid=Decimal("0"))

    def test_complete_mocked_deposit_lifecycle(self):
        # --- A: contract with an unpaid deposit ---
        self.assertEqual(self.contract.deposit_paid, Decimal("0"))

        # --- B & C: start one MWK 100 Airtel deposit; mock Airtel initiation HTTP 200 "Success." ---
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.headers = {"X-Request-Id": "trace-deposit-100"}
            post.return_value.json.return_value = {
                "status": {"code": "200", "message": "Success."},
                "data": {"transaction": {"id": "AIRTEL-INIT-100"}},
                "transaction": {"status_code": "TIP", "message": "Success."},
            }
            response = self.client.post(
                "/api/payments/airtel/collections/initiate/",
                data=json.dumps({
                    "msisdn": "0991234567",
                    "amount": 100,
                    "purpose": "DEPOSIT",
                    "contract_id": self.contract.id,
                }),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        internal_reference = body["internal_reference"]

        # --- D ---
        self.assertEqual(AirtelTransaction.objects.count(), 1)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        tx = AirtelTransaction.objects.get(internal_reference=internal_reference)
        self.assertEqual(tx.status, AirtelTransaction.STATUS_PENDING)  # awaiting final confirmation
        status_payload = self.client.get(f"/api/payments/{internal_reference}/status/?token={tx.status_token}").json()
        self.assertEqual(status_payload["status"], "pending_customer_approval")
        self.assertFalse(status_payload["final"])
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.deposit_paid, Decimal("0"))  # balance unchanged
        self.assertIsNone(self.contract.access_expires_at)  # access days not yet activated
        self.assertEqual(tx.initiation_response.get("sanitized_headers", {}).get("X-Request-Id"), "trace-deposit-100")
        self.assertEqual(tx.initiation_response.get("provider_trace_id"), "trace-deposit-100")

        # --- E: submit a successful callback using the exact internal reference ---
        callback_payload = {
            "transaction": {
                "reference_id": internal_reference,
                "id": "AIRTEL-TXN-100",
                "status_code": "TS",
                "message": "Success.",
                "airtel_money_id": "MP260725.0001.D00100",
                "amount": "100",
            }
        }
        first_callback = self.client.post(
            "/api/payments/airtel/callback/", data=json.dumps(callback_payload), content_type="application/json",
        )
        self.assertEqual(first_callback.status_code, 200)

        # --- F ---
        self.assertEqual(AirtelCallbackLog.objects.count(), 1)
        log = AirtelCallbackLog.objects.get()
        self.assertEqual(log.transaction_id, tx.pk)
        self.assertEqual(log.matched_identifier, internal_reference)
        self.assertEqual(log.matched_field, "internal_reference")
        self.assertEqual(log.signature_validation_result, SIGNATURE_STATE_NOT_CHECKED)
        self.assertNotEqual(log.signature_validation_result, SIGNATURE_STATE_VERIFIED)
        self.assertFalse(log.signature_valid)
        self.assertEqual(log.processing_state, "PROCESSED")

        tx.refresh_from_db()
        self.assertEqual(tx.status, AirtelTransaction.STATUS_SUCCESS)
        self.assertIsNotNone(tx.processed_success_at)
        self.assertTrue(tx.repayment_posted)
        portal_tx = PaymentTransaction.objects.get(pk=tx.payment_transaction_id)
        self.assertEqual(portal_tx.status, PaymentTransaction.STATUS_PAID)

        self.contract.refresh_from_db()
        self.assertEqual(self.contract.deposit_paid, Decimal("100"))  # balance changed exactly once
        self.assertEqual(self.contract.deposit_access_days, 7)  # access policy remains exactly 7 days
        self.assertIsNotNone(self.contract.access_expires_at)
        self.assertAlmostEqual(
            (self.contract.access_expires_at - timezone.now()).total_seconds(),
            timedelta(days=7).total_seconds(),
            delta=120,
        )

        portal_tx.refresh_from_db()
        self.assertEqual(portal_tx.commissionable_amount, Decimal("0"))  # deposits never earn commission
        from commissions.models import Commission
        self.assertEqual(Commission.objects.count(), 0)  # commission finalized exactly once (none due for a deposit)
        self.assertEqual(portal_tx.balance_before, Decimal("100"))
        self.assertEqual(portal_tx.balance_after, Decimal("0"))

        paid_after_first = self.contract.deposit_paid
        access_expires_after_first = self.contract.access_expires_at

        # --- G: submit the identical callback again ---
        second_callback = self.client.post(
            "/api/payments/airtel/callback/", data=json.dumps(callback_payload), content_type="application/json",
        )
        self.assertEqual(second_callback.status_code, 200)

        # --- H ---
        self.assertEqual(AirtelCallbackLog.objects.count(), 2)
        second_log = AirtelCallbackLog.objects.order_by("-created_at").first()
        self.assertTrue(second_log.duplicate)
        self.assertEqual(second_log.processing_state, "DUPLICATE")

        self.contract.refresh_from_db()
        self.assertEqual(self.contract.deposit_paid, paid_after_first)  # balance does not change again
        self.assertEqual(self.contract.access_expires_at, access_expires_after_first)  # access days do not extend again
        self.assertEqual(Commission.objects.count(), 0)  # commission does not run again

        # --- I: airtel_trace ---
        from payments.management.commands.airtel_trace import build_trace
        tx.refresh_from_db()
        trace = build_trace(tx)
        self.assertIsNotNone(trace["initiation_at"])
        self.assertTrue(trace["initiation_payload"])
        self.assertFalse(trace["initiation_response"].get("unavailable"))
        self.assertEqual(trace["initiation_response_headers"].get("X-Request-Id"), "trace-deposit-100")
        self.assertEqual(trace["provider_trace_id"], "trace-deposit-100")
        self.assertEqual(len(trace["callbacks"]), 2)
        self.assertEqual(trace["callbacks"][0]["matched_field"], "internal_reference")
        self.assertEqual(trace["callbacks"][0]["matched_identifier"], internal_reference)
        self.assertTrue(trace["callbacks"][1]["duplicate"])
        self.assertEqual(trace["authentication_state"], SIGNATURE_STATE_NOT_CHECKED)
        self.assertEqual(trace["posting"]["portal_status"], PaymentTransaction.STATUS_PAID)
        self.assertEqual(trace["posting"]["balance_before"], "100.00")
        self.assertEqual(trace["posting"]["balance_after"], "0.00")
        self.assertTrue(trace["access_policy"]["matches_expected_days"])
        self.assertEqual(trace["access_policy"]["expected_deposit_access_days"], 7)
        self.assertIsNotNone(trace["posting"]["processed_success_at"])
        self.assertIsInstance(trace["warnings"], list)

        # Also exercise the actual management command end-to-end (no network access).
        output = io.StringIO()
        call_command("airtel_trace", internal_reference, stdout=output)
        self.assertIn(internal_reference, output.getvalue())


@override_settings(
    AIRTEL_ENV="uat",
    AIRTEL_ENVIRONMENT="staging",
    AIRTEL_PRODUCTION_ENABLED=False,
    AIRTEL_COLLECTIONS_ENABLED=True,
    AIRTEL_DRY_RUN=False,
    AIRTEL_TEST_MAX_AMOUNT="2000",
    AIRTEL_TEST_MIN_AMOUNT="100",
    AIRTEL_ALLOWED_TEST_MSISDNS="0991234567",
    AIRTEL_CALLBACK_AUTH_ENABLED=False,
    AIRTEL_CALLBACK_HASH_KEY="testhash",
    AIRTEL_AUTH_TOKEN="test-token",
    AIRTEL_CLIENT_ID="client-id",
    AIRTEL_CLIENT_SECRET="client-secret",
    AIRTEL_MERCHANT_CODE="merchant-code",
    AIRTEL_CALLBACK_URL="https://tengasale-api.onrender.com/api/payments/airtel/callback/",
    AIRTEL_PENDING_EXPIRY_MINUTES=15,
)
class StaleAttemptExplicitRetryAcceptanceTest(TestCase):
    """An old pending attempt that never got confirmation must never block the
    customer forever, but must also never let a late/duplicate success double-
    credit the contract once the customer has explicitly retried it."""

    def setUp(self):
        self.client = Client()
        self.contract = make_contract()

    def test_explicit_retry_of_stale_attempt_is_safe_end_to_end(self):
        old = AirtelTransaction.objects.create(
            internal_reference="TENGA-AIRTEL-STALE-ACCEPT-1",
            customer_msisdn="+265991234567",
            amount=Decimal("500"),
            purpose=AirtelTransaction.PURPOSE_INSTALLMENT,
            direction=AirtelTransaction.DIRECTION_COLLECTION,
            status=AirtelTransaction.STATUS_PENDING,
            contract=self.contract,
        )
        old_portal_tx = PaymentTransaction.objects.create(
            payment_contract=self.contract,
            provider=PaymentTransaction.PROVIDER_AIRTEL,
            payment_type=PaymentTransaction.TYPE_REPAYMENT,
            amount=Decimal("500"),
            commissionable_amount=Decimal("500"),
            phone="+265991234567",
            network=PaymentTransaction.NETWORK_AIRTEL,
            status=PaymentTransaction.STATUS_PENDING,
            balance_before=self.contract.remaining_amount,
        )
        old.payment_transaction = old_portal_tx
        old.save(update_fields=["payment_transaction"])
        AirtelTransaction.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(minutes=30))

        # --- Explicit retry from this specific stale attempt ---
        with patch("payments.airtel_client.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": {"code": "200"}, "transaction": {"status_code": "TIP"}}
            response = self.client.post(
                "/api/payments/airtel/collections/initiate/",
                data=json.dumps({
                    "msisdn": "0991234567",
                    "amount": 500,
                    "purpose": "INSTALLMENT",
                    "contract_id": self.contract.id,
                    "retry_of": old.internal_reference,
                }),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        new_ref = response.json()["internal_reference"]
        new_tx = AirtelTransaction.objects.get(internal_reference=new_ref)

        old.refresh_from_db()
        old_portal_tx.refresh_from_db()
        self.assertNotEqual(new_ref, old.internal_reference)
        self.assertEqual(old.status, AirtelTransaction.STATUS_EXPIRED)
        self.assertEqual(old_portal_tx.status, PaymentTransaction.STATUS_EXPIRED)
        self.assertIsNotNone(old.expired_at)
        self.assertEqual(new_tx.retry_of_id, old.pk)
        self.assertEqual(old.superseded_by_id, new_tx.pk)

        # Customer is not jammed: the new attempt has a fully working status page.
        wait_page = self.client.get(f"/pay/payment/{new_ref}/?token={new_tx.status_token}")
        self.assertEqual(wait_page.status_code, 200)
        self.assertContains(wait_page, "Waiting for confirmation")
        self.assertContains(wait_page, "Check again")

        # The new attempt is completed successfully.
        new_callback_payload = {
            "transaction": {"reference_id": new_ref, "id": "AIRTEL-RETRY-TXN", "status_code": "TS", "amount": "500"}
        }
        new_callback_response = self.client.post(
            "/api/payments/airtel/callback/", data=json.dumps(new_callback_payload), content_type="application/json",
        )
        self.assertEqual(new_callback_response.status_code, 200)
        new_tx.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertTrue(new_tx.repayment_posted)
        self.assertEqual(self.contract.amount_paid, Decimal("500"))
        paid_after_new_attempt = self.contract.amount_paid

        # A late success for the OLD (already-superseded) attempt must be safe:
        # it must not double-credit the contract, and must be flagged for review.
        AirtelCallbackService().apply_success(old)
        old.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertFalse(old.repayment_posted)
        self.assertTrue(old.potential_overpayment)
        self.assertTrue(old.reconciliation_required)
        self.assertEqual(self.contract.amount_paid, paid_after_new_attempt)  # contract is not credited twice
