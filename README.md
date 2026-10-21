# Auto-Payments-PK

Open-source, Vercel-hosted payment-verification API for Pakistani bank and wallet transfer alerts.

Auto-Payments-PK is designed to **verify an incoming transfer against a trusted bank/wallet notification**, the exact PKR amount, and a merchant order reference. It does not initiate transfers, collect banking passwords, bypass bank security, or claim to be an official bank API.

> [!WARNING]
> This repository is an independent community project, not affiliated with the State Bank of Pakistan, any bank, wallet, Raast, or payment service provider. Email/SMS notifications can be delayed, changed, spoofed, or incomplete. Do not treat this project as a certified payment gateway or the sole proof of settlement for important payments. Reconcile transactions against official bank records and obtain the approvals required for your business.

## What works now

- Python serverless functions that deploy on Vercel.
- An implemented NayaPay email-alert adapter based on the public example in the linked project.
- Strict sender allowlisting, transaction-ID matching, exact two-decimal PKR comparison, and optional total-amount checks.
- A committed `BANK_ADAPTERS_JSON.json` catalogue of Pakistani commercial banks, digital banks, microfinance banks, EMIs, and wallet brands, with an explicit per-provider activation status.
- A configurable adapter format for adding further bank and wallet notification templates.
- API-key authentication for verification requests.
- Supabase/Postgres-backed one-time payment claims to prevent a single transfer from being reused for different orders.
- Public health and bank-directory endpoints, tests, a database schema, and an environment-variable template.

**Important:** `BANK_ADAPTERS_JSON.json` is the committed bank/wallet catalogue and is bundled with the Vercel functions. It currently contains 54 profiles, but only NayaPay is enabled because the repository includes a working example for its alert email. Other profiles are present for discovery and transparency, but remain disabled until the exact sender address and transaction-email fields can be confirmed from a genuine, redacted message. SBP's general alert policy and a bank's public contact email do not prove the transaction-alert sender. Guessing them could incorrectly confirm a payment. Public sources are recorded in each profile's `source_urls` field.

## Architecture

    Client or merchant server
        |
        | POST /api/verify (API key + order reference)
        v
    Vercel Python Function
        |
        +-- Authenticates request
        +-- Checks configured bank adapter and trusted sender
        +-- Searches the configured mailbox for the transaction ID
        +-- Validates transaction ID and amount from the message
        +-- Atomically claims the payment in Supabase/Postgres
        v
    JSON verification result

The service reads email notifications over IMAP. For the built-in adapter, configure a dedicated mailbox that receives NayaPay payment alerts. Use an app password where supported; never use your normal account password. For wider production use, consider implementing the provider's official API or a verified webhook where the bank/provider offers one.

## Deploy to Vercel

1. Fork this repository and import it into Vercel.
2. Keep the project root at the repository root. No build command is required for the Python API.
3. Create a Supabase project, then run the SQL file at database/schema.sql in its SQL Editor.
4. In Vercel **Project → Settings → Environment Variables**, add the variables below. Set them for the Production environment and redeploy.
5. Use a long random API_SECRET_KEY, a mailbox dedicated to payment alerts, and set the Supabase server-side key in the exact environment variable `SUPABASE_SERVICE_ROLE_KEY`. It may contain the legacy JWT-style `service_role` key or a modern `sb_secret_...` secret key. Keep it only in Vercel server-side environment variables.
6. After deployment, test https://YOUR-DOMAIN/api/health and https://YOUR-DOMAIN/api/banks. Use the verification endpoint only after sending a small test transfer and checking the notification parser against the actual bank email.

### Required environment variables

| Variable | Purpose |
| --- | --- |
| API_SECRET_KEY | Secret required by POST /api/verify (use at least 32 random characters). |
| GMAIL_ADDRESS | Mailbox login that receives the transaction alert. |
| GMAIL_APP_PASSWORD | Mailbox app password, not the ordinary email password. |
| SUPABASE_URL | Project URL, for example https://project-ref.supabase.co. |
| SUPABASE_SERVICE_ROLE_KEY | Server-only privileged Supabase key: legacy JWT-style `service_role` or modern `sb_secret_...`. Never expose it in browser code. |

