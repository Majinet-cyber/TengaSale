from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from integrations.upya.exceptions import UpyaError
from integrations.upya.phone import InvalidMalawiMobile, normalize_malawi_mobile

from .account_service import CustomerFinanceAccountService
from .models import USSDSession


def ussd_continue(text):
    return f"CON {text}"


def ussd_end(text):
    return f"END {text}"


@dataclass(frozen=True)
class UssdRequest:
    session_id: str
    service_code: str
    phone_number: str
    text: str


class UssdProviderAdapter:
    required_fields = ("sessionId", "phoneNumber")

    def parse_request(self, request):
        values = {name: request.POST.get(name, "").strip() for name in (*self.required_fields, "serviceCode", "text")}
        if any(not values[name] for name in self.required_fields):
            raise ValueError("Missing required USSD provider fields.")
        if settings.USSD_SERVICE_CODE and values["serviceCode"] != settings.USSD_SERVICE_CODE:
            raise ValueError("Unexpected USSD service code.")
        return UssdRequest(values["sessionId"][:128], values["serviceCode"][:64], values["phoneNumber"][:32], values["text"][:1000])


class TengaUssdService:
    """Provider-neutral USSD state machine. All financed values are fresh Upya values."""

    ROOT = "ROOT"

    def __init__(self, account_service=None):
        self.accounts = account_service or CustomerFinanceAccountService()

    @staticmethod
    def _money(value):
        return f"K{Decimal(value):,.0f}" if value is not None else "Unknown"

    @staticmethod
    def _status(value):
        labels = {"ENABLED": "Active", "LOCKED": "Locked", "PAIDOFF": "Paid off", "REPOSSESSED": "Repossessed", "WRITEOFF": "Written off"}
        return labels.get(str(value or "").upper(), str(value or "Unknown").replace("_", " ").title())

    @staticmethod
    def root_menu():
        return ussd_continue("Welcome to Tenga\n1. Pay\n2. Check balance\n3. Last payment\n4. Contract details")

    def _contracts(self, mobile):
        contracts = self.accounts.get_contracts_for_customer(mobile)
        if not contracts:
            return None, ussd_end("No Tenga contract found for this number.")
        return contracts, None

    @staticmethod
    def _select_contract(contracts, token):
        try:
            index = int(token) - 1
            return contracts[index] if index >= 0 else None
        except (ValueError, IndexError):
            return None

    def _contract_for(self, contracts, parts):
        if len(contracts) == 1:
            return contracts[0], 1
        if len(parts) < 2:
            lines = ["Choose contract"] + [f"{i}. Contract ...{item.contract_number[-4:]}" for i, item in enumerate(contracts, 1)]
            return None, ussd_continue("\n".join(lines))
        contract = self._select_contract(contracts, parts[1])
        if not contract:
            return None, ussd_end("Invalid contract selection.")
        return contract, 2

    def handle(self, event):
        if not settings.USSD_ENABLED:
            return ussd_end("Tenga USSD is not available yet. Please try again later.")
        try:
            mobile = normalize_malawi_mobile(event.phone_number)
        except InvalidMalawiMobile:
            return ussd_end("We could not confirm your mobile number. Please contact Tenga support.")
        session, _ = USSDSession.objects.update_or_create(
            session_id=event.session_id,
            defaults={"normalized_mobile": mobile, "provider": settings.USSD_PROVIDER, "service_code": event.service_code, "expires_at": timezone.now() + timedelta(minutes=settings.USSD_SESSION_TTL_MINUTES)},
        )
        parts = event.text.split("*") if event.text else []
        if not parts:
            session.state = self.ROOT
            session.selected_contract_number = ""
            session.save(update_fields=["state", "selected_contract_number", "updated_at"])
            return self.root_menu()
        if parts[-1] == "0":
            return self.root_menu()
        if parts[0] not in {"1", "2", "3", "4"}:
            return ussd_end("Invalid option. Please try again.")
        rate_key = f"ussd:lookup:{mobile}"
        count = cache.get(rate_key, 0)
        if count >= settings.USSD_LOOKUP_RATE_LIMIT:
            return ussd_end("Too many requests. Please try again shortly.")
        cache.set(rate_key, count + 1, settings.USSD_LOOKUP_RATE_WINDOW)
        try:
            contracts, error = self._contracts(mobile)
            if error:
                return error
            contract, consumed = self._contract_for(contracts, parts)
            if isinstance(consumed, str):
                session.state = "SELECT_CONTRACT"
                session.save(update_fields=["state", "updated_at"])
                return consumed
            session.selected_contract_number = contract.contract_number
            session.state = {"1": "PAY_MENU", "2": "BALANCE", "3": "LAST_PAYMENT", "4": "CONTRACT_DETAILS"}[parts[0]]
            session.save(update_fields=["selected_contract_number", "state", "updated_at"])
            if parts[0] == "2":
                return ussd_end(f"Tenga\nBalance: {self._money(contract.balance)}\nDue: {self._money(contract.expected_payment)}\nMinimum: {self._money(contract.minimum_payment)}\nStatus: {self._status(contract.unit_status)}")
            if parts[0] == "3":
                payment = self.accounts.get_last_payment(contract.contract_number)
                if not payment:
                    return ussd_end("No payment found yet.")
                reference = f"...{payment.transaction_id[-5:]}" if payment.transaction_id else ""
                date = payment.date[:10] if payment.date else ""
                details = ["Last payment", self._money(payment.amount)]
                if date: details.append(date)
                if reference: details.append(f"Ref: {reference}")
                return ussd_end("\n".join(details))
            if parts[0] == "4":
                details = self.accounts.get_contract_details(contract.contract_number)
                lines = ["Contract"]
                if details.product: lines.append(details.product)
                lines.append(f"Contract: {details.contract_number}")
                if details.total_paid is not None: lines.append(f"Paid: {self._money(details.total_paid)}")
                if details.total_cost is not None: lines.append(f"Total: {self._money(details.total_cost)}")
                lines.append(f"Status: {self._status(contract.unit_status)}")
                return ussd_end("\n".join(lines))
            return self._pay_menu(contract, parts, consumed)
        except UpyaError:
            return ussd_end("Tenga is temporarily unable to confirm your account. Please try again shortly.")

    def _pay_menu(self, contract, parts, consumed):
        status = str(contract.unit_status or "").upper()
        if status == "PAIDOFF":
            return ussd_end("This contract is fully paid. Thank you for choosing Tenga.")
        if status in {"REPOSSESSED", "WRITEOFF"}:
            return ussd_end("This contract needs support. Please contact Tenga.")
        amount_token_index = consumed
        choices = []
        if contract.expected_payment is not None: choices.append(("due", contract.expected_payment))
        if contract.minimum_payment is not None and contract.minimum_payment != contract.expected_payment: choices.append(("minimum", contract.minimum_payment))
        if len(parts) <= amount_token_index:
            if not choices:
                return ussd_end("Payment amounts are unavailable. Please contact Tenga.")
            lines = ["Choose amount"] + [f"{i}. {self._money(amount)} {label}" for i, (label, amount) in enumerate(choices, 1)]
            return ussd_continue("\n".join(lines))
        # Live initiation is deliberately fail-closed until the provider-to-Upya
        # reference format is confirmed; accepting an intent here could misallocate money.
        return ussd_end("USSD payment is not enabled yet. Please use the secure Tenga payment portal.")
