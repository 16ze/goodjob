from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Protocol, Self, cast

from postgrest.types import JSON
from pydantic import BaseModel, ConfigDict, Field
from supabase import create_client

from src.lib.config import DatabaseSettings, get_database_settings


class SupabaseResponse(Protocol):
    """Partie de réponse Supabase utilisée par le repository."""

    data: object


class ExecutableQuery(Protocol):
    """Query builder exécutable Supabase."""

    def execute(self) -> SupabaseResponse: ...


class FilterQuery(Protocol):
    """Query builder Supabase filtrable et limitable."""

    def eq(self, column: str, value: object) -> Self: ...

    def gte(self, column: str, value: object) -> Self: ...

    def is_(self, column: str, value: str) -> Self: ...

    def limit(self, count: int) -> ExecutableQuery: ...


class UpdateQuery(Protocol):
    """Query builder update Supabase."""

    def eq(self, column: str, value: object) -> ExecutableQuery: ...


class SelectQuery(Protocol):
    """Query builder select Supabase."""

    def eq(self, column: str, value: object) -> FilterQuery: ...

    def gte(self, column: str, value: object) -> FilterQuery: ...

    def limit(self, count: int) -> ExecutableQuery: ...


class DeleteQuery(Protocol):
    """Query builder delete Supabase."""

    def eq(self, column: str, value: object) -> ExecutableQuery: ...


class InsertQuery(Protocol):
    """Query builder insert Supabase."""

    def execute(self) -> SupabaseResponse: ...


class OffersTable(Protocol):
    """Surface PostgREST strictement nécessaire aux tables du projet."""

    def upsert(self, json: JSON, *, on_conflict: str) -> ExecutableQuery: ...

    def select(self, columns: str) -> SelectQuery: ...

    def update(self, json: JSON) -> UpdateQuery: ...

    def delete(self) -> DeleteQuery: ...

    def insert(self, json: JSON) -> InsertQuery: ...


class SupabaseClientLike(Protocol):
    """Client Supabase minimal pour rendre le repository testable hors réseau."""

    def table(self, table_name: str) -> OffersTable: ...


class OfferUpsert(BaseModel):
    """Payload minimal et typé pour créer ou mettre à jour une offre."""

    source: str
    url: str
    titre: str
    url_hash: str | None = None
    entreprise: str | None = None
    lieu: str | None = None
    code_postal: str | None = None
    date_publication: date | None = None
    description_brute: str | None = None
    score_match: int | None = Field(default=None, ge=0, le=100)
    raison_score: str | None = None
    statut: str = "nouveau"
    lettre_generee: str | None = None
    notion_page_id: str | None = None
    gmail_draft_id: str | None = None

    model_config = ConfigDict(frozen=True)

    def with_hash(self) -> Self:
        """Calcule le hash d'URL si l'appelant ne l'a pas déjà fourni."""

        if self.url_hash is not None:
            return self
        return self.model_copy(update={"url_hash": hash_url(self.url)})

    def to_supabase_payload(self) -> dict[str, object]:
        """Convertit l'offre en JSON compatible PostgREST, sans champs nuls inutiles."""

        payload = self.with_hash().model_dump(mode="json", exclude_none=True)
        return cast(dict[str, object], payload)


class OfferRow(BaseModel):
    """Ligne `offres` relue depuis Supabase."""

    id: str
    source: str
    url: str
    url_hash: str
    titre: str
    entreprise: str | None = None
    lieu: str | None = None
    code_postal: str | None = None
    date_publication: date | None = None
    description_brute: str | None = None
    score_match: int | None = None
    raison_score: str | None = None
    statut: str
    lettre_generee: str | None = None
    notion_page_id: str | None = None
    gmail_draft_id: str | None = None
    statut_reponse: str | None = None
    envoye_at: datetime | None = None
    email_destinataire: str | None = None
    notes: str | None = None
    gmail_message_id: str | None = None


