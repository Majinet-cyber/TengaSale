from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def optional_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class UpyaPaymentOption:
    contract_number: str
    payg_number: str = ""
    customer_first_name: str = ""
    customer_last_name: str = ""
    minimum_payment: Decimal | None = None
    expected_payment: Decimal | None = None
    days: int | None = None
    unit_status: str = ""
    total_paid: Decimal | None = None
    balance: Decimal | None = None
    mobile: str = ""

    @classmethod
    def from_payload(cls, payload: dict) -> "UpyaPaymentOption":
        if not isinstance(payload, dict) or not payload.get("contractNumber"):
            raise ValueError("Upya payment option is missing contractNumber.")
        try:
            days = int(payload["days"]) if payload.get("days") not in (None, "") else None
        except (TypeError, ValueError):
            days = None
        return cls(
            contract_number=str(payload["contractNumber"]),
            payg_number=str(payload.get("paygNumber") or ""),
            customer_first_name=str(payload.get("firstName") or ""),
            customer_last_name=str(payload.get("lastName") or ""),
            minimum_payment=optional_decimal(payload.get("minimumPayment")),
            expected_payment=optional_decimal(payload.get("expectedPayment")),
            days=days,
            unit_status=str(payload.get("unitStatus") or ""),
            total_paid=optional_decimal(payload.get("totalPaid")),
            balance=optional_decimal(payload.get("balance")),
            mobile=str(payload.get("mobile") or ""),
        )


@dataclass(frozen=True)
class UpyaCustomerDetails:
    contract_number: str
    first_name: str = ""
    last_name: str = ""
    mobile: str = ""
    total_paid: Decimal | None = None
    total_cost: Decimal | None = None
    recurring_payment: Decimal | None = None
    frequency: int | None = None
    product: str = ""
    serial_number: str = ""


@dataclass(frozen=True)
class UpyaPaymentRecord:
    transaction_id: str
    amount: Decimal | None = None
    currency: str = ""
    date: str = ""
    message: str = ""
