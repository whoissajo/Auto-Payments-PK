"""Shared validation, provider parsing and Supabase claim logic for Auto-Payments-PK."""

from __future__ import annotations

import email
import html
import imaplib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Any


VERSION = "0.1.0"
CENT = Decimal("0.01")
MAX_BODY_BYTES = 16 * 1024
MAX_EMAIL_BODY_CHARS = 100_000

BANK_DIRECTORY = [
    ("albaraka", "Al Baraka Bank Pakistan"),
    ("allied-bank", "Allied Bank"),
    ("askari-bank", "Askari Bank"),
    ("bank-alfalah", "Bank Alfalah"),
    ("bank-al-habib", "Bank AL Habib"),
    ("bank-islamic", "BankIslami"),
    ("bank-of-china", "Bank of China Pakistan"),
    ("bank-of-khyber", "The Bank of Khyber"),
    ("bank-of-punjab", "The Bank of Punjab"),
    ("bank-makramah", "Bank Makramah"),
    ("citibank", "Citibank Pakistan"),
    ("deutsche-bank", "Deutsche Bank Pakistan"),
    ("dubai-islamic", "Dubai Islamic Bank Pakistan"),
    ("faysal-bank", "Faysal Bank"),
    ("first-women-bank", "First Women Bank"),
    ("hbl", "Habib Bank Limited"),
    ("habib-metro", "Habib Metropolitan Bank"),
    ("icbc-pakistan", "ICBC Pakistan"),
    ("js-bank", "JS Bank"),
    ("mcb", "MCB Bank"),
    ("mcb-islamic", "MCB Islamic Bank"),
    ("meezan-bank", "Meezan Bank"),
    ("national-bank", "National Bank of Pakistan"),
    ("nbp", "National Bank of Pakistan"),
    ("samba-bank", "Samba Bank"),
    ("sindh-bank", "Sindh Bank"),
    ("soneri-bank", "Soneri Bank"),
    ("standard-chartered", "Standard Chartered Pakistan"),
    ("ubl", "United Bank Limited"),
    ("zarai-taraqiati", "Zarai Taraqiati Bank"),
    ("easypaisa", "Easypaisa"),
    ("jazzcash", "JazzCash"),
    ("nayapay", "NayaPay"),
    ("sadapay", "SadaPay"),
    ("zindigi", "Zindigi"),
]

NAYAPAY_ADAPTER = {
    "name": "NayaPay",
    "sender_allowlist": ["service@nayapay.com"],
    "transaction_id_regex": (
        r"Transaction\s+ID\s*[:\-]?\s*([A-Z0-9]{10,})"
    ),
    "amount_regex": (
        r"Amount\s+Received\s*(?:[:\-]\s*)?(?:Rs\.?|PKR)\s*"
        r"([\d,]+(?:\.\d{1,2})?)"
    ),
    "total_amount_regex": (
        r"Total\s+Amount\s*(?:[:\-]\s*)?(?:Rs\.?|PKR)\s*"
        r"([\d,]+(?:\.\d{1,2})?)"
    ),
}


class ServiceError(Exception):
    """An API-safe error with an HTTP status and stable error code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class ParsedPayment:
    transaction_id: str
    amount: Decimal
    total_amount: Decimal | None
    sender: str
    message_id: str


class TextOnlyHTML(HTMLParser):
    """Converts HTML email bodies into searchable plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)


def write_json(handler: Any, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("X-Frame-Options", "DENY")
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler: Any) -> dict[str, Any]:
    raw_length = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as exc:
        raise ServiceError(400, "invalid_request", "Invalid Content-Length header.") from exc

    if length < 1:
        raise ServiceError(400, "invalid_request", "A JSON request body is required.")
    if length > MAX_BODY_BYTES:
        raise ServiceError(413, "request_too_large", "Request body must be 16 KB or smaller.")

    raw = handler.rfile.read(length)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(400, "invalid_json", "Request body must be valid UTF-8 JSON.") from exc

    if not isinstance(body, dict):
        raise ServiceError(400, "invalid_request", "JSON body must be an object.")
    return body


