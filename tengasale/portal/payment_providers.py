"""
TengaSale Payment Provider Abstraction Layer.

Providers:
  - MockPaymentProvider  — always succeeds, used when MOCK_PAYMENTS=true
  - PayChanguProvider    — PayChangu checkout (redirect flow)
  - AirtelMoneyProvider  — Airtel Money via PayChangu MoMo API
  - TNMMpambaProvider    — TNM Mpamba via PayChangu MoMo API
  - PayTriggerProvider   — PayTrigger integration (placeholder)

PayChangu MoMo flow (Airtel / TNM):
  1. Provider calls integrations.paychangu_client.momo_initialize → sends PIN prompt to customer
  2. Customer approves on their phone
  3. PayChangu calls our webhook (/pay/webhooks/paychangu/) with the result
  4. Webhook calls apply_payment_to_contract and sends notifications

Usage:
  provider = get_payment_provider("airtel_money")
  result = provider.create_payment_intent(amount=1500, phone="0881234567", reference="TS-PAY-ABC12345")
"""

import logging
import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from datetime import datetime

from django.conf import settings

logger = logging.getLogger(__name__)


class PaymentIntentResult:
    """Returned by create_payment_intent."""
    def __init__(
        self,
        success: bool,
        provider_reference: str = "",
        redirect_url: str = "",
        message: str = "",
        raw: dict = None,
        charge_id: str = "",
        flow: str = "momo",  # "momo" | "checkout"
    ):
        self.success = success
        self.provider_reference = provider_reference
        self.redirect_url = redirect_url
        self.message = message
        self.raw = raw or {}
        self.charge_id = charge_id
        self.flow = flow


class PaymentVerifyResult:
    """Returned by verify_payment."""
    def __init__(
        self,
        paid: bool,
        provider_reference: str = "",
        amount: Decimal = Decimal("0"),
        message: str = "",
        raw: dict = None,
    ):
        self.paid = paid
        self.provider_reference = provider_reference
        self.amount = amount
        self.message = message
        self.raw = raw or {}


class PaymentProvider(ABC):
    """Abstract base for all payment providers."""

    @abstractmethod
    def create_payment_intent(
        self, amount: Decimal, phone: str, reference: str, description: str = ""
    ) -> PaymentIntentResult:
        """Initiate a payment request."""

    @abstractmethod
    def verify_payment(self, provider_reference: str) -> PaymentVerifyResult:
        """Check whether a payment has been completed."""

    @abstractmethod
    def handle_webhook(self, payload: dict) -> dict:
        """Process an incoming webhook from the provider."""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def is_configured(self) -> bool:
        return True

    @property
    def mode(self) -> str:
        return "sandbox"


# ---------------------------------------------------------------------------
# Mock Provider
# ---------------------------------------------------------------------------

class MockPaymentProvider(PaymentProvider):
    """
    Sandbox mock provider. Always returns success.
    Used when MOCK_PAYMENTS=true or when no real keys are configured.
    """

    def create_payment_intent(self, amount, phone, reference, description="") -> PaymentIntentResult:
        mock_ref = f"MOCK-{reference}"
        return PaymentIntentResult(
            success=True,
            provider_reference=mock_ref,
            message=f"[MOCK] Payment of MWK {amount} initiated for {phone}",
            raw={"mock": True, "reference": mock_ref, "amount": str(amount), "phone": phone},
            flow="mock",
        )

    def verify_payment(self, provider_reference) -> PaymentVerifyResult:
        return PaymentVerifyResult(
            paid=True,
            provider_reference=provider_reference,
            amount=Decimal("0"),
            message="[MOCK] Payment verified (sandbox mode)",
            raw={"mock": True, "status": "paid"},
        )

    def handle_webhook(self, payload) -> dict:
        return {"ok": True, "mock": True, "payload": payload}

    @property
    def is_configured(self) -> bool:
        return True

    @property
    def mode(self) -> str:
        return "mock"


# ---------------------------------------------------------------------------
# PayChangu Provider — Standard Checkout (redirect)
# ---------------------------------------------------------------------------