### Optional environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| IMAP_HOST | imap.gmail.com | IMAP server host. |
| IMAP_PORT | 993 | TLS IMAP port. |
| IMAP_FOLDER | INBOX | Mail folder to search. |
| IMAP_TIMEOUT_SECONDS | 8 | Socket timeout for the mailbox. |
| BANK_ADAPTERS_JSON | unset | Optional JSON object that overrides selected entries from `BANK_ADAPTERS_JSON.json` for private/test adapters. |

Copy .env.example to a local .env for local testing. Never commit .env or real credentials. Vercel environment variable changes apply to new deployments, so redeploy after updates.

## Bank catalogue and adding adapters

The repository's `BANK_ADAPTERS_JSON.json` file is the source-controlled catalogue. It includes bank and wallet names, institution type, official source links, current activation state, and notes on what remains to be verified. Vercel is configured to bundle this file with both `/api/banks` and `/api/verify`; you do not need to paste the entire catalogue into a Vercel environment variable.

The `GET /api/banks` response tells you which entries are configured. At this point, only NayaPay is enabled. A bank listed with `status: "needs_verified_email_sample"` is not an active integration.

To activate a bank or wallet:

1. Make a small test credit to the receiving account and save the original alert email in a private location. Redact account numbers, names, balances, links, and other personal information before sharing a test fixture.
2. Confirm the exact `From` email address from multiple genuine alerts and add only that exact address to `sender_allowlist`. Do not use a broad domain wildcard or a public customer-support email.
3. Write transaction-ID and amount regexes against that institution's actual email body. Check that the amount regex cannot accidentally capture the balance, fee, or a later total field.
4. Add a synthetic test fixture and test for valid transactions, missing fields, amount mismatches, malformed values, and similar labels in the same email.
5. Only then set `enabled` to `true` and `status` to `configured`. Deploy and compare a test transfer against the bank's official statement.

Use `BANK_ADAPTERS_JSON` only if you need an environment-specific override. It must be a JSON object keyed by adapter slug. Environment entries replace the same slug from the file; they do not need to repeat all other catalogue entries. Never put credentials in the JSON file or in a client-side environment variable.

