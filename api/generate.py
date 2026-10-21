import base64
import logging
import os
import urllib.parse
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler

import qrcode

from api._shared import (
    ServiceError,
    create_payment_intent,
    load_adapters,
    parse_money,
    require_api_key,
    validate_identifier,
    write_json,
)


def build_qr_payload(template: str, amount: Decimal, merchant_reference: str) -> str:
    """Render a provider-configured dynamic payment URI."""
    if "{amount}" not in template:
        raise ServiceError(
            503,
            "qr_not_configured",
            "PAYMENT_QR_PAYLOAD_TEMPLATE must include the {amount} placeholder.",
        )
    result = template.replace("{amount}", urllib.parse.quote(f"{amount:.2f}", safe=""))
    result = result.replace("{reference}", urllib.parse.quote(merchant_reference, safe=""))
    if not result.strip() or len(result) > 2048:
        raise ServiceError(503, "qr_not_configured", "The configured payment QR payload is empty or too long.")
    return result


def qr_svg_data_url(payload: str) -> str:
    """Generate a dependency-light SVG QR image (no Pillow needed)."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    commands = [
        f"M{x},{y}h1v1h-1z"
        for y, row in enumerate(matrix)
        for x, dark in enumerate(row)
        if dark
    ]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
        f'<path d="{"".join(commands)}" fill="#000"/></svg>'
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return "data:image/svg+xml;base64," + encoded


class handler(BaseHTTPRequestHandler):
    server_version = "AutoPaymentsPK"
    sys_version = ""

    def do_POST(self):
        try:
            require_api_key(self)
            from api._shared import read_json
            body = read_json(self)

            bank = validate_identifier(body.get("bank"), "bank", r"[a-z0-9][a-z0-9-]{1,39}", 40)
            amount = parse_money(body.get("amount"))
            supplied_reference = body.get("merchant_reference")
            if supplied_reference is None or supplied_reference == "":
                merchant_reference = "PAY-" + uuid.uuid4().hex
            else:
                merchant_reference = validate_identifier(
                    supplied_reference,
                    "merchant_reference",
                    r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}",
                    128,
                )

            if bank not in load_adapters():
                raise ServiceError(
                    422,
                    "adapter_not_configured",
                    "This bank has no configured, tested notification adapter yet.",
                )

            template = os.environ.get("PAYMENT_QR_PAYLOAD_TEMPLATE", "").strip()
            if not template:
                raise ServiceError(
                    503,
                    "qr_not_configured",
                    "Configure PAYMENT_QR_PAYLOAD_TEMPLATE using the payment URI format approved by your bank or payment provider.",
                )

            supabase_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
            publishable_key = (
                os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
                or os.environ.get("SUPABASE_ANON_KEY", "").strip()
            )
            if not supabase_url.startswith("https://") or not publishable_key:
                raise ServiceError(
                    503,
                    "realtime_not_configured",
                    "SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY (or legacy SUPABASE_ANON_KEY) are required for WebSocket status updates.",
                )

            qr_payload = build_qr_payload(template, amount, merchant_reference)
            qr_code = qr_svg_data_url(qr_payload)
            intent = create_payment_intent(bank, amount, merchant_reference, qr_payload)

            write_json(self, 201, {
                "status": "pending",
                "intent_id": intent["id"],
                "merchant_reference": merchant_reference,
                "bank": bank,
                "amount": f"{amount:.2f}",
                "currency": "PKR",
                "qr_code": qr_code,
                "qr_format": "svg-data-url",
                "expires_at": intent["expires_at"],
                "expires_in_seconds": 300,
                "status_url": "/api/status?id=" + urllib.parse.quote(intent["id"], safe=""),
                "status_token": intent["status_token"],
                "realtime": {
                    "provider": "supabase",
                    "url": supabase_url,
                    "publishable_key": publishable_key,
                    "channel": "payment-intent:" + intent["status_token"],
                    "event": "PAYMENT_STATUS",
                },
            })
        except ServiceError as exc:
            write_json(self, exc.status, {"error": exc.code, "message": exc.message})
        except Exception:
            logging.exception("Payment QR generation failed")
            write_json(self, 500, {
                "error": "internal_error",
                "message": "The payment QR could not be generated. Check configuration and server logs.",
            })

    def do_GET(self):
        write_json(self, 405, {"error": "method_not_allowed", "message": "Use POST to generate a payment QR."})

    def do_OPTIONS(self):
        write_json(self, 405, {"error": "method_not_allowed", "message": "Call this endpoint from a trusted backend."})

    def log_message(self, format, *args):
        return
