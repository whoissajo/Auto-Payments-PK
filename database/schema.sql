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

-- Explicit least-privilege Data API grants. Supabase projects created under the
-- newer opt-in exposure defaults do not automatically grant table permissions.
-- Only the server's privileged role may read or insert verified payment claims.
revoke all on table public.verified_payments from public, anon, authenticated;
grant usage on schema public to service_role;
grant select, insert on table public.verified_payments to service_role;


-- Payment intents power /api/generate (and its requested /api/genrate alias).
-- One pending intent per bank/amount avoids ambiguous automatic matching when
-- two customers request exactly the same price at the same time.
create table if not exists public.payment_intents (
    id uuid primary key default gen_random_uuid(),
    bank text not null,
    merchant_reference text not null unique,
    amount numeric(18, 2) not null check (amount > 0),
    currency char(3) not null default 'PKR' check (currency = 'PKR'),
    status text not null default 'pending'
        check (status in ('pending', 'verified', 'not_verified')),
    status_token text not null unique,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    last_checked_at timestamptz,
    verified_at timestamptz,
    transaction_id text,
    sender text,
    email_message_id text,
    constraint payment_intents_expiry_after_creation check (expires_at > created_at)
);

create index if not exists payment_intents_pending_expiry_idx
    on public.payment_intents (expires_at)
    where status = 'pending';

create unique index if not exists payment_intents_one_pending_amount_per_bank
    on public.payment_intents (bank, amount)
    where status = 'pending';

alter table public.payment_intents enable row level security;
revoke all on table public.payment_intents from public, anon, authenticated;
grant select, insert, update on table public.payment_intents to service_role;

-- Supabase Realtime sends a small public broadcast on a random, 256-bit
-- capability-token topic. The topic is returned only to the caller creating
-- the intent. The event contains no mailbox, sender, or transaction identifiers.
create or replace function public.broadcast_payment_intent_status()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    perform realtime.send(
        jsonb_build_object(
            'intent_id', new.id::text,
            'status', new.status,
            'amount', new.amount::text,
            'currency', new.currency,
            'expires_at', new.expires_at,
            'verified_at', new.verified_at
        ),
        'PAYMENT_STATUS',
        'payment-intent:' || new.status_token,
        false
    );
    return new;
end;
$$;

revoke all on function public.broadcast_payment_intent_status() from public, anon, authenticated;
drop trigger if exists payment_intent_status_broadcast on public.payment_intents;
create trigger payment_intent_status_broadcast
    after update of status on public.payment_intents
    for each row
    when (old.status is distinct from new.status)
    execute function public.broadcast_payment_intent_status();
