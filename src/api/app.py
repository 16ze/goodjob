from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import httpx
import jwt as pyjwt
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from src.ai.generate_letter import OpenAIJobLetterGenerator, build_openai_letter_generator, generate_letters_for_matching_offers
from src.ai.score import OpenAIJobScorer, build_openai_scorer, score_new_offers
from src.integrations.email_finder import find_company_email_sync
from src.db.client import OfferRow, ParametersRow, SupabaseOfferRepository
from src.integrations.gmail import GmailClient
from src.lib.config import AuthSettings, OpenAISettings, Settings, get_auth_settings, get_openai_settings, get_settings
from src.lib.logging import configure_logging
from src.pipeline.send_applications import SendApplicationsService, UrlCvAttachmentProvider
from src.integrations.notion import NotionSyncClient
from src.sync.notion_sync import sync_pending_offers_to_notion

DASHBOARD_LIMIT = 80
STATIC_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class OfferDto(BaseModel):
    """Offre affichée dans l'UI de validation."""

    id: str
    source: str
    titre: str
    entreprise: str | None
    lieu: str | None
    code_postal: str | None
    date_publication: str | None
    url: str
    description_brute: str | None
    score_match: int | None
    raison_score: str | None
    statut: str
    statut_reponse: str | None
    lettre_generee: str | None
    email_destinataire: str | None
    envoye_at: str | None
    pret_envoi: bool
    notes: str | None = None
    gmail_message_id: str | None = None
    notion_page_id: str | None = None
    gmail_draft_id: str | None = None

    model_config = ConfigDict(frozen=True)


class ParametersDto(BaseModel):
    """Réglages modifiables depuis l'UI."""

    actif: bool
    max_par_jour: int = Field(ge=1, le=50)
    score_seuil: int = Field(ge=0, le=100)
    cv_url: str | None = None
    cv_nom: str | None = None
    cv_configure: bool

    model_config = ConfigDict(frozen=True)


class DashboardDto(BaseModel):
    """Payload de démarrage du tableau de bord."""

    parameters: ParametersDto
    offers: list[OfferDto]

    model_config = ConfigDict(frozen=True)


class ParametersUpdateRequest(BaseModel):
    """Payload de mise à jour des paramètres de production."""

    actif: bool
    max_par_jour: int = Field(ge=1, le=50)
    score_seuil: int = Field(ge=0, le=100)
    cv_url: str | None = None
    cv_nom: str | None = None

    model_config = ConfigDict(frozen=True)


class OfferEmailUpdateRequest(BaseModel):
    """Destinataire validé manuellement avant l'envoi."""

    email_destinataire: str | None = Field(default=None, max_length=254)

    model_config = ConfigDict(frozen=True)


_ALLOWED_STATUTS = {"nouveau", "draft_pret", "ko_manuel", "ko_auto", "envoye"}
_ALLOWED_STATUTS_REPONSE = {"positif", "negatif", "sans_reponse", "en_attente"}


class OfferStatutUpdateRequest(BaseModel):
    statut: str

    model_config = ConfigDict(frozen=True)


class OfferStatutReponseUpdateRequest(BaseModel):
    statut_reponse: str | None = None

    model_config = ConfigDict(frozen=True)


class OfferLettreUpdateRequest(BaseModel):
    """Lettre éditée manuellement dans l'UI. Le JSON est reconstruit côté serveur."""

    objet: str = Field(min_length=1, max_length=180)
    lettre: str = Field(min_length=1)
    notes_personnalisation: list[str] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)


class SendResultDto(BaseModel):
    """Résultat d'un envoi Gmail déclenché explicitement."""

    message_id: str
    thread_id: str | None = None

    model_config = ConfigDict(frozen=True)


class BatchSendRequest(BaseModel):
    offer_ids: list[str] = Field(min_length=1, max_length=50)

    model_config = ConfigDict(frozen=True)


class BatchSendResultDto(BaseModel):
    sent: int
    failed: int
    errors: list[str]

    model_config = ConfigDict(frozen=True)


class OfferNotesUpdateRequest(BaseModel):
    """Notes libres mises à jour depuis l'UI."""

    notes: str | None = None

    model_config = ConfigDict(frozen=True)


