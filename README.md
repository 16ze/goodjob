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

## Règle produit

Le système ne doit jamais envoyer d'email automatiquement.
