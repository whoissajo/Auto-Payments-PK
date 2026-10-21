from http.server import BaseHTTPRequestHandler

from api._shared import VERSION, write_json


class handler(BaseHTTPRequestHandler):
    server_version = "AutoPaymentsPK"
    sys_version = ""

    def do_GET(self):
        write_json(self, 200, {
            "status": "ok",
            "service": "Auto-Payments-PK",
            "version": VERSION,
        })

    def do_POST(self):
        write_json(self, 405, {"error": "method_not_allowed", "message": "Use GET for this endpoint."})

    def log_message(self, format, *args):
        # Do not log headers or request bodies that might contain sensitive data.
        return