class PayChanguProvider(PaymentProvider):
    """
    PayChangu standard checkout. Redirects customer to PayChangu-hosted checkout page.
    Used when provider_name="paychangu" (no specific network selected).

    Environment variables required:
      PAYCHANGU_PUBLIC_KEY
      PAYCHANGU_SECRET_KEY
      PAYCHANGU_WEBHOOK_SECRET
      PAYCHANGU_API_BASE  (default: https://api.paychangu.com)
    """

    def __init__(self):
        self.public_key = getattr(settings, "PAYCHANGU_PUBLIC_KEY", "")
        self.secret_key = getattr(settings, "PAYCHANGU_SECRET_KEY", "")
        self.webhook_secret = getattr(settings, "PAYCHANGU_WEBHOOK_SECRET", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.public_key and self.secret_key)

    @property
    def mode(self) -> str:
        payments_mode = getattr(settings, "PAYMENTS_MODE", "test").lower()
        if not self.is_configured:
            return "not_configured"
        return payments_mode

    def _build_callback_url(self) -> str:
        base = getattr(settings, "TENGASALE_PAYMENT_BASE_URL", "") or getattr(settings, "TENGASALE_PUBLIC_BASE_URL", "")
        if base:
            return f"{base.rstrip('/')}/pay/webhooks/paychangu/"
        configured = getattr(settings, "PAYCHANGU_CALLBACK_URL", "")
        return configured or ""

    def create_payment_intent(self, amount, phone, reference, description="") -> PaymentIntentResult:
        from integrations import paychangu_client

        if not self.is_configured or paychangu_client.is_mock_mode():
            mock_ref = f"MOCK-{reference}"
            return PaymentIntentResult(
                success=True,
                provider_reference=mock_ref,
                message=f"[MOCK] Checkout of MWK {amount} initiated",
                raw={"mock": True},
                flow="mock",
            )

        return_url = self._build_callback_url().replace("/pay/webhooks/paychangu/", "/pay/payment-return/") or "/pay/"
        callback_url = self._build_callback_url()

        result = paychangu_client.initiate_payment(
            amount=Decimal(str(amount)),
            tx_ref=reference,
            return_url=return_url,
            callback_url=callback_url or None,
            customer_phone=phone,
            description=description or "TengaSale Payment",
        )

        if result.get("status") == "success":
            return PaymentIntentResult(
                success=True,
                provider_reference=reference,
                redirect_url=result.get("checkout_url", ""),
                message="Redirecting to secure payment page.",
                raw=result.get("raw_response", {}),
                flow="checkout",
            )
        return PaymentIntentResult(
            success=False,
            message=result.get("message", "Failed to create checkout."),
            raw=result.get("raw_response", {}),
        )

    def verify_payment(self, provider_reference) -> PaymentVerifyResult:
        from integrations import paychangu_client
        result = paychangu_client.verify_transaction(provider_reference)
        paid = result.get("status") == "SUCCESS"
        return PaymentVerifyResult(
            paid=paid,
            provider_reference=provider_reference,
            amount=Decimal(str(result.get("amount", "0") or "0")),
            message=result.get("message", ""),
            raw=result.get("raw_response", {}),
        )

    def handle_webhook(self, payload: dict) -> dict:
        return {"ok": True, "provider": "paychangu", "payload": payload}


# ---------------------------------------------------------------------------
# PayChangu MoMo Base — shared by Airtel and TNM
# ---------------------------------------------------------------------------

