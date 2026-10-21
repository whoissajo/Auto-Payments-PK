import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler

from api._shared import (
    ServiceError,
    claim_payment,
    find_recent_payment_by_amount,
    get_payment_intent,
    parse_utc_datetime,
    update_payment_intent,
    write_json,
)


def _cors_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    origin = (handler.headers.get("Origin") or "").strip()
    configured = os.environ.get("PAYMENT_ALLOWED_ORIGINS", "")
    allowed = {item.strip() for item in configured.split(",") if item.strip()}
    if origin and (origin in allowed or "*" in allowed):
        return {
            "Access-Control-Allow-Origin": "*" if "*" in allowed else origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "X-Payment-Token, Content-Type",
            "Access-Control-Max-Age": "300",
            "Vary": "Origin",
        }
    return {}


def _public_intent(intent: dict, now: datetime) -> dict:
    expires_at = parse_utc_datetime(intent["expires_at"])
    remaining = max(0, int((expires_at - now).total_seconds()))
    result = {
        "intent_id": intent["id"],
        "merchant_reference": intent["merchant_reference"],
        "bank": intent["bank"],
        "amount": f'{Decimal(str(intent["amount"])):.2f}',
        "currency": intent.get("currency", "PKR"),
        "status": intent["status"],
        "created_at": intent["created_at"],
        "expires_at": intent["expires_at"],
        "expires_in_seconds": remaining if intent["status"] == "pending" else 0,
    }
    if intent.get("verified_at"):
        result["verified_at"] = intent["verified_at"]
    if intent.get("transaction_id") and intent["status"] == "verified":
        result["transaction_id"] = intent["transaction_id"]
    if intent["status"] == "not_verified":
        result["message"] = "Payment was not verified within the five-minute payment window."
    return result


class handler(BaseHTTPRequestHandler):
    server_version = "AutoPaymentsPK"
    sys_version = ""

    def _respond(self, status: int, payload: dict) -> None:
        write_json(self, status, payload, _cors_headers(self))

    def do_OPTIONS(self):
        origin = (self.headers.get("Origin") or "").strip()
        headers = _cors_headers(self)
        if origin and not headers:
            write_json(self, 403, {"error": "origin_not_allowed", "message": "This checkout origin is not allowed."})
            return
        self._respond(200, {"status": "ok"})

    def do_GET(self):
        try:
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            intent_id = (query.get("id") or [""])[0].strip()
            token = (self.headers.get("X-Payment-Token") or "").strip()
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", intent_id):
                raise ServiceError(400, "invalid_request", "A valid payment intent id is required.")
            if not re.fullmatch(r"[0-9a-f]{64}", token):
                raise ServiceError(401, "invalid_payment_token", "A valid X-Payment-Token header is required.")

            intent = get_payment_intent(intent_id, token)
            if intent is None:
                raise ServiceError(404, "payment_intent_not_found", "The payment intent was not found.")

            if intent["status"] != "pending":
                self._respond(200, _public_intent(intent, datetime.now(timezone.utc)))
                return

            created_at = parse_utc_datetime(intent["created_at"])
            expires_at = parse_utc_datetime(intent["expires_at"])
            now = datetime.now(timezone.utc)

            # Inspect the inbox once per request, including after expiry, so a
            # delayed status request can still verify an email whose IMAP arrival
            # timestamp fell inside the original five-minute window.
            payment = find_recent_payment_by_amount(
                intent["bank"],
                Decimal(str(intent["amount"])),
                created_at,
                expires_at,
                intent["merchant_reference"],
            )
            if payment is not None:
                claim_payment(
                    intent["bank"],
                    payment,
                    Decimal(str(intent["amount"])),
                    intent["merchant_reference"],
                )
                changes = {
                    "status": "verified",
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                    "transaction_id": payment.transaction_id,
                    "sender": payment.sender,
                    "email_message_id": payment.message_id or None,
                }
                updated = update_payment_intent(intent_id, token, changes)
                if updated is None:
                    # Another request may have completed the same intent first.
                    intent = get_payment_intent(intent_id, token) or intent
                else:
                    intent = updated
            elif now >= expires_at:
                updated = update_payment_intent(
                    intent_id,
                    token,
                    {"status": "not_verified"},
                )
                if updated is None:
                    intent = get_payment_intent(intent_id, token) or intent
                else:
                    intent = updated
            else:
                # Refresh after scanning in case another concurrent request won.
                intent = get_payment_intent(intent_id, token) or intent

            self._respond(200, _public_intent(intent, datetime.now(timezone.utc)))
        except ServiceError as exc:
            self._respond(exc.status, {"error": exc.code, "message": exc.message})
        except Exception:
            logging.exception("Payment status check failed")
            self._respond(500, {
                "error": "internal_error",
                "message": "Could not check the payment status. Check redacted server logs.",
            })

    def do_POST(self):
        self._respond(405, {"error": "method_not_allowed", "message": "Use GET to check a payment intent."})

    def log_message(self, format, *args):
        # Do not log the token header or payment metadata.
        return