def require_api_key(handler: Any) -> None:
    configured = os.environ.get("API_SECRET_KEY", "")
    if len(configured) < 32:
        raise ServiceError(
            503,
            "server_not_configured",
            "API_SECRET_KEY must be configured with at least 32 characters.",
        )

    auth = handler.headers.get("Authorization", "")
    supplied = ""
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if not supplied:
        supplied = handler.headers.get("X-API-Key", "").strip()

    import hmac

    if not supplied or not hmac.compare_digest(supplied, configured):
        raise ServiceError(401, "unauthorized", "A valid API key is required.")


def validate_identifier(value: Any, field: str, pattern: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ServiceError(400, "invalid_request", f"{field} must be a string.")
    value = value.strip()
    if not value or len(value) > max_length or not re.fullmatch(pattern, value):
        raise ServiceError(400, "invalid_request", f"{field} has an invalid format.")
    return value


def parse_money(value: Any, field: str = "amount") -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ServiceError(400, "invalid_request", f"{field} must be a positive PKR amount.")

    text = str(value).strip().replace(",", "")
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ServiceError(400, "invalid_request", f"{field} must be a valid decimal amount.") from exc

    if not amount.is_finite() or amount <= 0 or amount > Decimal("9999999999999999"):
        raise ServiceError(400, "invalid_request", f"{field} must be a positive PKR amount.")
    if amount.as_tuple().exponent < -2:
        raise ServiceError(400, "invalid_request", f"{field} can have at most two decimal places.")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def _load_bank_profiles() -> dict[str, dict[str, Any]]:
    """Load the committed catalog, then apply optional environment overrides.

    Profiles with enabled=false are catalog entries only; they are never used
    to verify payments. Exact senders and parsers must be tested per institution.
    """
    profiles: dict[str, dict[str, Any]] = {
        "nayapay": {
            **NAYAPAY_ADAPTER,
            "enabled": True,
            "status": "configured",
            "institution_type": "emi",
        }
    }

    catalog_path = Path(__file__).resolve().parent.parent / "BANK_ADAPTERS_JSON.json"
    try:
        with catalog_path.open("r", encoding="utf-8") as catalog_file:
            catalog = json.load(catalog_file)
    except FileNotFoundError:
        # Keep the built-in, tested NayaPay adapter as a fallback for local use.
        catalog = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ServiceError(
            503,
            "adapter_configuration_invalid",
            "BANK_ADAPTERS_JSON.json could not be read or is not valid JSON.",
        ) from exc

    if not isinstance(catalog, dict):
        raise ServiceError(503, "adapter_configuration_invalid", "BANK_ADAPTERS_JSON.json must contain a JSON object.")

    for slug, config in catalog.items():
        if slug in ("_meta", "$meta"):
            continue
        if not isinstance(config, dict):
            raise ServiceError(503, "adapter_configuration_invalid", f"Bank profile {slug} must be an object.")
        profiles[slug] = config

    # Environment definitions intentionally override the committed profile for
    # that slug. Useful for private/test overrides, but secrets never belong here.
    raw = os.environ.get("BANK_ADAPTERS_JSON", "").strip()
    if raw:
        try:
            custom = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ServiceError(
                503,
                "adapter_configuration_invalid",
                "BANK_ADAPTERS_JSON is not valid JSON.",
            ) from exc

        if not isinstance(custom, dict):
            raise ServiceError(503, "adapter_configuration_invalid", "BANK_ADAPTERS_JSON must be a JSON object.")

        for slug, config in custom.items():
            if slug in ("_meta", "$meta"):
                continue
            profiles[slug] = config

    return profiles


def load_adapters(profiles: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Return only enabled and fully validated email adapters."""
    if profiles is None:
        profiles = _load_bank_profiles()

    adapters: dict[str, dict[str, Any]] = {}
    for slug, config in profiles.items():
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,39}", slug):
            raise ServiceError(503, "adapter_configuration_invalid", f"Bank adapter has an invalid slug: {slug!r}.")
        if not isinstance(config, dict):
            raise ServiceError(503, "adapter_configuration_invalid", f"Adapter {slug} must be an object.")

        name = config.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 150:
            raise ServiceError(503, "adapter_configuration_invalid", f"Adapter {slug} requires a valid name.")

        enabled = config.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ServiceError(503, "adapter_configuration_invalid", f"Adapter {slug} enabled must be true or false.")
        if not enabled:
            # A nonempty profile is not an active integration. Do not guess senders,
            # regexes, or transaction formats for banks without verified fixtures.
            continue

        senders = config.get("sender_allowlist")
        tx_regex = config.get("transaction_id_regex")
        amount_regex = config.get("amount_regex")
        total_regex = config.get("total_amount_regex")

        if not isinstance(senders, list) or not senders or any(not isinstance(sender, str) for sender in senders):
            raise ServiceError(
                503,
                "adapter_configuration_invalid",
                f"Enabled adapter {slug} requires an exact sender_allowlist.",
            )

        normalized_senders: list[str] = []
        for sender in senders:
            parsed = parseaddr(sender.strip())[1].lower()
            if not parsed or "@" not in parsed or parsed != sender.strip().lower():
                raise ServiceError(
                    503,
                    "adapter_configuration_invalid",
                    f"Adapter {slug} contains an invalid sender address.",
                )
            normalized_senders.append(parsed)
        if len(set(normalized_senders)) != len(normalized_senders):
            normalized_senders = list(dict.fromkeys(normalized_senders))

        for label, pattern in (("transaction_id_regex", tx_regex), ("amount_regex", amount_regex)):
            if not isinstance(pattern, str) or not pattern or len(pattern) > 1000:
                raise ServiceError(
                    503,
                    "adapter_configuration_invalid",
                    f"Enabled adapter {slug} requires {label}.",
                )
            try:
                compiled = re.compile(pattern, re.IGNORECASE | re.DOTALL)
                if compiled.groups < 1 and "value" not in compiled.groupindex:
                    raise ValueError("regex needs a capture group")
            except (re.error, ValueError) as exc:
                raise ServiceError(
                    503,
                    "adapter_configuration_invalid",
                    f"Adapter {slug} has an invalid {label}.",
                ) from exc

        if total_regex is not None:
            if not isinstance(total_regex, str) or not total_regex or len(total_regex) > 1000:
                raise ServiceError(
                    503,
                    "adapter_configuration_invalid",
                    f"Adapter {slug} has an invalid total_amount_regex.",
                )
            try:
                re.compile(total_regex, re.IGNORECASE | re.DOTALL)
            except re.error as exc:
                raise ServiceError(
                    503,
                    "adapter_configuration_invalid",
                    f"Adapter {slug} has an invalid total_amount_regex.",
                ) from exc

        adapters[slug] = {
            "name": name.strip(),
            "sender_allowlist": normalized_senders,
            "transaction_id_regex": tx_regex,
            "amount_regex": amount_regex,
            "total_amount_regex": total_regex,
            "status": "configured",
            "institution_type": config.get("institution_type"),
        }

    return adapters


def bank_directory() -> list[dict[str, Any]]:
    profiles = _load_bank_profiles()
    adapters = load_adapters(profiles)
    names = {slug: name for slug, name in BANK_DIRECTORY}

    result: list[dict[str, Any]] = []
    all_slugs = set(names) | set(profiles)
    for slug in sorted(all_slugs):
        if slug in ("_meta", "$meta"):
            continue
        profile = profiles.get(slug, {})
        adapter = adapters.get(slug)
        name = (adapter or profile).get("name") or names.get(slug)
        if not name:
            continue

        configured = adapter is not None
        status = "configured" if configured else profile.get("status", "needs_verified_email_sample")
        item: dict[str, Any] = {
            "id": slug,
            "name": name,
            "configured": configured,
            "status": status,
            "institution_type": profile.get("institution_type", "bank"),
        }
        if profile.get("alias_for"):
            item["alias_for"] = profile["alias_for"]
        if profile.get("source_urls"):
            item["source_urls"] = profile["source_urls"]
        if profile.get("research_note"):
            item["note"] = profile["research_note"]
        result.append(item)

    result.sort(key=lambda item: item["name"].casefold())
    return result


def regex_value(pattern: str | None, text: str) -> str | None:
    if not pattern:
        return None
    try:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    except re.error as exc:
        raise ServiceError(503, "adapter_configuration_invalid", "A configured adapter regex is invalid.") from exc
    if not match:
        return None
    if "value" in match.re.groupindex:
        return (match.group("value") or "").strip()
    if match.lastindex:
        return (match.group(1) or "").strip()
    return match.group(0).strip()


def parse_email_fields(body: str, adapter: dict[str, Any]) -> tuple[str | None, Decimal | None, Decimal | None]:
    body = body[:MAX_EMAIL_BODY_CHARS]
    tx_value = regex_value(adapter.get("transaction_id_regex"), body)
    amount_value = regex_value(adapter.get("amount_regex"), body)
    total_value = regex_value(adapter.get("total_amount_regex"), body)

    txid = None
    if tx_value:
        match = re.search(r"[A-Z0-9][A-Z0-9._/-]{5,127}", tx_value, re.IGNORECASE)
        if match:
            txid = match.group(0).upper()

    amount = None
    if amount_value:
        cleaned = re.sub(r"(?i)\b(?:PKR|Rs\.?)\b", "", amount_value).strip().replace(",", "")
        try:
            parsed = Decimal(cleaned)
            if parsed.is_finite() and parsed >= 0 and parsed <= Decimal("9999999999999999"):
                amount = parsed.quantize(CENT, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            amount = None

    total_amount = None
    if total_value:
        cleaned = re.sub(r"(?i)\b(?:PKR|Rs\.?)\b", "", total_value).strip().replace(",", "")
        try:
            parsed = Decimal(cleaned)
            if parsed.is_finite() and parsed >= 0 and parsed <= Decimal("9999999999999999"):
                total_amount = parsed.quantize(CENT, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            total_amount = None

    return txid, amount, total_amount


def decode_message_bodies(message: email.message.Message) -> list[str]:
    plain: list[str] = []
    rich: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]

    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type().lower()
        if content_type not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")
        if content_type == "text/html":
            parser = TextOnlyHTML()
            try:
                parser.feed(decoded)
                decoded = parser.text()
            except Exception:
                decoded = re.sub(r"(?is)<[^>]*>", " ", html.unescape(decoded))
        decoded = html.unescape(decoded).replace("\xa0", " ")
        decoded = re.sub(r"[ \t]+", " ", decoded)
        if decoded.strip():
            (plain if content_type == "text/plain" else rich).append(decoded.strip())

    return plain + rich


def _require_mail_env() -> tuple[str, str, str, int, int, str]:
    username = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    host = os.environ.get("IMAP_HOST", "imap.gmail.com").strip()
    folder = os.environ.get("IMAP_FOLDER", "INBOX").strip() or "INBOX"
    try:
        port = int(os.environ.get("IMAP_PORT", "993"))
        timeout = int(os.environ.get("IMAP_TIMEOUT_SECONDS", "8"))
    except ValueError as exc:
        raise ServiceError(503, "server_not_configured", "IMAP_PORT and IMAP_TIMEOUT_SECONDS must be integers.") from exc
    if not username or not password:
        raise ServiceError(
            503,
            "server_not_configured",
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be configured.",
        )
    if not host or port < 1 or port > 65535 or timeout < 1 or timeout > 30:
        raise ServiceError(503, "server_not_configured", "IMAP configuration is invalid.")
    return username, password, host, port, timeout, folder


def find_payment(bank: str, transaction_id: str, amount_expected: Decimal) -> ParsedPayment | None:
    adapters = load_adapters()
    adapter = adapters.get(bank)
    if adapter is None:
        raise ServiceError(
            422,
            "adapter_not_configured",
            "This bank has no configured, tested notification adapter yet.",
        )

    username, password, host, port, timeout, folder = _require_mail_env()
    imap = None
    try:
        imap = imaplib.IMAP4_SSL(host, port, timeout=timeout, ssl_context=ssl.create_default_context())
        imap.login(username, password)
        status, _ = imap.select(folder, readonly=True)
        if status != "OK":
            raise ServiceError(502, "provider_unavailable", "Could not open the configured alert mailbox.")

        uids: set[bytes] = set()
        for sender in adapter["sender_allowlist"]:
            status, data = imap.uid(
                "SEARCH",
                None,
                "FROM",
                f'"{sender}"',
                "TEXT",
                f'"{transaction_id}"',
            )
            if status == "OK" and data and data[0]:
                uids.update(data[0].split())

        ordered_uids = sorted(uids, key=lambda uid: int(uid))[-20:]
        amount_mismatch: Decimal | None = None
        total_mismatch = False

        for uid in reversed(ordered_uids):
            status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not data:
                continue
            raw_email = next(
                (item[1] for item in data if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes)),
                None,
            )
            if not raw_email:
                continue

            message = BytesParser(policy=policy.default).parsebytes(raw_email)
            sender = parseaddr(message.get("From", ""))[1].strip().lower()
            if sender not in adapter["sender_allowlist"]:
                continue

            bodies = decode_message_bodies(message)
            parsed_fields = None
            for body in bodies:
                txid, found_amount, found_total = parse_email_fields(body, adapter)
                if txid == transaction_id.upper() and found_amount is not None:
                    parsed_fields = (txid, found_amount, found_total)
                    break
            if parsed_fields is None:
                continue

            txid, found_amount, found_total = parsed_fields
            if found_total is not None and found_total != found_amount:
                total_mismatch = True
                continue
            if found_amount != amount_expected:
                amount_mismatch = found_amount
                continue

            msg_id = (message.get("Message-ID", "") or "").strip()
            return ParsedPayment(
                transaction_id=txid,
                amount=found_amount,
                total_amount=found_total,
                sender=sender,
                message_id=msg_id[:255],
            )

        if total_mismatch:
            raise ServiceError(
                409,
                "total_amount_mismatch",
                "A matching transaction notification had inconsistent total and received amounts.",
            )
        if amount_mismatch is not None:
            raise ServiceError(
                409,
                "amount_mismatch",
                "A matching transaction notification was found, but its amount does not match.",
            )
        return None
    except ServiceError:
        raise
    except (imaplib.IMAP4.error, OSError, ssl.SSLError, TimeoutError) as exc:
        raise ServiceError(
            502,
            "provider_unavailable",
            "Could not securely read the configured alert mailbox. Check IMAP settings and credentials.",
        ) from exc
    finally:
        if imap is not None:
            try:
                imap.close()
            except Exception:
                pass
            try:
                imap.logout()
            except Exception:
                pass


def _supabase_config() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key or not url.startswith("https://"):
        raise ServiceError(
            503,
            "server_not_configured",
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured.",
        )
    return url, key


def _db_request(method: str, query: dict[str, str] | None = None, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    base_url, key = _supabase_config()
    url = base_url + "/rest/v1/verified_payments"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        # Supabase requires the API key header. Legacy service_role keys are JWTs
        # and also work as a Bearer token. Modern sb_secret_* keys are not JWTs,
        # so do not send them as Authorization: Bearer or PostgREST may reject them.
        "apikey": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }
    if key.startswith("eyJ"):
        headers["Authorization"] = "Bearer " + key

    request = urllib.request.Request(
        url=url,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = response.read(256_000).decode("utf-8", errors="replace")
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read(16_000).decode("utf-8", errors="replace")
        if exc.code == 409:
            return 409, body
        raise ServiceError(
            502,
            "provider_unavailable",
            "Supabase rejected the request. Check SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, Data API access, and the table grants in database/schema.sql.",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ServiceError(
            502,
            "provider_unavailable",
            "Could not reach payment verification storage.",
        ) from exc


def _find_claim(bank: str, transaction_id: str | None = None, merchant_reference: str | None = None) -> dict[str, Any] | None:
    filters: dict[str, str] = {
        "select": "bank,transaction_id,merchant_reference,amount,currency,verified_at",
        "limit": "1",
    }
    if transaction_id is not None:
        filters["bank"] = "eq." + bank
        filters["transaction_id"] = "eq." + transaction_id
    if merchant_reference is not None:
        filters["merchant_reference"] = "eq." + merchant_reference
    status, data = _db_request("GET", filters)
    if status != 200 or not isinstance(data, list):
        raise ServiceError(502, "provider_unavailable", "Could not read payment claim records.")
    return data[0] if data else None


def claim_payment(bank: str, payment: ParsedPayment, expected: Decimal, merchant_reference: str) -> dict[str, Any]:
    existing_tx = _find_claim(bank, transaction_id=payment.transaction_id)
    if existing_tx:
        if (
            existing_tx.get("merchant_reference") == merchant_reference
            and Decimal(str(existing_tx.get("amount"))) == expected
        ):
            return {
                "status": "already_verified",
                "bank": bank,
                "transaction_id": payment.transaction_id,
                "amount": f"{expected:.2f}",
                "currency": "PKR",
                "merchant_reference": merchant_reference,
            }
        raise ServiceError(
            409,
            "duplicate_transaction",
            "This transaction has already been claimed for a different order or amount.",
        )

    existing_order = _find_claim(bank, merchant_reference=merchant_reference)
    if existing_order:
        raise ServiceError(
            409,
            "order_already_paid",
            "This merchant reference already has a verified payment.",
        )

    row = {
        "bank": bank,
        "transaction_id": payment.transaction_id,
        "merchant_reference": merchant_reference,
        "amount": f"{expected:.2f}",
        "currency": "PKR",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "sender": payment.sender,
        "email_message_id": payment.message_id or None,
    }
    status, data = _db_request("POST", payload=row)
    if status == 201:
        return {
            "status": "verified",
            "bank": bank,
            "transaction_id": payment.transaction_id,
            "amount": f"{expected:.2f}",
            "currency": "PKR",
            "merchant_reference": merchant_reference,
        }

    if status == 409:
        # A concurrent request may have inserted the same key. Read-after-conflict
        # makes same-order retries idempotent while still preventing reuse.
        existing_tx = _find_claim(bank, transaction_id=payment.transaction_id)
        if (
            existing_tx
            and existing_tx.get("merchant_reference") == merchant_reference
            and Decimal(str(existing_tx.get("amount"))) == expected
        ):
            return {
                "status": "already_verified",
                "bank": bank,
                "transaction_id": payment.transaction_id,
                "amount": f"{expected:.2f}",
                "currency": "PKR",
                "merchant_reference": merchant_reference,
            }
        existing_order = _find_claim(bank, merchant_reference=merchant_reference)
        if existing_order:
            raise ServiceError(
                409,
                "order_already_paid",
                "This merchant reference already has a verified payment.",
            )
        raise ServiceError(
            409,
            "duplicate_transaction",
            "This transaction has already been claimed for a different order or amount.",
        )

    raise ServiceError(502, "provider_unavailable", "Could not safely claim the verified payment.")
