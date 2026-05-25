# goodjob

Pipeline semi-automatique pour détecter des offres de vendeur en Suisse frontalière,
préparer les candidatures, puis laisser Bryan valider et envoyer manuellement.

## Setup local

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Vérification Ticket 1

```bash
ruff check
mypy --strict src
pytest
```

## Vérification Ticket 2

Appliquer d'abord `src/db/migrations/0001_init.sql` dans Supabase, puis remplir
`SUPABASE_URL` et `SUPABASE_SERVICE_KEY` dans `.env`.

```bash
python -m src.db.smoke_test
```

## Règle produit

Le système ne doit jamais envoyer d'email automatiquement.