class ActionResultDto(BaseModel):
    """Résultat générique d'une action de test ou de vérification."""

    ok: bool
    message: str
    url: str | None = None

    model_config = ConfigDict(frozen=True)


class PipelineResultDto(BaseModel):
    """Résultat chiffré d'un déclenchement pipeline."""

    count_result: int

    model_config = ConfigDict(frozen=True)


class PipelineRunDto(BaseModel):
    """Ligne de log d'un run pipeline."""

    id: int
    action: str
    count_result: int | None
    error: str | None
    created_at: str

    model_config = ConfigDict(frozen=True)


def create_app() -> FastAPI:
    """Construit l'application HTTP sans lancer de tâche automatique."""

    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="GoodJob API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "PATCH", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )

    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    return app


app = create_app()

_AUTH_SKIP_PATHS = {"/api/auth/login"}
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_DAYS = 30


@app.middleware("http")
async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Vérifie le JWT Bearer sur toutes les routes /api sauf /api/auth/login."""

    auth_cfg = get_auth_settings()
    path = request.url.path

    # Auth désactivée en dev (APP_PASSWORD non configuré)
    if not auth_cfg.app_password:
        return await call_next(request)

    # Routes publiques et assets statiques
    if path in _AUTH_SKIP_PATHS or not path.startswith("/api/"):
        return await call_next(request)

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return JSONResponse({"detail": "Non authentifié"}, status_code=401)

    token = header[7:]
    try:
        pyjwt.decode(token, auth_cfg.jwt_secret, algorithms=[_JWT_ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        return JSONResponse({"detail": "Session expirée, reconnecte-toi"}, status_code=401)
    except pyjwt.InvalidTokenError:
        return JSONResponse({"detail": "Token invalide"}, status_code=401)

    return await call_next(request)


class LoginRequest(BaseModel):
    password: str

    model_config = ConfigDict(frozen=True)


class LoginResponse(BaseModel):
    token: str

    model_config = ConfigDict(frozen=True)


@app.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    """Authentifie Bryan et retourne un JWT valide 30 jours."""

    auth_cfg = get_auth_settings()
    if auth_cfg.app_password and payload.password != auth_cfg.app_password:
        raise HTTPException(status_code=401, detail="Mot de passe incorrect")

    exp = datetime.now(UTC) + timedelta(days=_JWT_EXPIRY_DAYS)
    token = pyjwt.encode(
        {"sub": "bryan", "exp": exp},
        auth_cfg.jwt_secret,
        algorithm=_JWT_ALGORITHM,
    )
    return LoginResponse(token=token)


def get_repository() -> SupabaseOfferRepository:
    """Crée un repository par requête pour éviter tout état partagé implicite."""

    return SupabaseOfferRepository.from_settings()


def get_full_settings() -> Settings:
    """Expose la configuration complète uniquement côté serveur."""

    return get_settings()


def get_ai_settings() -> OpenAISettings:
    return get_openai_settings()


RepositoryDep = Annotated[SupabaseOfferRepository, Depends(get_repository)]
SettingsDep = Annotated[Settings, Depends(get_full_settings)]
AISettingsDep = Annotated[OpenAISettings, Depends(get_ai_settings)]


@app.get("/api/dashboard", response_model=DashboardDto)
def get_dashboard(repository: RepositoryDep) -> DashboardDto:
    """Retourne les offres et paramètres nécessaires au premier écran."""

    parameters = repository.get_parameters()
    offers = repository.list_dashboard_offers(DASHBOARD_LIMIT)
    return DashboardDto(
        parameters=_parameters_to_dto(parameters),
        offers=[_offer_to_dto(offer) for offer in offers],
    )


@app.patch("/api/settings", response_model=ParametersDto)
def update_settings(
    payload: ParametersUpdateRequest,
    repository: RepositoryDep,
) -> ParametersDto:
    """Met à jour les réglages de validation sans toucher aux secrets Gmail."""

    parameters = repository.update_parameters(
        actif=payload.actif,
        max_par_jour=payload.max_par_jour,
        score_seuil=payload.score_seuil,
        cv_url=_clean_optional_text(payload.cv_url),
        cv_nom=_clean_optional_text(payload.cv_nom),
    )
    return _parameters_to_dto(parameters)


@app.patch("/api/offers/{offer_id}/email", response_model=OfferDto)
def update_offer_email(
    offer_id: str,
    payload: OfferEmailUpdateRequest,
    repository: RepositoryDep,
) -> OfferDto:
    """Enregistre le destinataire choisi dans l'UI."""

    email = _clean_optional_text(payload.email_destinataire)
    if email is not None and "@" not in email:
        raise HTTPException(status_code=422, detail="Email destinataire invalide")

    try:
        offer = repository.update_offer_email(offer_id, email)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _offer_to_dto(offer)


