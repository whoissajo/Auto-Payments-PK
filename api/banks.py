import logging
from http.server import BaseHTTPRequestHandler

from api._shared import ServiceError, bank_directory, write_json


class handler(BaseHTTPRequestHandler):
    server_version = "AutoPaymentsPK"
    sys_version = ""

    def do_GET(self):
        try:
            write_json(self, 200, {
                "banks": bank_directory(),
                "count": len(bank_directory()),
                "note": "configured means an adapter is present, not that the provider has an official API integration",
            })
        except ServiceError as exc:
            write_json(self, exc.status, {"error": exc.code, "message": exc.message})
        except Exception:
            logging.exception("Bank directory request failed")
            write_json(self, 500, {"error": "internal_error", "message": "Could not load the bank directory."})

    def do_POST(self):
        write_json(self, 405, {"error": "method_not_allowed", "message": "Use GET for this endpoint."})

    def log_message(self, format, *args):
        return
