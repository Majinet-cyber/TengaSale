from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from .exceptions import UpyaAuthenticationError, UpyaBadResponse, UpyaTimeout
from .payment_gateway import UpyaPaymentGateway
from .phone import InvalidMalawiMobile, normalize_malawi_mobile


class PhoneNormalizationTests(SimpleTestCase):
    def test_supported_malawi_formats_share_one_canonical_value(self):
        for value in ("+265 99 123 4567", "265991234567", "0991-234-567"):
            self.assertEqual(normalize_malawi_mobile(value), "265991234567")

    def test_invalid_or_partial_numbers_are_rejected(self):
        for value in ("", "991234", "+260971234567"):
            with self.assertRaises(InvalidMalawiMobile):
                normalize_malawi_mobile(value)


@override_settings(
    UPYA_PAYMENT_GATEWAY_BASE_URL="https://mm.api.upya.io",
    UPYA_PAYMENT_GATEWAY_USERNAME="user", UPYA_PAYMENT_GATEWAY_PASSWORD="secret",
    UPYA_PAYMENT_GATEWAY_CLIENT_IDENTIFIER="tenga", UPYA_CONNECT_TIMEOUT=1, UPYA_READ_TIMEOUT=2,
)
class UpyaPaymentGatewayTests(SimpleTestCase):
    def response(self, payload, status=200):
        response = Mock(status_code=status, ok=200 <= status < 300)
        response.json.return_value = payload
        return response

    def test_payment_options_are_normalized_without_calculation(self):
        session = Mock()
        session.request.return_value = self.response({"contractNumber": "C-1", "balance": "785000", "expectedPayment": "24500", "minimumPayment": "5000"})
        option = UpyaPaymentGateway(session=session).get_payment_options(subscriber="265991234567")[0]
        self.assertEqual(str(option.balance), "785000")
        self.assertEqual(str(option.expected_payment), "24500")
        args, kwargs = session.request.call_args
        self.assertEqual(kwargs["json"], {"subscriber": "265991234567"})
        self.assertEqual(kwargs["auth"], ("user", "secret"))
        self.assertEqual(kwargs["timeout"], (1, 2))

    def test_missing_money_stays_unknown(self):
        session = Mock()
        session.request.return_value = self.response({"contractNumber": "C-1"})
        option = UpyaPaymentGateway(session=session).get_payment_options(reference="C-1")[0]
        self.assertIsNone(option.balance)
        self.assertIsNone(option.minimum_payment)

    def test_auth_and_bad_shapes_fail_closed(self):
        session = Mock()
        session.request.return_value = self.response({}, 403)
        with self.assertRaises(UpyaAuthenticationError):
            UpyaPaymentGateway(session=session).get_payment_options(reference="C-1")
        session.request.return_value = self.response("bad")
        with self.assertRaises(UpyaBadResponse):
            UpyaPaymentGateway(session=session).get_payment_options(reference="C-1")