class _PayChanguMoMoProvider(PaymentProvider):
    """
    Base class for PayChangu Mobile Money providers (Airtel Money + TNM Mpamba).
    Sends a PIN prompt directly to the customer's phone via PayChangu MoMo API.
    """
    _momo_method: str = ""  # "airtel" or "tnm"

    def __init__(self):
        self.secret_key = getattr(settings, "PAYCHANGU_SECRET_KEY", "")
        self.public_key = getattr(settings, "PAYCHANGU_PUBLIC_KEY", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.secret_key and self.public_key)

    @property
    def mode(self) -> str:
        payments_mode = getattr(settings, "PAYMENTS_MODE", "test").lower()
        if not self.is_configured:
            return "not_configured"
        return payments_mode

    def _build_callback_url(self) -> str:
        base = (
            getattr(settings, "TENGASALE_PAYMENT_BASE_URL", "")
            or getattr(settings, "TENGASALE_PUBLIC_BASE_URL", "")
        )
        if base:
            return f"{base.rstrip('/')}/pay/webhooks/paychangu/"
        return getattr(settings, "PAYCHANGU_CALLBACK_URL", "") or ""

    def create_payment_intent(self, amount, phone, reference, description="") -> PaymentIntentResult:
        from integrations import paychangu_client

        if paychangu_client.is_mock_mode():
            mock_ref = f"MOCK-{self._momo_method.upper()}-{reference}"
            return PaymentIntentResult(
                success=True,
                provider_reference=mock_ref,
                message=f"[MOCK] {self._momo_method.upper()} PIN prompt sent to {phone}. Enter your PIN to confirm.",
                raw={"mock": True, "method": self._momo_method},
                flow="mock",
            )

        # Resolve operator ref_id
        op_result = paychangu_client.get_operator_ref_id(self._momo_method)
        if op_result.get("status") != "success":
            logger.error(
                "PayChangu MoMo: could not resolve operator ref_id for %s: %s",
                self._momo_method, op_result.get("message"),
            )
            return PaymentIntentResult(
                success=False,
                message=f"Payment network ({self._momo_method.upper()}) not available. Try again or contact support.",
            )

        operator_ref_id = op_result["ref_id"]
        charge_id = f"ch-{uuid.uuid4().hex[:16]}"
        callback_url = self._build_callback_url()

        result = paychangu_client.momo_initialize(
            operator_ref_id=operator_ref_id,
            mobile=phone,
            amount=Decimal(str(amount)),
            tx_ref=reference,
            charge_id=charge_id,
            callback_url=callback_url or None,
            description=description or "TengaSale Payment",
        )

        if result.get("status") == "success":
            return PaymentIntentResult(
                success=True,
                provider_reference=charge_id,
                message=f"PIN prompt sent. Enter your {self._momo_method.upper()} PIN on your phone to confirm payment.",
                raw=result.get("raw_response", {}),
                charge_id=charge_id,
                flow="momo",
            )
        return PaymentIntentResult(
            success=False,
            message=result.get("message", "Mobile money payment initiation failed."),
            raw=result.get("raw_response", {}),
        )

    def verify_payment(self, provider_reference: str) -> PaymentVerifyResult:
        from integrations import paychangu_client
        result = paychangu_client.momo_verify(provider_reference)
        paid = result.get("status") == "SUCCESS"
        return PaymentVerifyResult(
            paid=paid,
            provider_reference=provider_reference,
            amount=Decimal(str(result.get("amount", "0") or "0")),
            message=result.get("message", ""),
            raw=result.get("raw_response", {}),
        )

    def handle_webhook(self, payload: dict) -> dict:
        return {"ok": True, "provider": f"paychangu_{self._momo_method}", "payload": payload}


# ---------------------------------------------------------------------------
# Airtel Money Provider (via PayChangu MoMo)
# ---------------------------------------------------------------------------

class AirtelMoneyProvider(_PayChanguMoMoProvider):
    """
    Airtel Money Malawi via PayChangu MoMo API.

    Falls back to direct Airtel credentials if AIRTEL_MONEY_CLIENT_ID is configured
    (not yet implemented — uses PayChangu MoMo as the default path).
    """
    _momo_method = "airtel"

    @property
    def is_configured(self) -> bool:
        # Use PayChangu MoMo if PayChangu is configured
        if super().is_configured:
            return True
        # Could also check AIRTEL_MONEY_CLIENT_ID here for direct integration
        return False


# ---------------------------------------------------------------------------
# TNM Mpamba Provider (via PayChangu MoMo)
# ---------------------------------------------------------------------------

