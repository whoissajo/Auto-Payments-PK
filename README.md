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

**Important:** `BANK_ADAPTERS_JSON.json` is the committed bank/wallet catalogue and is bundled with the Vercel functions. It currently contains 54 profiles, but only NayaPay is enabled because the repository includes a working example for its alert email. Other profiles remain disabled until the exact sender address and transaction-email fields can be confirmed from genuine, redacted messages and covered by tests. No `source_urls` or `official_references` fields are required in a profile. See [BANK_ADAPTERS_GUIDE.md](BANK_ADAPTERS_GUIDE.md) for a copy-paste AI prompt that analyzes a redacted bank email and returns one candidate JSON profile.

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

The repository's `BANK_ADAPTERS_JSON.json` file is the source-controlled catalogue. Each profile includes a display name, institution type, activation state, exact sender allowlist, parsing regexes, and an optional `note`. Source URLs are not part of the profile schema. Vercel is configured to bundle this file with both `/api/banks` and `/api/verify`; you do not need to paste the entire catalogue into a Vercel environment variable. To prepare a new profile from a real notification, follow [BANK_ADAPTERS_GUIDE.md](BANK_ADAPTERS_GUIDE.md).

The `GET /api/banks` response tells you which entries are configured. At this point, only NayaPay is enabled. A bank listed with `status: "needs_verified_email_sample"` is not an active integration. The public response omits `source_urls` entirely and includes `note` only when it contains non-empty text.

To activate a bank or wallet:

1. Make a small test credit to the receiving account and save the original alert email in a private location. Redact account numbers, names, balances, links, and other personal information before sharing a test fixture.
2. Confirm the exact `From` email address from multiple genuine alerts and add only that exact address to `sender_allowlist`. Do not use a broad domain wildcard or a public customer-support email.
3. Write transaction-ID and amount regexes against that institution's actual email body. Check that the amount regex cannot accidentally capture the balance, fee, or a later total field.
4. Add a synthetic test fixture and test for valid transactions, missing fields, amount mismatches, malformed values, and similar labels in the same email.
5. Only then set `enabled` to `true` and `status` to `configured`. Deploy and compare a test transfer against the bank's official statement.

Use `BANK_ADAPTERS_JSON` only if you need an environment-specific override. It must be a JSON object keyed by adapter slug. Environment entries replace the same slug from the file; they do not need to repeat all other catalogue entries. Never put credentials in the JSON file or in a client-side environment variable.

Use [BANK_ADAPTERS_GUIDE.md](BANK_ADAPTERS_GUIDE.md) for the full AI prompt, redaction guidance, expected JSON format, and safe activation workflow. Official bank directories and general alert policies cannot establish a provider-specific transaction-email sender or template.

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

## QR generation and five-minute automatic verification

