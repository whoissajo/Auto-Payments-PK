import os
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from api._shared import (
    NAYAPAY_ADAPTER,
    ServiceError,
    parse_email_fields,
    parse_money,
    validate_identifier,
    load_adapters,
    _db_request,
    _load_bank_profiles,
    bank_directory,
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


class SupabaseKeyCompatibilityTests(unittest.TestCase):
    def _mock_response(self):
        response = Mock()
        response.status = 200
        response.read.return_value = b"[]"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_modern_secret_key_is_not_sent_as_bearer_jwt(self):
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_test_key",
        }):
            with patch("api._shared.urllib.request.urlopen", return_value=self._mock_response()) as urlopen:
                status, data = _db_request("GET")
                request = urlopen.call_args.args[0]
                self.assertEqual(status, 200)
                self.assertEqual(data, [])
                self.assertEqual(request.get_header("Apikey"), "sb_secret_test_key")
                self.assertIsNone(request.get_header("Authorization"))

    def test_legacy_service_role_jwt_is_sent_as_bearer(self):
        legacy_key = "eyJ.test.legacy-service-role"
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": legacy_key,
        }):
            with patch("api._shared.urllib.request.urlopen", return_value=self._mock_response()) as urlopen:
                _db_request("GET")
                request = urlopen.call_args.args[0]
                self.assertEqual(request.get_header("Apikey"), legacy_key)
                self.assertEqual(request.get_header("Authorization"), "Bearer " + legacy_key)


class NarrowNayaPayAmountParsingTests(unittest.TestCase):
    def test_missing_amount_does_not_fall_through_to_total_amount(self):
        message = (
            "Transaction ID: SADAPKKA202609241090215740392466\n"
            "Amount Received\n"
            "Total Amount\n"
            "Rs. 1.00"
        )
        txid, amount, total = parse_email_fields(message, NAYAPAY_ADAPTER)
        self.assertEqual(txid, "SADAPKKA202609241090215740392466")
        self.assertIsNone(amount)
        self.assertEqual(total, Decimal("1.00"))


class BankCatalogTests(unittest.TestCase):
    def test_committed_catalog_contains_bank_and_wallet_profiles(self):
        with patch.dict(os.environ, {}, clear=True):
            profiles = _load_bank_profiles()
            self.assertGreaterEqual(len(profiles), 50)
            self.assertIn("nayapay", profiles)
            self.assertIn("hbl", profiles)
            self.assertIn("jazzcash", profiles)
            self.assertIn("mashreq-bank", profiles)

    def test_only_verified_nayapay_template_is_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            profiles = _load_bank_profiles()
            adapters = load_adapters(profiles)
            self.assertEqual(set(adapters), {"nayapay"})
            self.assertFalse(profiles["hbl"]["enabled"])
            self.assertEqual(profiles["hbl"]["sender_allowlist"], [])
            self.assertIsNone(profiles["hbl"]["amount_regex"])

    def test_committed_nayapay_adapter_parses_label_value_template(self):
        with patch.dict(os.environ, {}, clear=True):
            profile = _load_bank_profiles()["nayapay"]
        message = (
            "Transaction ID\nSADAPKKA202609241090215740392466\n"
            "Amount Received\nRs. 1\nTotal Amount\nRs. 1"
        )
        txid, amount, total = parse_email_fields(message, profile)
        self.assertEqual(txid, "SADAPKKA202609241090215740392466")
        self.assertEqual(amount, Decimal("1.00"))
        self.assertEqual(total, Decimal("1.00"))

    def test_bank_directory_exposes_configuration_status(self):
        with patch.dict(os.environ, {}, clear=True):
            directory = bank_directory()
        by_id = {item["id"]: item for item in directory}
        self.assertIn("nayapay", by_id)
        self.assertTrue(by_id["nayapay"]["configured"])
        self.assertIn("hbl", by_id)
        self.assertFalse(by_id["hbl"]["configured"])
        self.assertEqual(by_id["hbl"]["status"], "needs_verified_email_sample")


if __name__ == "__main__":
    unittest.main()
