import base64
import os
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from api.generate import build_qr_payload, qr_svg_data_url
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
    create_payment_intent,
)


class PaymentQRTests(unittest.TestCase):
    def test_builds_dynamic_uri_with_encoded_amount_and_reference(self):
        payload = build_qr_payload(
            "pay://merchant?amount={amount}&reference={reference}",
            Decimal("12.50"),
            "ORDER:AB 123",
        )
        self.assertEqual(
            payload,
            "pay://merchant?amount=12.50&reference=ORDER%3AAB%20123",
        )

    def test_rejects_template_without_amount_placeholder(self):
        with self.assertRaises(ServiceError) as error:
            build_qr_payload("pay://merchant?account=123", Decimal("12.50"), "ORDER-123")
        self.assertEqual(error.exception.code, "qr_not_configured")

    def test_qr_encoder_returns_svg_data_url(self):
        result = qr_svg_data_url("pay://merchant?amount=1.00&reference=TEST-123")
        self.assertTrue(result.startswith("data:image/svg+xml;base64,"))
        svg = base64.b64decode(result.split(",", 1)[1]).decode("utf-8")
        self.assertIn("<svg", svg)
        self.assertIn("<path", svg)
        self.assertIn("</svg>", svg)

    def test_intent_creation_sets_five_minute_expiry_and_random_token(self):
        from datetime import datetime

        def fake_db_request(method, query=None, payload=None, table="verified_payments"):
            self.assertEqual(method, "POST")
            self.assertEqual(table, "payment_intents")
            created = datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))
            expires = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
            self.assertEqual((expires - created).total_seconds(), 300)
            self.assertEqual(payload["status"], "pending")
            self.assertEqual(payload["amount"], "125.00")
            self.assertEqual(len(payload["status_token"]), 64)
            return 201, [{
                "id": "11111111-1111-4111-8111-111111111111",
                **payload,
            }]

        with patch("api._shared._db_request", side_effect=fake_db_request):
            intent = create_payment_intent(
                "nayapay",
                Decimal("125.00"),
                "ORDER-123",
                "pay://merchant?amount=125.00",
            )
        self.assertEqual(intent["status"], "pending")
        self.assertEqual(intent["qr_payload"], "pay://merchant?amount=125.00")
        self.assertEqual(len(intent["status_token"]), 64)


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

    def test_catalog_profiles_use_optional_note_without_source_metadata(self):
        with patch.dict(os.environ, {}, clear=True):
            profiles = _load_bank_profiles()
        self.assertNotIn("source_urls", profiles["hbl"])
        self.assertNotIn("official_references", profiles["hbl"])
        self.assertIn("note", profiles["hbl"])
        self.assertNotIn("research_note", profiles["hbl"])
        self.assertNotIn("source_urls", profiles["nayapay"])
        self.assertEqual(set(profiles["nayapay"]["sender_allowlist"]), {"service@nayapay.com"})

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

    def test_bank_directory_exposes_configuration_status_without_source_urls(self):
        with patch.dict(os.environ, {}, clear=True):
            directory = bank_directory()
        by_id = {item["id"]: item for item in directory}
        self.assertIn("nayapay", by_id)
        self.assertTrue(by_id["nayapay"]["configured"])
        self.assertIn("hbl", by_id)
        self.assertFalse(by_id["hbl"]["configured"])
        self.assertEqual(by_id["hbl"]["status"], "needs_verified_email_sample")
        self.assertNotIn("source_urls", by_id["hbl"])
        self.assertIn("note", by_id["hbl"])


if __name__ == "__main__":
    unittest.main()