The API provides both \`POST /api/generate\` and the requested misspelling \`POST /api/genrate\`; both routes behave the same. The route creates a PKR payment intent, renders a QR image, and gives it a five-minute deadline. A checkout should call \`GET /api/status?id=INTENT_UUID\` every few seconds using the returned \`X-Payment-Token\`. Each status request searches the configured mailbox for a trusted, unclaimed credit alert matching the exact amount and whose IMAP arrival time falls inside the intent's five-minute window.

When exactly one matching alert is found, the API claims the transaction using the existing one-time-claim protection and changes the intent status to \`verified\`. If no valid alert is found by expiry, the status becomes \`not_verified\`. Multiple matching alerts are treated as ambiguous; the API will not guess which transaction belongs to the order. To reduce ambiguity, the database allows only one pending intent per bank and amount.

### Required configuration

In Vercel, add:

| Variable | Purpose |
| --- | --- |
| \`PAYMENT_QR_PAYLOAD_TEMPLATE\` | The **actual dynamic payment URI/template provided by your bank or payment provider**. It must contain \`{amount}\` and may contain \`{reference}\`. These placeholders are URL-encoded before being substituted. There is no safe generic Pakistani-bank QR format to invent. |
| \`SUPABASE_PUBLISHABLE_KEY\` | Supabase publishable key used by the checkout page to subscribe to realtime status broadcasts. Legacy \`SUPABASE_ANON_KEY\` is also accepted. This is a public/client key, never the service-role secret. |
| \`PAYMENT_ALLOWED_ORIGINS\` | Optional comma-separated exact origins allowed to call \`GET /api/status\` from a browser, e.g. \`https://checkout.example.com\`. Leave unset if status checks are made server-to-server. |

\`SUPABASE_URL\`, \`SUPABASE_SERVICE_ROLE_KEY\`, \`API_SECRET_KEY\`, and the configured IMAP mailbox are also required. Do not use a static screenshot or invent a payment URI: set the template only to the dynamic URI format actually supported by your receiving bank/provider. If your provider supplies only a static QR image, this template-based endpoint cannot safely add a new amount to it.

Run the updated \`database/schema.sql\` in the Supabase SQL Editor. It creates the \`payment_intents\` table, restricts Data API access to \`service_role\`, and adds a status-change trigger that broadcasts to a per-intent Supabase Realtime channel. The broadcast uses a random 256-bit token as a capability topic and contains only status, amount, currency, intent ID, expiry, and verification time. The client should subscribe as soon as it receives the generate response, before showing the QR.

### Generate a QR

Call this from a trusted merchant backend. **Never put \`API_SECRET_KEY\` in browser or mobile code.**

    curl -X POST "https://YOUR-DOMAIN/api/generate" \
      -H "Authorization: Bearer YOUR_API_SECRET_KEY" \
      -H "Content-Type: application/json" \
      -d '{"bank":"nayapay","amount":"150.00","merchant_reference":"ORDER-2026-000123"}'

The response includes \`qr_code\` (an SVG data URL), \`intent_id\`, \`status_url\`, \`status_token\`, \`expires_at\`, and the Supabase Realtime connection details. Display \`qr_code\` as an image and keep the status token private to this payment checkout.

### Listen for the WebSocket update and keep checking

Install \`@supabase/supabase-js\` in your checkout frontend. The Realtime subscription delivers a \`PAYMENT_STATUS\` broadcast when the intent changes to \`verified\` or \`not_verified\`. Keep calling \`GET /api/status\` during checkout; that request is what checks the IMAP inbox. The WebSocket sends the resulting state change immediately to connected clients. The status endpoint remains the source of truth if the browser reconnects or a broadcast is missed.

    import { createClient } from "@supabase/supabase-js";

    export async function watchPayment(payment, apiBaseUrl, onStatus) {
      const supabase = createClient(
        payment.realtime.url,
        payment.realtime.publishable_key
      );
      let finished = false;
      const isFinal = (status) =>
        status === "verified" || status === "not_verified";

      const channel = supabase.channel(payment.realtime.channel);
      channel.on("broadcast", { event: "PAYMENT_STATUS" }, ({ payload }) => {
        onStatus(payload);
        if (isFinal(payload.status)) finished = true;
      });

      await new Promise((resolve, reject) => {
        channel.subscribe((status) => {
          if (status === "SUBSCRIBED") resolve();
          if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
            reject(new Error("Could not connect to payment status WebSocket"));
          }
        });
      });

      try {
        while (!finished) {
          const response = await fetch(
            new URL(payment.status_url, apiBaseUrl),
            { headers: { "X-Payment-Token": payment.status_token } }
          );
          const state = await response.json();
          if (!response.ok) throw new Error(state.message || "Status check failed");
          onStatus(state);
          if (isFinal(state.status)) {
            finished = true;
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 5000));
        }
      } finally {
        await supabase.removeChannel(channel);
      }
    }

If the checkout page is hosted on a different domain, add that exact origin to \`PAYMENT_ALLOWED_ORIGINS\`. Subscribe before starting the first status request. Do not treat the QR being scanned, the checkout browser saying success, or a WebSocket message alone as proof of payment; confirm the final \`verified\` status returned by the API.

**Runtime limitation:** this mailbox-based implementation checks while a checkout watcher is making status requests. If a customer closes the page, there is no five-minute Python process running in the background on Vercel Hobby; an always-on worker or external queue/worker would be needed for verification to continue independently of the checkout. Vercel Hobby cron jobs are daily-only, so a cron job is not a substitute for five-second polling.

## Adding another bank or wallet

For the main catalogue, add the new profile to `BANK_ADAPTERS_JSON.json`. Follow [BANK_ADAPTERS_GUIDE.md](BANK_ADAPTERS_GUIDE.md) to generate a candidate profile from a redacted raw email with AI, merge it into the file, write synthetic tests, and only then enable it. Each enabled adapter needs an exact sender allowlist and tested regexes with capture groups for the transaction ID and credited amount. An optional total-amount regex adds another consistency check. The `BANK_ADAPTERS_JSON` environment variable is for environment-specific overrides, not a replacement for the committed catalogue.

Example (replace the sample sender and patterns with values verified against that provider's real email template):

    {
      "my-bank": {
        "name": "My Bank",
        "institution_type": "commercial_bank",
        "enabled": false,
        "status": "needs_parser_tests",
        "alert_channel": "email",
        "sender_allowlist": ["alerts@example-bank.test"],
        "transaction_id_regex": "(?:Transaction ID|Reference No)\\s*[:#-]?\\s*([A-Z0-9-]{8,40})",
        "amount_regex": "(?:Amount Credited|Transaction Amount)\\s*[:#-]?\\s*(?:PKR|Rs\\.? )?([0-9,]+(?:\\.[0-9]{1,2})?)",
        "total_amount_regex": "(?:Total Amount)\\s*[:#-]?\\s*(?:PKR|Rs\\.? )?([0-9,]+(?:\\.[0-9]{1,2})?)",
        "note": "Example only; replace with a tested provider-specific profile."
      }
    }

For a private or environment-specific override, set this JSON as a single-line `BANK_ADAPTERS_JSON` environment variable in Vercel. For the shared catalogue, edit `BANK_ADAPTERS_JSON.json` as described above. Escape backslashes as required by JSON. The service only accepts messages from the exact allowlisted sender addresses. Do not use a broad domain wildcard. If a provider changes its template, update the regexes and rerun tests before deploying.

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
