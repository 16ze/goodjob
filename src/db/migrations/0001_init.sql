create extension if not exists pgcrypto;

create table if not exists public.offres (
  id uuid primary key default gen_random_uuid(),
  source text not null,
  url text not null unique,
  url_hash text not null unique,
  titre text not null,
  entreprise text,
  lieu text,
  code_postal text,
  date_publication date,
  description_brute text,
  score_match int check (score_match is null or (score_match >= 0 and score_match <= 100)),
  raison_score text,
  statut text not null default 'nouveau' check (
    statut in ('nouveau', 'draft_pret', 'envoye', 'ko_auto', 'ko_manuel')
  ),
  lettre_generee text,
  notion_page_id text,
  gmail_draft_id text,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now()
);

create index if not exists idx_offres_statut on public.offres(statut);
create index if not exists idx_offres_date on public.offres(date_publication desc);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_offres_updated_at on public.offres;

create trigger trg_offres_updated_at
before update on public.offres
for each row
execute function public.set_updated_at();

alter table public.offres enable row level security;