class TNMMpambaProvider(_PayChanguMoMoProvider):
    """
    TNM Mpamba Malawi via PayChangu MoMo API.
    """
    _momo_method = "tnm"

    @property
    def is_configured(self) -> bool:
        if super().is_configured:
            return True
        return False


# ---------------------------------------------------------------------------
# PayTrigger Provider (placeholder)
# ---------------------------------------------------------------------------

class PayTriggerProvider(PaymentProvider):
    """
    PayTrigger integration.

    Environment variables required:
      PAYTRIGGER_API_KEY
    """

    def __init__(self):
        self.api_key = getattr(settings, "PAYTRIGGER_API_KEY", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def mode(self) -> str:
        return "live" if self.is_configured else "not_configured"

    def create_payment_intent(self, amount, phone, reference, description="") -> PaymentIntentResult:
        if not self.is_configured:
            return PaymentIntentResult(
                success=False,
                message="PayTrigger is not configured. Set PAYTRIGGER_API_KEY.",
            )
        return PaymentIntentResult(success=False, message="PayTrigger integration not yet implemented.")

    def verify_payment(self, provider_reference) -> PaymentVerifyResult:
        return PaymentVerifyResult(paid=False, message="PayTrigger verification not yet implemented.")

    def handle_webhook(self, payload) -> dict:
        return {"ok": False, "message": "PayTrigger webhook handler not yet implemented."}


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def get_payment_provider(provider_name: str = None) -> PaymentProvider:
    """
    Return the appropriate payment provider.

    If MOCK_PAYMENTS=true (default), always returns MockPaymentProvider.
    Otherwise, selects based on provider_name.

    Provider names:
      "paychangu"    → PayChanguProvider (checkout/redirect)
      "airtel_money" → AirtelMoneyProvider (MoMo direct charge via PayChangu)
      "tnm_mpamba"   → TNMMpambaProvider (MoMo direct charge via PayChangu)
      "paytrigger"   → PayTriggerProvider
    """
    if getattr(settings, "MOCK_PAYMENTS", True):
        return MockPaymentProvider()

    providers = {
        "paychangu": PayChanguProvider,
        "airtel_money": AirtelMoneyProvider,
        "tnm_mpamba": TNMMpambaProvider,
        "paytrigger": PayTriggerProvider,
    }

    if provider_name and provider_name in providers:
        provider = providers[provider_name]()
        if provider.is_configured:
            return provider
        logger.warning(
            "Provider '%s' is not configured — falling back to mock.", provider_name
        )

    # Try PayChangu first, then fall back to mock
    paychangu = PayChanguProvider()
    if paychangu.is_configured:
        return paychangu

    logger.warning("No real payment provider configured — using mock mode.")
    return MockPaymentProvider()


def get_integration_status() -> list[dict]:
    """
    Return integration status for all providers.
    Used in admin/dev surfaces to show what's configured.
    """
    from integrations import paychangu_client
    return [
        {
            "name": "Mock Payment",
            "provider": "mock",
            "status": "active" if getattr(settings, "MOCK_PAYMENTS", True) else "inactive",
            "mode": "mock",
        },
        {
            "name": "PayChangu Checkout",
            "provider": "paychangu",
            "status": "configured" if PayChanguProvider().is_configured else "not_configured",
            "mode": PayChanguProvider().mode,
        },
        {
            "name": "Airtel Money (via PayChangu MoMo)",
            "provider": "airtel_money",
            "status": "configured" if AirtelMoneyProvider().is_configured else "not_configured",
            "mode": AirtelMoneyProvider().mode,
            "mock_mode": paychangu_client.is_mock_mode(),
        },
        {
            "name": "TNM Mpamba (via PayChangu MoMo)",
            "provider": "tnm_mpamba",
            "status": "configured" if TNMMpambaProvider().is_configured else "not_configured",
            "mode": TNMMpambaProvider().mode,
            "mock_mode": paychangu_client.is_mock_mode(),
        },
        {
            "name": "PayTrigger",
            "provider": "paytrigger",
            "status": "configured" if PayTriggerProvider().is_configured else "not_configured",
            "mode": PayTriggerProvider().mode,
        },
    ]
