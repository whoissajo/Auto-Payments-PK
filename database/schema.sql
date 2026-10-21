-- Auto-Payments-PK: durable, one-time payment claims.
-- Run this in the Supabase SQL Editor before enabling POST /api/verify.
-- The API accesses this table server-side with the service-role key only.

create extension if not exists pgcrypto;

create table if not exists public.verified_payments (
    id uuid primary key default gen_random_uuid(),
    bank text not null,
    transaction_id text not null,
    merchant_reference text not null,
    amount numeric(18, 2) not null check (amount > 0),
    currency char(3) not null default 'PKR' check (currency = 'PKR'),
    verified_at timestamptz not null,
    sender text not null,
    email_message_id text,
    created_at timestamptz not null default now(),
    constraint verified_payments_bank_transaction_unique unique (bank, transaction_id),
    constraint verified_payments_merchant_reference_unique unique (merchant_reference)
);

create index if not exists verified_payments_verified_at_idx
    on public.verified_payments (verified_at desc);

alter table public.verified_payments enable row level security;

-- Intentionally create no anon/authenticated policies. The server's
-- service-role key bypasses RLS. Never expose that key to a client.
