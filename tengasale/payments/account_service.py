from integrations.upya import UpyaPaymentGateway
from integrations.upya.phone import normalize_malawi_mobile


class CustomerFinanceAccountService:
    """Shared, authoritative customer account reads backed only by Upya."""

    def __init__(self, gateway=None):
        self.gateway = gateway or UpyaPaymentGateway()

    def get_contracts_for_customer(self, mobile):
        return self.gateway.get_payment_options(subscriber=normalize_malawi_mobile(mobile))

    def get_account_summary(self, contract_number):
        options = self.gateway.get_payment_options(reference=contract_number)
        return options[0] if options else None

    def get_last_payment(self, contract_number):
        payments = self.gateway.get_last_payments(contract_number, limit=1)
        return payments[0] if payments else None

    def get_contract_details(self, contract_number):
        return self.gateway.get_customer_details(contract_number)
