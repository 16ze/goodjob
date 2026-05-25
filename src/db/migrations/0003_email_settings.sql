alter table public.offres
  add column if not exists statut_reponse text default 'en_attente' check (
    statut_reponse in ('en_attente', 'positif', 'negatif', 'sans_reponse')
  ),
  add column if not exists envoye_at timestamp with time zone,
  add column if not exists email_destinataire text;

create table if not exists public.parametres (
  id int primary key default 1 check (id = 1),
  actif boolean not null default false,
  max_par_jour int not null default 5 check (max_par_jour > 0),
  score_seuil int not null default 65 check (score_seuil >= 0 and score_seuil <= 100),
  cv_url text,
  cv_nom text,
  updated_at timestamp with time zone not null default now()
);

insert into public.parametres (id)
values (1)
on conflict (id) do nothing;

drop trigger if exists trg_parametres_updated_at on public.parametres;

create trigger trg_parametres_updated_at
before update on public.parametres
for each row
execute function public.set_updated_at();

alter table public.parametres enable row level security;
