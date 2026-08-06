from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import (
    Customer,
    Device,
    DeviceCommand,
    DeviceStatusLog,
    FinancingContract,
    PaymentRecord,
    RepaymentSchedule,
    UnlockToken,
)


class FinancingTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="merchant-finance", password="Testpass123!")
        self.user.profile.role = "merchant"
        self.user.profile.save(update_fields=["role"])
        self.customer = Customer.objects.create(
            full_name="Jane Customer",
            phone_number="+265991234567",
            customer_id_number="NID12345",
            address="Lilongwe",
            next_of_kin_name="John Kin",
            next_of_kin_phone="+265999111222",
        )
        self.device = Device.objects.create(
            brand="TECNO",
            model="Spark",
            imei_1="123456789012345",
            purchase_cost=Decimal("100000.00"),
            selling_price=Decimal("150000.00"),
        )
        self.contract = FinancingContract.objects.create(
            customer=self.customer,
            device=self.device,
            deposit_amount=Decimal("15000.00"),
            total_loan_amount=Decimal("135000.00"),
            monthly_payment_amount=Decimal("45000.00"),
            term_months=3,
            start_date=timezone.localdate(),
            next_due_date=timezone.localdate() + timedelta(days=30),
            lock_date=timezone.localdate() + timedelta(days=35),
            created_by=self.user,
        )
        services.build_repayment_schedule(self.contract)


class FinancingModelTests(FinancingTestCase):
    def test_contract_creation_assigns_device(self):
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.STATUS_FINANCED)
        self.assertEqual(self.device.assigned_customer, self.customer)
        self.assertTrue(
            DeviceStatusLog.objects.filter(
                device=self.device,
                contract=self.contract,
                action=DeviceStatusLog.ACTION_ASSIGNED,
            ).exists()
        )

    def test_payment_verification_generates_unlock_token(self):
        payment = PaymentRecord.objects.create(
            contract=self.contract,
            customer=self.customer,
            amount=Decimal("45000.00"),
            payment_method=PaymentRecord.METHOD_CASH,
            transaction_reference="CASH-001",
        )

        verified, token = services.verify_payment(payment, verified_by=self.user)

        self.assertEqual(verified.verification_status, PaymentRecord.STATUS_VERIFIED)
        self.assertEqual(token.status, UnlockToken.STATUS_ACTIVE)
        self.assertEqual(token.contract, self.contract)
        self.assertEqual(token.device, self.device)
        self.assertEqual(token.customer, self.customer)
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.STATUS_UNLOCKED)
        self.assertTrue(DeviceCommand.objects.filter(command_type=DeviceCommand.TYPE_UNLOCK).exists())

    def test_device_command_creation_and_mock_lock_unlock(self):
        lock_command = services.issue_device_command(self.contract, DeviceCommand.TYPE_LOCK, created_by=self.user)
        self.device.refresh_from_db()
        self.assertEqual(lock_command.status, DeviceCommand.STATUS_SUCCESSFUL)
        self.assertEqual(self.device.status, Device.STATUS_LOCKED)

        unlock_command = services.issue_device_command(self.contract, DeviceCommand.TYPE_UNLOCK, created_by=self.user)
        self.device.refresh_from_db()
        self.assertEqual(unlock_command.status, DeviceCommand.STATUS_SUCCESSFUL)
        self.assertEqual(self.device.status, Device.STATUS_UNLOCKED)


class FinancingApiTests(FinancingTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_api_lock_workflow_uses_mock_provider(self):
        response = self.client.post(
            reverse("api_tengasale_device_lock"),
            data={"contract_id": self.contract.id},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["status"], DeviceCommand.STATUS_SUCCESSFUL)
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Device.STATUS_LOCKED)

    def test_api_payment_verify_generates_token(self):
        payment = PaymentRecord.objects.create(
            contract=self.contract,
            customer=self.customer,
            amount=Decimal("45000.00"),
            payment_method=PaymentRecord.METHOD_AIRTEL,
            transaction_reference="AIRTEL-001",
        )
        response = self.client.post(
            reverse("api_tengasale_payment_verify"),
            data={"payment_id": payment.id},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertTrue(UnlockToken.objects.filter(id=payload["data"]["unlock_token_id"]).exists())

    def test_customer_device_portal_renders_financed_phone_controls(self):
        response = self.client.get(reverse("customer_device_portal", args=[self.contract.id]))
        self.assertContains(response, "TengaSale financed phone")
        self.assertContains(response, "PAY NOW")
        self.assertContains(response, "Enter PIN Unlock")
        self.assertContains(response, self.device.masked_imei)

    def test_pending_payments_requires_merchant_or_staff(self):
        response = self.client.get(reverse("financing_pending_payments"))
        self.assertEqual(response.status_code, 200)
