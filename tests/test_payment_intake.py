import hashlib
import hmac
import unittest

from payment_intake import (
    SepayPayloadError,
    SepaySignatureError,
    parse_sepay_webhook_payload,
    verify_sepay_hmac,
)


class PaymentIntakeTests(unittest.TestCase):
    def test_parse_nested_transaction_payload(self):
        raw_body = (
            b'{"transaction":{"transferAmount":"70,000",'
            b'"content":"Thanh toan MUA ABCD1234 cho shop","transferType":"in"}}'
        )

        payment = parse_sepay_webhook_payload(raw_body)

        self.assertEqual(payment.amount, 70000)
        self.assertEqual(payment.content, "Thanh toan MUA ABCD1234 cho shop")
        self.assertEqual(payment.payment_code, "MUA ABCD1234")
        self.assertTrue(payment.is_incoming)

    def test_parse_top_level_reference_transaction_id(self):
        raw_body = b'{"amount":"70000","content":"MUA ABCD1234","referenceCode":"REF1","transferType":"in"}'

        payment = parse_sepay_webhook_payload(raw_body)

        self.assertEqual(payment.transaction_id, "ref:REF1:in:70000")

    def test_reject_invalid_json_payload(self):
        with self.assertRaises(SepayPayloadError):
            parse_sepay_webhook_payload(b"{bad json")

    def test_verify_valid_hmac(self):
        raw_body = b'{"ok":true}'
        secret = "secret"
        timestamp = 1234567890
        signature = "sha256=" + hmac.new(
            secret.encode(),
            str(timestamp).encode() + b"." + raw_body,
            hashlib.sha256,
        ).hexdigest()

        verify_sepay_hmac(
            raw_body,
            {"x-sepay-signature": signature, "x-sepay-timestamp": str(timestamp)},
            secret,
            now=timestamp,
        )

    def test_reject_invalid_hmac(self):
        with self.assertRaises(SepaySignatureError):
            verify_sepay_hmac(
                b'{"ok":true}',
                {"x-sepay-signature": "bad", "x-sepay-timestamp": "1234567890"},
                "secret",
                now=1234567890,
            )


if __name__ == "__main__":
    unittest.main()