Research references in the catalogue include the [SBP digital-transaction-alert circular](https://www.sbp.org.pk/circulars/pspod-circular-letter-no-01-of-2022), [SBP's commercial-bank directory](https://www.sbp.org.pk/sbp-regulated-institutions/commercial-banks), [SBP's microfinance-bank directory](https://www.sbp.org.pk/sbp-regulated-institutions/microfinance-banks), and the [authorized EMI list](https://www.sbp.org.pk/ps/PDF/List-of-EMIs.pdf). These sources identify institutions or the general alert policy; they do not disclose all transaction-email senders and message layouts.

## API

All responses are JSON. The API deliberately does not support browser CORS by default; call it from a trusted backend and keep the API key off client-side websites and mobile apps.

### GET /api/health

Returns basic liveness information. It does not perform a live bank or database check and should not be used as proof that all integrations are configured.

### GET /api/banks

Returns the bank/wallet directory and whether an adapter is configured. The built-in NayaPay adapter is configured by default; custom adapters are enabled by BANK_ADAPTERS_JSON.

### POST /api/verify

Required header:

    Authorization: Bearer YOUR_API_SECRET_KEY
    Content-Type: application/json

Request body:

    {
      "bank": "nayapay",
      "transaction_id": "SAMPLE_TRANSACTION_ID_123456",
      "amount": "150.00",
      "merchant_reference": "ORDER-2026-000123"
    }

Rules:
- bank is a lowercase slug returned by /api/banks.
- transaction_id is the exact transaction/reference ID shown by the provider.
- amount is the expected amount in PKR. It is compared to two decimal places without floating-point arithmetic.
- merchant_reference is your unique order/payment reference. Reusing a verified transaction for a different order is rejected.
- Never trust the caller's amount alone; successful verification requires a matching notification from the allowlisted sender.

Example with curl:

    curl -X POST "https://YOUR-DOMAIN/api/verify" \
      -H "Authorization: Bearer YOUR_API_SECRET_KEY" \
      -H "Content-Type: application/json" \
      -d '{"bank":"nayapay","transaction_id":"SAMPLE_TRANSACTION_ID_123456","amount":"150.00","merchant_reference":"ORDER-2026-000123"}'

Possible responses include:

- 200 verified — the notification matched and the payment was claimed for that order.
- 200 already_verified — the same transaction was already verified for the same order and amount (safe idempotent retry).
- 404 not_found — no matching trusted notification was found.
- 409 duplicate_transaction — that transaction was already claimed for another order.
- 409 amount_mismatch — a notification was found but its amount does not match.
- 409 total_amount_mismatch — the notification's received amount and total amount disagree.
- 409 order_already_paid — the order reference already belongs to a verified transaction.
- 400 invalid_request — request fields are missing or invalid.
- 401 unauthorized — API key is missing or incorrect.
- 422 adapter_not_configured — the selected bank needs an adapter.
- 502 provider_unavailable — IMAP or the persistence service failed.
- 503 server_not_configured — a required server-side environment variable is missing.

Do not interpret not_found as proof the customer did not pay. Alert delivery and indexing can take time; retry conservatively and reconcile with bank records.

## Adding another bank or wallet

Use BANK_ADAPTERS_JSON to register a provider whose notification format you have actually tested. The value must be a JSON object keyed by bank slug. Each adapter needs a display name, an exact sender allowlist, and regexes with a capture group for the transaction ID and amount. An optional total-amount regex adds another consistency check.

Example (replace the sample sender and patterns with values verified against that provider's real email template):

    {
      "my-bank": {
        "name": "My Bank",
        "sender_allowlist": ["alerts@example-bank.test"],
        "transaction_id_regex": "(?:Transaction ID|Reference No)\\s*[:#-]?\\s*([A-Z0-9-]{8,40})",
        "amount_regex": "(?:Amount Credited|Transaction Amount)\\s*[:#-]?\\s*(?:PKR|Rs\\.? )?([0-9,]+(?:\\.[0-9]{1,2})?)",
        "total_amount_regex": "(?:Total Amount)\\s*[:#-]?\\s*(?:PKR|Rs\\.? )?([0-9,]+(?:\\.[0-9]{1,2})?)"
      }
    }

Set the JSON as a single-line environment variable in Vercel. Escape backslashes as required by JSON. The service only accepts messages from the exact allowlisted sender addresses. Do not use a broad domain wildcard. If a provider changes its template, update the regexes and rerun tests before deploying.

Do not blindly copy regexes from this example: email templates differ, and a loose amount pattern can accidentally match a balance or fee. If the bank does not send an email with a stable transaction identifier and an unambiguous amount, use an official API/webhook or do not mark it automated.

## Security and operational notes

- Never put bank credentials, email app passwords, API keys, or the Supabase service-role key in source code.
- If a secret has ever been committed publicly, revoke/rotate it and remove it from Git history.
- The database enforces unique transaction claims and unique merchant references. Keep the schema constraint in place.
- Only the server uses `SUPABASE_SERVICE_ROLE_KEY`; do not put it in a client application. Legacy JWT-style keys are sent as a Bearer token; modern `sb_secret_...` keys are sent using the `apikey` header only.
- Verification is based on incoming email evidence, not direct account access. Never ask customers for OTPs, PINs, internet-banking passwords, or app credentials.
- Use official bank/PSP merchant onboarding and Raast P2M integration for legitimate commercial payment acceptance. Requirements vary by provider and business model.
- This project does not handle refunds, chargebacks, reversals, account balance checks, or payment initiation.
- Use fake/redacted test messages in issues and pull requests. Do not upload full bank statements or emails containing personal data.
- The API key is shared-secret authentication, not a full merchant-identity system. For a multi-merchant service, add per-merchant credentials, rate limits, audit logging, monitoring, and access controls.

## Local tests

Requires Python 3.12 or newer. Run:

    python -m unittest discover -s tests -v

The tests use synthetic, non-sensitive email text and do not connect to a bank, mailbox, or Supabase.

## Contributing

Contributions are welcome. Add synthetic fixtures for message templates, keep sender allowlists exact, add tests for valid/malformed amounts and transaction IDs, and update this README when an adapter is added. Never include live credentials or real customer data.

## License

MIT. See LICENSE.
