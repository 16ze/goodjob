# CLAUDE.md — Projet `goodjob`

## Contexte

Bryan Hilaire, résident français à Belfort. **Permis G expiré** — à réactiver à l'embauche (demande faite par le futur employeur auprès du canton). Statut classique côté RH suisse, non bloquant pour postuler.
Recherche poste vendeur en Suisse, région Jura + Jura bernois + Neuchâtel limitrophe.
Périmètre géo dur : codes postaux **2800–2900** (canton du Jura), **2350–2400** (Jura bernois), **2520–2610** (Neuchâtel ouest). Villes cibles : Delémont, Porrentruy, Saignelégier, Moutier, Tavannes, Saint-Imier, La Chaux-de-Fonds.

## Objectif du projet

Système semi-automatique de détection + préparation de candidatures :
- Scrape quotidien des nouvelles offres "vendeur / vendeuse / conseiller·ère de vente" sur 4 sources
- Filtrage géo + déduplication
- Génération lettre personnalisée + score de matching par Claude
- Dashboard Notion avec validation humaine
- Envoi MANUEL par Bryan (jamais d'envoi auto)

**Non-objectifs** : envoi automatique d'emails, scraping massif (>500 req/jour), candidatures hors Jura.

## Stack

- **Langage** : Python 3.11
- **Scraping** : Playwright (mode headless, user-agent rotatif)
- **Base de données** : Supabase (project: `goodjob`)
- **IA** : Anthropic API (claude-sonnet-4-6 pour lettres, claude-haiku-4-5 pour scoring)
- **Orchestration** : GitHub Actions (cron `0 6 * * *` UTC = 07:00 Paris/Suisse hiver, 08:00 été)
- **Dashboard** : Notion (MCP) — base "Candidatures Suisse"
- **Drafts** : Gmail API (création de brouillons, jamais d'envoi)
- **Déploiement** : Vercel pour l'éventuelle UI Next.js, scraper en cron self-hosted ou GitHub Actions

## Sources cibles

1. **jobup.ch** — recherche : `vendeur` + canton JU/BE/NE
2. **jobs.ch** — recherche : `vendeur` + zone Jura
3. **indeed.ch** — fallback, doublons fréquents à dédupliquer
4. **Sites entreprises directs** (carrière) :
   - migros.ch (Migros Neuchâtel-Fribourg)
   - coop.ch
   - manor.ch
   - aldi.ch / lidl.ch
   - denner.ch
   - landi.ch (zone rurale jurassienne)

## Schéma Supabase

```sql
create table offres (
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
  score_match int,
  raison_score text,
  statut text default 'nouveau',
  lettre_generee text,
  notion_page_id text,
  gmail_draft_id text,
  created_at timestamp default now(),
  updated_at timestamp default now()
);

create index idx_statut on offres(statut);
create index idx_date on offres(date_publication desc);
```

## Règles de filtrage

**Inclure** :
- Titres contenant : `vendeur`, `vendeuse`, `conseiller(ère) (de )?vente`, `employé(e) (de )?commerce`
- Code postal dans whitelist (voir périmètre)
- Permis G accepté OU non spécifié
- **Exclure si "permis G valide obligatoire dès J1" / "permis en cours de validité exigé"** (Bryan a permis G expiré à réactiver via embauche)

**Exclure** :
- Postes à >40km de Belfort
- Postes exigeant allemand C1 (sauf si bilingue mentionné côté Bryan)
- Stages, apprentissages CFC (sauf demande explicite)
- Postes <50% si pas demandé

## Scoring (Claude haiku)

Prompt système attribue un score 0–100 sur :
- Proximité géo (30 pts)
- Adéquation profil (40 pts)
- Conditions (langue, taux d'activité, frontalier OK) (30 pts)

Seuil draft auto-généré : **score ≥ 65**.

## Conventions code

- Python : `ruff` + `mypy --strict`
- Pas de secrets en clair → `.env` + `os.getenv`, exemple dans `.env.example`
- Logs structurés JSON (logger `structlog`)
- Tous les scrapers héritent de `BaseScraper` avec méthodes `fetch_listings()`, `parse_listing()`, `extract_full_offer()`
- Tests : `pytest`, fixtures HTML capturées dans `tests/fixtures/`

## MCP connectés à utiliser

- `Supabase` : créer projet, appliquer migrations, exécuter SQL
- `Notion` : créer base "Candidatures Suisse", insérer/maj pages
- `Gmail` : créer drafts uniquement (jamais d'envoi)
- `Vercel` : déploiement si UI Next.js (optionnel)
- `Context7` : doc Playwright, Supabase, Anthropic SDK à jour

## Orchestration GitHub Actions

Workflow `.github/workflows/daily.yml` :
- Trigger : `cron: '0 6 * * *'` (06:00 UTC) + `workflow_dispatch` (run manuel)
- Steps : checkout → setup Python 3.11 → install deps → `playwright install chromium` → `python -m pipeline.run`
- Secrets requis (Settings → Secrets and variables → Actions) :
  - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
  - `ANTHROPIC_API_KEY`
  - `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`
  - `NOTION_TOKEN`, `NOTION_DATABASE_ID`
- Quota : ~10 min/run × 30 runs/mois = 300 min, largement sous le plafond gratuit (2000 min/mois repo privé)

## Workflow quotidien (cron 07:00)

1. `scrapers/run_all.py` → fetch toutes sources, insert dans `offres`
2. `filtering/geo_filter.py` → marquer `ko_auto` les offres hors périmètre
3. `ai/score.py` → score haiku sur les `nouveau`
4. `ai/generate_letter.py` → pour les `score >= 65`, générer lettre + draft Gmail
5. `sync/notion_sync.py` → push dans Notion avec lien CV adapté
6. Email matinal récap : "X offres détectées, Y prêtes à valider"

## À ne JAMAIS faire

- Envoyer un email automatiquement
- Scraper >100 pages/jour par source (risque ban)
- Mentir sur les compétences ou l'expérience dans les lettres
- Postuler à un poste sans validation humaine finale
- Stocker des données personnelles de recruteurs hors RGPD

## Roadmap

- **J0–J2** : scraper jobup.ch + Supabase + scoring haiku — valider 1 cycle complet manuel
- **J3–J4** : ajout jobs.ch + indeed.ch + dédup
- **J5–J6** : génération lettre + draft Gmail + sync Notion
- **J7** : ajout sites entreprises directs
- **J8+** : workflow GitHub Actions + monitoring (logs Supabase + email récap matinal)

## Commandes utiles

```bash
# Setup local
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env

# Run scraper unique
python -m scrapers.jobup --max 50

# Run pipeline complet (dry-run)
python -m pipeline.run --dry-run

# Tests
pytest -xvs
```
