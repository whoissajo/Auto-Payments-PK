import logging
from http.server import BaseHTTPRequestHandler

from api._shared import (
    ServiceError,
    _supabase_config,
    claim_payment,
    find_payment,
    load_adapters,
    parse_money,
    read_json,
    require_api_key,
    validate_identifier,
    write_json,
)


class handler(BaseHTTPRequestHandler):
    server_version = "AutoPaymentsPK"
    sys_version = ""

    def do_POST(self):
        try:
            require_api_key(self)
            body = read_json(self)

            bank = validate_identifier(
                body.get("bank"),
                "bank",
                r"[a-z0-9][a-z0-9-]{1,39}",
                40,
            )
            transaction_id = validate_identifier(
                body.get("transaction_id"),
                "transaction_id",
                r"[A-Za-z0-9][A-Za-z0-9._/-]{5,127}",
                128,
            ).upper()
            merchant_reference = validate_identifier(
                body.get("merchant_reference"),
                "merchant_reference",
                r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}",
                128,
            )
            amount = parse_money(body.get("amount"))

            adapters = load_adapters()
            if bank not in adapters:
                raise ServiceError(
                    422,
                    "adapter_not_configured",
                    "This bank has no configured, tested notification adapter yet.",
                )

            # Fail early if durable one-time payment claims are not configured.
            _supabase_config()

            payment = find_payment(bank, transaction_id, amount)
            if payment is None:
                write_json(self, 404, {
                    "status": "not_found",
                    "message": "No matching trusted payment notification was found. This does not prove that no payment occurred.",
                    "bank": bank,
                    "transaction_id": transaction_id,
                    "amount": f"{amount:.2f}",
                    "currency": "PKR",
                    "merchant_reference": merchant_reference,
                })
                return

            result = claim_payment(bank, payment, amount, merchant_reference)
            write_json(self, 200, result)

        except ServiceError as exc:
            write_json(self, exc.status, {"error": exc.code, "message": exc.message})
        except Exception:
            logging.exception("Payment verification failed")
            write_json(self, 500, {
                "error": "internal_error",
                "message": "The verification request could not be completed. Check server logs using a redacted test case.",
            })

    def do_GET(self):
        write_json(self, 405, {"error": "method_not_allowed", "message": "Use POST with a JSON body for payment verification."})

    def do_OPTIONS(self):
        write_json(self, 405, {"error": "method_not_allowed", "message": "Browser CORS is disabled. Call this API from a trusted backend."})

    def log_message(self, format, *args):
        # Avoid request logging: query strings can contain transaction metadata.
        return