@app.post("/api/offers/{offer_id}/send", response_model=SendResultDto)
def send_offer(
    offer_id: str,
    repository: RepositoryDep,
    settings: SettingsDep,
) -> SendResultDto:
    """Déclenche un envoi Gmail explicite pour une seule offre."""

    service = SendApplicationsService(
        repository,
        GmailClient.from_settings(settings),
        UrlCvAttachmentProvider(),
    )
    try:
        result = service.send_single_application(offer_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Persiste le message_id Gmail après l'envoi réussi
    offer_after_send = repository.get_offer(offer_id)
    if offer_after_send is not None and offer_after_send.email_destinataire is not None:
        repository.update_offer_sent(
            offer_id,
            email_destinataire=offer_after_send.email_destinataire,
            gmail_message_id=result.message_id,
        )
    return SendResultDto(message_id=result.message_id, thread_id=result.thread_id)


@app.post("/api/offers/send_batch", response_model=BatchSendResultDto)
def send_batch_offers(
    payload: BatchSendRequest,
    repository: RepositoryDep,
    settings: SettingsDep,
) -> BatchSendResultDto:
    """Déclenche un envoi Gmail pour une liste d'offres sélectionnées."""

    service = SendApplicationsService(
        repository,
        GmailClient.from_settings(settings),
        UrlCvAttachmentProvider(),
    )
    parameters = repository.get_parameters()
    sent = 0
    failed = 0
    errors: list[str] = []

    for offer_id in payload.offer_ids:
        try:
            if not parameters.actif:
                raise RuntimeError("L'envoi Gmail est désactivé dans les paramètres")
            offer = repository.get_offer(offer_id)
            if offer is None:
                raise RuntimeError(f"Offre {offer_id} introuvable")
            if offer.lettre_generee is None:
                raise RuntimeError(f"Offre {offer_id} : aucune lettre générée")
            if offer.email_destinataire is None:
                raise RuntimeError(f"Offre {offer_id} : aucun email destinataire")
            result = service.send_single_application(offer_id)
            offer_after_send = repository.get_offer(offer_id)
            if offer_after_send is not None and offer_after_send.email_destinataire is not None:
                repository.update_offer_sent(
                    offer_id,
                    email_destinataire=offer_after_send.email_destinataire,
                    gmail_message_id=result.message_id,
                )
            sent += 1
        except Exception as exc:
            failed += 1
            errors.append(str(exc))

    return BatchSendResultDto(sent=sent, failed=failed, errors=errors)


@app.patch("/api/offers/{offer_id}/statut", response_model=OfferDto)
def update_offer_statut(
    offer_id: str,
    payload: OfferStatutUpdateRequest,
    repository: RepositoryDep,
) -> OfferDto:
    """Change le statut manuel d'une offre (ko_manuel, draft_pret, nouveau…)."""

    if payload.statut not in _ALLOWED_STATUTS:
        raise HTTPException(status_code=422, detail=f"Statut invalide: {payload.statut}")
    try:
        offer = repository.update_offer_statut(offer_id, payload.statut)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _offer_to_dto(offer)


@app.patch("/api/offers/{offer_id}/statut_reponse", response_model=OfferDto)
def update_offer_statut_reponse(
    offer_id: str,
    payload: OfferStatutReponseUpdateRequest,
    repository: RepositoryDep,
) -> OfferDto:
    """Enregistre la réponse recruteur (positif, négatif, en_attente, sans_reponse)."""

    if payload.statut_reponse is not None and payload.statut_reponse not in _ALLOWED_STATUTS_REPONSE:
        raise HTTPException(status_code=422, detail=f"Statut réponse invalide: {payload.statut_reponse}")
    try:
        offer = repository.update_offer_statut_reponse(offer_id, payload.statut_reponse)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _offer_to_dto(offer)


@app.patch("/api/offers/{offer_id}/lettre", response_model=OfferDto)
def update_offer_lettre(
    offer_id: str,
    payload: OfferLettreUpdateRequest,
    repository: RepositoryDep,
) -> OfferDto:
    """Sauvegarde la lettre éditée manuellement dans l'UI."""

    serialized = json.dumps(
        {
            "objet": payload.objet,
            "lettre": payload.lettre,
            "notes_personnalisation": payload.notes_personnalisation,
        },
        ensure_ascii=False,
        indent=2,
    )
    try:
        offer = repository.update_offer_letter(offer_id, serialized)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _offer_to_dto(offer)


@app.post("/api/offers/{offer_id}/rescore", response_model=OfferDto)
def rescore_offer(
    offer_id: str,
    repository: RepositoryDep,
    ai_settings: AISettingsDep,
) -> OfferDto:
    """Re-déclenche le scoring OpenAI en forçant l'écrasement du score existant."""

    offer = repository.get_offer(offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Offre introuvable")
    scorer: OpenAIJobScorer = build_openai_scorer(ai_settings)
    try:
        result = scorer.score_offer(offer, force=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erreur OpenAI scoring: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=422, detail="Le scorer n'a pas retourné de résultat")
    updated = repository.update_offer_score(offer_id, result.score, result.raison)
    return _offer_to_dto(updated)


@app.post("/api/offers/{offer_id}/generate_letter", response_model=OfferDto)
def generate_offer_letter(
    offer_id: str,
    repository: RepositoryDep,
    ai_settings: AISettingsDep,
) -> OfferDto:
    """Re-génère la lettre de candidature via OpenAI (force=True)."""

    offer = repository.get_offer(offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Offre introuvable")
    generator: OpenAIJobLetterGenerator = build_openai_letter_generator(ai_settings)
    try:
        result = generator.generate_offer_letter(offer, force=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erreur OpenAI génération lettre: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=422, detail="Score insuffisant pour générer une lettre (< 65)")
    serialized = result.model_dump_json(indent=2)
    updated = repository.update_offer_letter(offer_id, serialized)
    return _offer_to_dto(updated)


@app.get("/api/offers/export")
def export_offers_csv(repository: RepositoryDep) -> StreamingResponse:
    """Exporte toutes les offres au format CSV."""

    offers = repository.list_dashboard_offers(500)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "titre", "entreprise", "lieu", "code_postal", "source",
        "date_publication", "score_match", "statut", "statut_reponse",
        "email_destinataire", "envoye_at", "url",
    ])
    for offer in offers:
        writer.writerow([
            offer.id,
            offer.titre,
            offer.entreprise,
            offer.lieu,
            offer.code_postal,
            offer.source,
            offer.date_publication.isoformat() if offer.date_publication is not None else "",
            offer.score_match,
            offer.statut,
            offer.statut_reponse,
            offer.email_destinataire,
            offer.envoye_at.isoformat() if offer.envoye_at is not None else "",
            offer.url,
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=\"goodjob_offres.csv\""},
    )


@app.patch("/api/offers/{offer_id}/notes", response_model=OfferDto)
def update_offer_notes(
    offer_id: str,
    payload: OfferNotesUpdateRequest,
    repository: RepositoryDep,
) -> OfferDto:
    """Met à jour les notes libres d'une offre."""

    try:
        offer = repository.update_offer_notes(offer_id, payload.notes)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _offer_to_dto(offer)


@app.delete("/api/offers/{offer_id}")
def delete_offer(
    offer_id: str,
    repository: RepositoryDep,
) -> dict[str, bool]:
    """Supprime définitivement une offre."""

    try:
        repository.delete_offer(offer_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@app.post("/api/settings/test_gmail", response_model=ActionResultDto)
def test_gmail_connection(settings: SettingsDep) -> ActionResultDto:
    """Vérifie que les credentials Gmail sont valides en obtenant un access token."""

    try:
        client = GmailClient.from_settings(settings)
        client._refresh_access_token()  # noqa: SLF001
        return ActionResultDto(ok=True, message="Connexion Gmail OK")
    except Exception as exc:
        return ActionResultDto(ok=False, message=str(exc))


@app.get("/api/settings/test_cv", response_model=ActionResultDto)
def test_cv_access(repository: RepositoryDep) -> ActionResultDto:
    """Vérifie que l'URL du CV configurée est accessible."""

    parameters = repository.get_parameters()
    cv_url = parameters.cv_url
    if cv_url is None:
        return ActionResultDto(ok=False, message="Aucun CV configuré", url=None)
    if cv_url.startswith("http"):
        try:
            response = httpx.head(cv_url, timeout=5, follow_redirects=True)
            if 200 <= response.status_code < 300:
                return ActionResultDto(ok=True, message="CV accessible", url=cv_url)
            return ActionResultDto(
                ok=False,
                message=f"CV inaccessible (HTTP {response.status_code})",
                url=cv_url,
            )
        except Exception as exc:
            return ActionResultDto(ok=False, message=str(exc), url=cv_url)
    else:
        if Path(cv_url).exists():
            return ActionResultDto(ok=True, message="CV trouvé localement", url=cv_url)
        return ActionResultDto(ok=False, message="Fichier CV introuvable", url=cv_url)


@app.post("/api/settings/upload_cv", response_model=ParametersDto)
async def upload_cv(
    repository: RepositoryDep,
    settings: SettingsDep,
    file: UploadFile = File(...),
) -> ParametersDto:
    """Téléverse le CV PDF vers Supabase Storage et met à jour les paramètres."""

    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Le fichier dépasse la limite de 5 Mo")

    filename = file.filename or "CV.pdf"
    from supabase import create_client as _create_supabase_client
    supabase_client = _create_supabase_client(
        settings.supabase_url,
        settings.supabase_service_key.get_secret_value(),
    )
    storage_path = filename
    supabase_client.storage.from_("cv").upload(
        storage_path,
        content,
        {"content-type": "application/pdf", "upsert": "true"},
    )
    public_url = supabase_client.storage.from_("cv").get_public_url(storage_path)

    current = repository.get_parameters()
    updated = repository.update_parameters(
        actif=current.actif,
        max_par_jour=current.max_par_jour,
        score_seuil=current.score_seuil,
        cv_url=public_url,
        cv_nom=filename,
    )
    return _parameters_to_dto(updated)


@app.post("/api/pipeline/score", response_model=PipelineResultDto)
def trigger_pipeline_score(
    repository: RepositoryDep,
    ai_settings: AISettingsDep,
) -> PipelineResultDto:
    """Déclenche le scoring OpenAI sur les offres nouvelles non scorées."""

    scorer = build_openai_scorer(ai_settings)
    try:
        count = score_new_offers(repository, scorer, limit=20)
        repository.insert_pipeline_log("score", count)
        return PipelineResultDto(count_result=count)
    except Exception as exc:
        repository.insert_pipeline_log("score", error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/pipeline/find_emails", response_model=PipelineResultDto)
def trigger_find_emails(repository: RepositoryDep) -> PipelineResultDto:
    """Cherche les emails manquants sur toutes les offres sans email_destinataire."""

    offers = repository.get_offers_missing_email(limit=50)
    found = 0
    for offer in offers:
        if not offer.get("entreprise"):
            continue
        email = find_company_email_sync(
            str(offer["entreprise"]),
            str(offer.get("url", "")),
        )
        if email:
            repository.update_offer_email(str(offer["id"]), email)
            found += 1
    repository.insert_pipeline_log("find_emails", found)
    return PipelineResultDto(count_result=found)


@app.post("/api/pipeline/letters", response_model=PipelineResultDto)
def trigger_pipeline_letters(
    repository: RepositoryDep,
    ai_settings: AISettingsDep,
) -> PipelineResultDto:
    """Déclenche la génération de lettres pour les offres matchantes."""

    generator = build_openai_letter_generator(ai_settings)
    try:
        count = generate_letters_for_matching_offers(repository, generator, limit=20)
        repository.insert_pipeline_log("letters", count)
        return PipelineResultDto(count_result=count)
    except Exception as exc:
        repository.insert_pipeline_log("letters", error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/pipeline/notion", response_model=PipelineResultDto)
def trigger_pipeline_notion(
    repository: RepositoryDep,
    settings: SettingsDep,
) -> PipelineResultDto:
    """Déclenche la synchronisation Notion pour les offres prêtes."""

    client = NotionSyncClient.from_settings(settings)
    try:
        count = sync_pending_offers_to_notion(repository, client, limit=20)
        repository.insert_pipeline_log("notion", count)
        return PipelineResultDto(count_result=count)
    except Exception as exc:
        repository.insert_pipeline_log("notion", error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/pipeline/logs", response_model=list[PipelineRunDto])
def get_pipeline_logs(repository: RepositoryDep) -> list[PipelineRunDto]:
    """Retourne les derniers logs de runs du pipeline."""

    logs = repository.get_pipeline_logs(20)
    result = []
    for log in logs:
        created_at = log.get("created_at")
        if hasattr(created_at, "isoformat"):
            created_at_str = created_at.isoformat()
        else:
            created_at_str = str(created_at) if created_at is not None else ""
        log_id = log["id"]
        count_result = log.get("count_result")
        error = log.get("error")
        result.append(
            PipelineRunDto(
                id=int(log_id) if isinstance(log_id, (int, str, float)) else 0,
                action=str(log["action"]),
                count_result=int(count_result) if isinstance(count_result, (int, float)) else None,
                error=str(error) if error is not None else None,
                created_at=created_at_str,
            )
        )
    return result


class TriggerResultDto(BaseModel):
    ok: bool
    message: str


@app.post("/api/pipeline/trigger", response_model=TriggerResultDto)
async def trigger_github_workflow() -> TriggerResultDto:
    """Déclenche le workflow GitHub Actions daily.yml via workflow_dispatch."""

    auth_cfg = get_auth_settings()
    if not auth_cfg.github_token or not auth_cfg.github_repo:
        raise HTTPException(
            status_code=503,
            detail="GITHUB_TOKEN et GITHUB_REPO non configurés — impossible de déclencher le workflow.",
        )
    url = f"https://api.github.com/repos/{auth_cfg.github_repo}/actions/workflows/daily.yml/dispatches"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {auth_cfg.github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": "main"},
        )
    if resp.status_code == 204:
        return TriggerResultDto(ok=True, message="Workflow lancé — il démarre dans quelques secondes.")
    detail = resp.text[:300]
    raise HTTPException(status_code=resp.status_code, detail=f"GitHub API: {detail}")


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str) -> FileResponse:
    """Sert le build React en production Railway."""

    requested_path = STATIC_DIR / full_path
    if full_path and requested_path.is_file():
        return FileResponse(requested_path)
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend non compilé")
    return FileResponse(index_path)


def _parameters_to_dto(parameters: ParametersRow) -> ParametersDto:
    return ParametersDto(
        actif=parameters.actif,
        max_par_jour=parameters.max_par_jour,
        score_seuil=parameters.score_seuil,
        cv_url=parameters.cv_url,
        cv_nom=parameters.cv_nom,
        cv_configure=parameters.cv_url is not None,
    )


def _offer_to_dto(offer: OfferRow) -> OfferDto:
    pret_envoi = (
        offer.envoye_at is None
        and offer.lettre_generee is not None
        and offer.email_destinataire is not None
    )
    return OfferDto(
        id=offer.id,
        source=offer.source,
        titre=offer.titre,
        entreprise=offer.entreprise,
        lieu=offer.lieu,
        code_postal=offer.code_postal,
        date_publication=offer.date_publication.isoformat()
        if offer.date_publication is not None
        else None,
        url=offer.url,
        description_brute=offer.description_brute,
        score_match=offer.score_match,
        raison_score=offer.raison_score,
        statut=offer.statut,
        statut_reponse=offer.statut_reponse,
        lettre_generee=offer.lettre_generee,
        email_destinataire=offer.email_destinataire,
        envoye_at=offer.envoye_at.isoformat() if offer.envoye_at is not None else None,
        pret_envoi=pret_envoi,
        notes=offer.notes,
        gmail_message_id=offer.gmail_message_id,
        notion_page_id=offer.notion_page_id,
        gmail_draft_id=offer.gmail_draft_id,
    )


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
