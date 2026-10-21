# Add a bank or wallet adapter with AI

This guide helps you turn a bank/wallet **transaction-credit email** into a candidate profile for `BANK_ADAPTERS_JSON.json`. The AI-generated profile is a starting point, not proof that payments can be safely verified. Keep it disabled until the sender and parser have been tested.

## 1. Collect and redact a real email

Use one or more genuine incoming-credit/received-payment alerts from the provider. In the email, copy the visible **From address**, subject, and message body. Include original field labels and enough surrounding text to distinguish the transaction amount from a balance, fee, or total.

Before pasting the email into an AI service, remove personal and financial data that is not needed for parsing: account/IBAN numbers, customer names, addresses, phone numbers, personal email addresses, balances, unrelated transaction details, tracking links, OTPs, PINs, passwords, and any credentials. Keep the bank/provider's exact sender email if you want the prompt to build an allowlist. Replace transaction IDs and amounts with fake values of the same format only if needed; keep the labels and punctuation unchanged. Never paste a mailbox password, app password, API key, full bank statement, or customer data.

A single email may not show all possible layouts. Two or three separately redacted alerts are better when the provider's messages differ.

## 2. Copy this prompt and paste the redacted email below it

Replace `<BANK NAME>` and `<BANK SLUG>`, then paste the redacted email sample(s) at the end. Ask an AI model to return only the JSON.

```text
You are helping configure one email parser for the open-source Auto-Payments-PK project.

Bank/wallet name: <BANK NAME>
Catalogue slug: <BANK SLUG>

I will paste one or more redacted raw transaction-credit notification emails after this prompt. Inspect the actual From header and body. Generate exactly ONE new profile object for BANK_ADAPTERS_JSON.json, keyed by the provided lowercase slug.

Return ONLY valid JSON:
- No Markdown fences, explanations, comments, or trailing commas.
- Return a JSON object containing this single top-level slug and its profile, so I can merge it into the existing BANK_ADAPTERS_JSON.json file.
- Do not include source_urls, official_references, research_note, or any fields that are not in the schema below.
- Escape regex backslashes correctly for JSON. The JSON must parse with Python's json.loads().

Use exactly these profile fields:
{
  "<BANK SLUG>": {
    "name": "<bank/wallet display name>",
    "institution_type": "<one supported type>",
    "enabled": false,
    "status": "needs_parser_tests",
    "alert_channel": "email",
    "sender_allowlist": [],
    "transaction_id_regex": null,
    "amount_regex": null,
    "total_amount_regex": null,
    "note": "<brief factual note about what the sample establishes and any uncertainty>"
  }
}

Rules:
1. institution_type must be one of: commercial_bank, islamic_bank, foreign_bank, digital_bank, specialized_bank, microfinance_bank, emi, wallet. Choose based on the institution name/context; do not invent a legal classification. If uncertain, use "bank".
2. sender_allowlist must contain only the exact full email address found in the provider's From header, normalized to lowercase. Never guess an address, allow a whole domain, use a display name alone, or include the user's mailbox address. If the real provider sender cannot be established from the sample, leave it [].
3. transaction_id_regex must match the transaction/reference ID from the email body and have a capture group for only that ID. Match the field label and surrounding punctuation as narrowly as the sample allows. Do not capture generic message IDs or unrelated reference numbers.
4. amount_regex must match the amount actually credited/received for this transaction, with a capture group for the numeric amount only (digits, optional commas, and at most two decimal places). It must not match an account balance, available balance, fee, tax, or total amount. Support the currency label/punctuation seen in the sample, such as PKR or Rs., without assuming unseen formats.
5. total_amount_regex should have a capture group for the numeric total only if the email contains a distinct, unambiguous total amount field. Otherwise set it to null. Never substitute the balance for the transaction amount.
6. Regexes are evaluated case-insensitively and can span newlines. Make each required regex specific to the observed field labels, and ensure transaction_id_regex and amount_regex each have a capture group. Do not claim a regex works for formats that are not present in the supplied sample.
7. Be conservative. If the sender, transaction ID, or credited amount cannot be reliably identified, keep the relevant value empty/null, mention the missing evidence in note, and leave enabled false. Always leave enabled false and status "needs_parser_tests" until a human runs tests on synthetic fixtures and confirms the parser against real notifications.
8. Do not create regexes from guesses or general bank knowledge. Only rely on the email samples below. Never include personal data from the examples in note or any other output field.
9. Do not add comments, URLs, documentation references, example API credentials, or additional profiles.

Redacted raw email sample(s) begin below:
<PASTE REDACTED EMAIL HEADERS AND BODY HERE>
```

## 3. Merge the result into the catalogue

1. Open `BANK_ADAPTERS_JSON.json` and add the returned top-level slug next to the existing profiles. Do not replace the whole catalogue with the AI response.
2. Check that the slug is unique and is lowercase with hyphens only. Do not create a duplicate of an existing alias/profile.
3. Confirm the JSON parses. Locally, run:

   ```bash
   python -m json.tool BANK_ADAPTERS_JSON.json
   python -m unittest discover -s tests -v
   ```

4. Add synthetic tests using fake transaction IDs and amounts. Test a correct credit alert, a different amount, a missing ID, balance-only text, fee/total fields, malformed amounts, and any second email layout. Never commit raw emails or identifying data as fixtures.
5. Only after review and tests pass, set `enabled` to `true` and `status` to `configured`. Do not enable a profile solely because an AI produced regexes.
6. Commit the catalogue and tests together, deploy, then make a small test transfer and reconcile it with the provider's official transaction record.

## Profile example

This is a schematic example only. It is not a real sender or a working adapter, and its profile must remain disabled until tested:

```json
{
  "example-bank": {
    "name": "Example Bank",
    "institution_type": "commercial_bank",
    "enabled": false,
    "status": "needs_parser_tests",
    "alert_channel": "email",
    "sender_allowlist": [],
    "transaction_id_regex": null,
    "amount_regex": null,
    "total_amount_regex": null,
    "note": "Awaiting a verified sender and tested transaction-credit parser."
  }
}
```

## Important limitations

Auto-Payments-PK reads incoming email notifications through IMAP. A sender allowlist and regex are not equivalent to an official bank API or cryptographically verified webhook. Email can be delayed, spoofed, or changed. Do not rely on this project as the sole settlement proof for high-value payments. Prefer a provider's official merchant API/webhook or other bank-confirmed integration when available.
