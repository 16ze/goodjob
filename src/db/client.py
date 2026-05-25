from __future__ import annotations

from collections.abc import Mapping
from datetime import date
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
    """Query builder filtrable Supabase."""

    def limit(self, count: int) -> ExecutableQuery: ...


class SelectQuery(Protocol):
    """Query builder select Supabase."""

    def eq(self, column: str, value: str) -> FilterQuery: ...


class OffersTable(Protocol):
    """Surface PostgREST strictement nécessaire à la table `offres`."""

    def upsert(self, json: JSON, *, on_conflict: str) -> ExecutableQuery: ...

    def select(self, columns: str) -> SelectQuery: ...


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