class ParametersRow(BaseModel):
    """Réglages singleton qui pilotent les envois manuels."""

    actif: bool = False
    max_par_jour: int = Field(default=5, ge=1)
    score_seuil: int = Field(default=65, ge=0, le=100)
    cv_url: str | None = None
    cv_nom: str | None = None


def hash_url(url: str) -> str:
    """Retourne le SHA-256 hexadécimal utilisé comme clé idempotente."""

    normalized_url = url.strip()
    return sha256(normalized_url.encode("utf-8")).hexdigest()


class SupabaseOfferRepository:
    """Repository explicite pour limiter la surface Supabase utilisée par le pipeline."""

    def __init__(self, client: SupabaseClientLike) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: DatabaseSettings | None = None) -> SupabaseOfferRepository:
        """Construit le repository depuis la configuration validée."""

        resolved_settings = settings or get_database_settings()
        client = create_client(
            resolved_settings.supabase_url,
            resolved_settings.supabase_service_key.get_secret_value(),
        )
        return cls(cast(SupabaseClientLike, client))

    def upsert_offer(self, offer: OfferUpsert) -> OfferRow:
        """Insère ou met à jour une offre sans créer de doublon sur `url_hash`."""

        response = (
            self._client.table("offres")
            .upsert(cast(JSON, offer.to_supabase_payload()), on_conflict="url_hash")
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase upsert offres a retourné {len(rows)} lignes")
        return OfferRow.model_validate(rows[0])

    def get_by_url_hash(self, url_hash: str) -> OfferRow | None:
        """Relit une offre par sa clé idempotente."""

        response = (
            self._client.table("offres")
            .select("*")
            .eq("url_hash", url_hash)
            .limit(1)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if not rows:
            return None
        return OfferRow.model_validate(rows[0])

    def get_new_unscored_offers(self, limit: int) -> list[OfferRow]:
        """Récupère les offres nouvelles qui n'ont pas encore été scorées."""

        response = (
            self._client.table("offres")
            .select("*")
            .eq("statut", "nouveau")
            .is_("score_match", "null")
            .limit(limit)
            .execute()
        )
        return [OfferRow.model_validate(row) for row in _read_response_rows(response.data)]

    def get_letter_candidates(self, limit: int, *, force: bool = False) -> list[OfferRow]:
        """Récupère les offres matchantes éligibles à une génération de lettre."""

        query = (
            self._client.table("offres")
            .select("*")
            .gte("score_match", 65)
        )
        if not force:
            query = query.is_("lettre_generee", "null")

        response = query.limit(limit).execute()
        return [OfferRow.model_validate(row) for row in _read_response_rows(response.data)]

    def update_offer_score(self, offer_id: str, score_match: int, raison_score: str) -> OfferRow:
        """Persiste le score OpenAI validé pour une offre."""

        payload: dict[str, object] = {
            "score_match": score_match,
            "raison_score": raison_score,
        }
        response = (
            self._client.table("offres")
            .update(cast(JSON, payload))
            .eq("id", offer_id)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase update offres a retourné {len(rows)} lignes")
        return OfferRow.model_validate(rows[0])

    def update_offer_letter(self, offer_id: str, lettre_generee: str) -> OfferRow:
        """Persiste la lettre générée après validation Pydantic de la réponse OpenAI."""

        payload: dict[str, object] = {"lettre_generee": lettre_generee}
        response = (
            self._client.table("offres")
            .update(cast(JSON, payload))
            .eq("id", offer_id)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase update offres a retourné {len(rows)} lignes")
        return OfferRow.model_validate(rows[0])

    def get_notion_sync_candidates(self, limit: int) -> list[OfferRow]:
        """Récupère les offres scorées dont la page Notion n'a pas encore été créée."""

        response = (
            self._client.table("offres")
            .select("*")
            .gte("score_match", 65)
            .is_("notion_page_id", "null")
            .limit(limit)
            .execute()
        )
        return [OfferRow.model_validate(row) for row in _read_response_rows(response.data)]

    def update_offer_notion_page(self, offer_id: str, notion_page_id: str) -> OfferRow:
        """Persiste l'ID de la page Notion fraichement créée pour une offre."""

        payload: dict[str, object] = {"notion_page_id": notion_page_id}
        response = (
            self._client.table("offres")
            .update(cast(JSON, payload))
            .eq("id", offer_id)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase update offres a retourné {len(rows)} lignes")
        return OfferRow.model_validate(rows[0])

    def get_parameters(self) -> ParametersRow:
        """Relit les paramètres singleton avec des défauts sûrs si la table est vide."""

        response = self._client.table("parametres").select("*").limit(1).execute()
        rows = _read_response_rows(response.data)
        if not rows:
            return ParametersRow()
        return ParametersRow.model_validate(rows[0])

    def update_parameters(
        self,
        *,
        actif: bool,
        max_par_jour: int,
        score_seuil: int,
        cv_url: str | None,
        cv_nom: str | None,
    ) -> ParametersRow:
        """Met à jour les réglages singleton utilisés par l'UI et l'envoi Gmail."""

        payload: dict[str, object | None] = {
            "actif": actif,
            "max_par_jour": max_par_jour,
            "score_seuil": score_seuil,
            "cv_url": cv_url,
            "cv_nom": cv_nom,
        }
        response = (
            self._client.table("parametres")
            .update(cast(JSON, payload))
            .eq("id", 1)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase update parametres a retourné {len(rows)} lignes")
        return ParametersRow.model_validate(rows[0])

    def list_dashboard_offers(self, limit: int) -> list[OfferRow]:
        """Liste les offres visibles dans le tableau de bord de validation."""

        response = self._client.table("offres").select("*").limit(limit).execute()
        return [OfferRow.model_validate(row) for row in _read_response_rows(response.data)]

    def get_offer(self, offer_id: str) -> OfferRow | None:
        """Relit une offre par son identifiant Supabase."""

        response = self._client.table("offres").select("*").eq("id", offer_id).limit(1).execute()
        rows = _read_response_rows(response.data)
        if not rows:
            return None
        return OfferRow.model_validate(rows[0])

    def update_offer_statut(self, offer_id: str, statut: str) -> OfferRow:
        """Met à jour le statut manuel d'une offre."""

        payload: dict[str, object] = {"statut": statut}
        response = (
            self._client.table("offres")
            .update(cast(JSON, payload))
            .eq("id", offer_id)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase update offres a retourné {len(rows)} lignes")
        return OfferRow.model_validate(rows[0])

    def update_offer_statut_reponse(self, offer_id: str, statut_reponse: str | None) -> OfferRow:
        """Met à jour le statut de réponse recruteur d'une offre."""

        payload: dict[str, object | None] = {"statut_reponse": statut_reponse}
        response = (
            self._client.table("offres")
            .update(cast(JSON, payload))
            .eq("id", offer_id)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase update offres a retourné {len(rows)} lignes")
        return OfferRow.model_validate(rows[0])

    def update_offer_email(self, offer_id: str, email_destinataire: str | None) -> OfferRow:
        """Persiste le destinataire validé depuis l'UI avant l'envoi."""

        payload: dict[str, object | None] = {"email_destinataire": email_destinataire}
        response = (
            self._client.table("offres")
            .update(cast(JSON, payload))
            .eq("id", offer_id)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase update offres a retourné {len(rows)} lignes")
        return OfferRow.model_validate(rows[0])

    def get_auto_send_candidates(
        self,
        limit: int,
        *,
        score_min: int,
        score_max: int,
    ) -> list[OfferRow]:
        """Récupère les offres basse priorité éligibles à l'auto-envoi cron."""

        # Fetch more than needed, then filter in Python (lte / not_.is_ not in protocol)
        response = (
            self._client.table("offres")
            .select("*")
            .gte("score_match", score_min)
            .is_("envoye_at", "null")
            .limit(limit * 5)
            .execute()
        )
        rows = [OfferRow.model_validate(row) for row in _read_response_rows(response.data)]
        return [
            offer
            for offer in rows
            if (
                offer.score_match is not None
                and offer.score_match <= score_max
                and offer.lettre_generee is not None
                and offer.email_destinataire is not None
                and offer.statut == "draft_pret"
            )
        ][:limit]

    def get_email_send_candidates(self, limit: int, *, score_threshold: int) -> list[OfferRow]:
        """Récupère les offres prêtes à un envoi manuel de candidature."""

        response = (
            self._client.table("offres")
            .select("*")
            .gte("score_match", score_threshold)
            .is_("envoye_at", "null")
            .limit(limit * 3)
            .execute()
        )
        offers = [OfferRow.model_validate(row) for row in _read_response_rows(response.data)]
        return [
            offer
            for offer in offers
            if offer.lettre_generee is not None and offer.email_destinataire is not None
        ][:limit]

    def update_offer_sent(
        self,
        offer_id: str,
        *,
        email_destinataire: str,
        sent_at: datetime | None = None,
        gmail_message_id: str | None = None,
    ) -> OfferRow:
        """Marque une offre comme envoyée après confirmation de Gmail."""

        resolved_sent_at = sent_at or datetime.now(UTC)
        payload: dict[str, object] = {
            "statut": "envoye",
            "envoye_at": resolved_sent_at.isoformat(),
            "email_destinataire": email_destinataire,
        }
        if gmail_message_id is not None:
            payload["gmail_message_id"] = gmail_message_id
        response = (
            self._client.table("offres")
            .update(cast(JSON, payload))
            .eq("id", offer_id)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase update offres a retourné {len(rows)} lignes")
        return OfferRow.model_validate(rows[0])

    def update_offer_notes(self, offer_id: str, notes: str | None) -> OfferRow:
        """Persiste les notes manuelles d'une offre."""

        payload: dict[str, object | None] = {"notes": notes}
        response = (
            self._client.table("offres")
            .update(cast(JSON, payload))
            .eq("id", offer_id)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) != 1:
            raise RuntimeError(f"Supabase update offres a retourné {len(rows)} lignes")
        return OfferRow.model_validate(rows[0])

    def delete_offer(self, offer_id: str) -> None:
        """Supprime définitivement une offre par son identifiant."""

        response = (
            self._client.table("offres")
            .delete()
            .eq("id", offer_id)
            .execute()
        )
        rows = _read_response_rows(response.data)
        if len(rows) == 0:
            raise RuntimeError(f"Aucune offre trouvée avec l'id {offer_id}")

    def get_pipeline_logs(self, limit: int = 20) -> list[dict[str, object]]:
        """Récupère les derniers logs de runs du pipeline."""

        response = (
            self._client.table("pipeline_runs")
            .select("*")
            .limit(limit)
            .execute()
        )
        rows = _read_response_rows(response.data)
        return [dict(row) for row in rows]

    def insert_pipeline_log(
        self,
        action: str,
        count_result: int | None = None,
        error: str | None = None,
    ) -> None:
        """Insère un log de run pipeline."""

        payload: dict[str, object | None] = {
            "action": action,
            "count_result": count_result,
            "error": error,
        }
        self._client.table("pipeline_runs").insert(cast(JSON, payload)).execute()


def _read_response_rows(data: object) -> list[Mapping[str, object]]:
    """Normalise la réponse Supabase en liste de dictionnaires typés."""

    if not isinstance(data, list):
        raise RuntimeError("Réponse Supabase invalide: `data` n'est pas une liste")

    rows: list[Mapping[str, object]] = []
    for row in data:
        if not isinstance(row, Mapping):
            raise RuntimeError("Réponse Supabase invalide: ligne non objet")
        rows.append(cast(Mapping[str, object], row))
    return rows
