import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from api._shared import (
    NAYAPAY_ADAPTER,
    ServiceError,
    parse_email_fields,
    parse_money,
    validate_identifier,
    load_adapters,
)


class AmountParsingTests(unittest.TestCase):
    def test_integer_rupee_amount_becomes_two_decimals(self):
        self.assertEqual(parse_money("1"), Decimal("1.00"))

    def test_decimal_amount(self):
        self.assertEqual(parse_money("1,250.50"), Decimal("1250.50"))

    def test_rejects_zero_or_negative(self):
        for value in ("0", "-1", "-0.01"):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError):
                    parse_money(value)

    def test_rejects_more_than_two_decimal_places(self):
        with self.assertRaises(ServiceError):
            parse_money("15.001")

    def test_rejects_non_finite_values(self):
        for value in ("NaN", "Infinity", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError):
                    parse_money(value)


class NayaPayParserTests(unittest.TestCase):
    def test_parses_label_value_email(self):
        message = """
        Transaction ID
        SADAPKKA202609241090215740392466
        Amount Received
        Rs. 1
        Total Amount
        Rs. 1
        """
        txid, amount, total = parse_email_fields(message, NAYAPAY_ADAPTER)
        self.assertEqual(txid, "SADAPKKA202609241090215740392466")
        self.assertEqual(amount, Decimal("1.00"))
        self.assertEqual(total, Decimal("1.00"))

    def test_parses_same_line_amount_fields(self):
        message = (
            "Transaction ID: SADAPKKA202609241090215740392466\n"
            "Amount Received: PKR 1,250.75\n"
            "Total Amount: PKR 1,250.75"
        )
        txid, amount, total = parse_email_fields(message, NAYAPAY_ADAPTER)
        self.assertEqual(txid, "SADAPKKA202609241090215740392466")
        self.assertEqual(amount, Decimal("1250.75"))
        self.assertEqual(total, Decimal("1250.75"))

    def test_wrong_or_missing_amount_does_not_parse_as_payment(self):
        message = "Transaction ID: SADAPKKA202609241090215740392466\nBalance: Rs 100"
        txid, amount, total = parse_email_fields(message, NAYAPAY_ADAPTER)
        self.assertEqual(txid, "SADAPKKA202609241090215740392466")
        self.assertIsNone(amount)
        self.assertIsNone(total)

    def test_mismatched_total_can_be_detected(self):
        message = (
            "Transaction ID: SADAPKKA202609241090215740392466\n"
            "Amount Received: Rs. 10.00\n"
            "Total Amount: Rs. 11.00"
        )
        _, amount, total = parse_email_fields(message, NAYAPAY_ADAPTER)
        self.assertEqual(amount, Decimal("10.00"))
        self.assertEqual(total, Decimal("11.00"))
        self.assertNotEqual(amount, total)


class InputValidationTests(unittest.TestCase):
    def test_accepts_transaction_identifier(self):
        self.assertEqual(
            validate_identifier("ABCD123456", "transaction_id", r"[A-Za-z0-9][A-Za-z0-9._/-]{5,127}", 128),
            "ABCD123456",
        )

    def test_rejects_query_injection_characters_in_transaction_id(self):
        with self.assertRaises(ServiceError):
            validate_identifier('123456" OR ALL', "transaction_id", r"[A-Za-z0-9][A-Za-z0-9._/-]{5,127}", 128)

    def test_default_nayapay_adapter_is_loaded(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("nayapay", load_adapters())


if __name__ == "__main__":
    unittest.main()
