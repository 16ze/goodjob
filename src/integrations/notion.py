from __future__ import annotations

from datetime import date
from typing import Protocol, cast

from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError, RequestTimeoutError
from pydantic import BaseModel, ConfigDict

from src.ai.generate_letter import LetterResult
from src.db.client import OfferRow
from src.lib.config import Settings, get_settings
from src.lib.logging import get_logger

NOTION_RICH_TEXT_LIMIT = 2000
LETTER_PROPERTY_LIMIT = 1900  # marge pour suffix d'ellipse
SUPPORTED_SOURCES: frozenset[str] = frozenset(
    {"jobup", "jobs.ch", "indeed", "manor", "migros", "coop"},
)

logger = get_logger(__name__)


class NotionPageResult(BaseModel):
    """Resultat structure de la creation d'une page Notion."""

    page_id: str
    page_url: str

    model_config = ConfigDict(frozen=True)


class NotionPagesAPI(Protocol):
    """Surface minimale Notion utilisee pour creer une page."""

    def create(self, **kwargs: object) -> dict[str, object]: ...


class NotionAPILike(Protocol):
    """Client Notion injectable pour tester sans reseau."""

    pages: NotionPagesAPI


class NotionSyncError(RuntimeError):
    """Erreur explicite quand Notion refuse la creation d'une page."""


class NotionSyncClient:
    """Service de creation de pages Notion pour les candidatures pretes."""

    def __init__(self, api: NotionAPILike, *, database_id: str) -> None:
        self._api = api
        self._database_id = database_id

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> NotionSyncClient:
        """Instancie le client a partir de la configuration validee."""

        resolved = settings or get_settings()
        api = NotionClient(auth=resolved.notion_token.get_secret_value())
        return cls(cast(NotionAPILike, api), database_id=resolved.notion_database_id)

    def create_candidature_page(
        self,
        offer: OfferRow,
        letter: LetterResult,
        *,
        gmail_draft_url: str | None = None,
    ) -> NotionPageResult:
        """Cree une page Notion pour une offre avec lettre validee."""

        properties = build_page_properties(
            offer,
            letter,
            gmail_draft_url=gmail_draft_url,
        )
        try:
            response = self._api.pages.create(
                parent={"database_id": self._database_id},
                properties=properties,
            )
        except (APIResponseError, RequestTimeoutError) as exc:
            logger.exception("notion_page_creation_failed", offer_id=offer.id)
            raise NotionSyncError("Notion a refuse la creation de page") from exc

        page_id = _str_or_raise(response.get("id"), "id")
        page_url = _str_or_raise(response.get("url"), "url")
        logger.info(
            "notion_page_created",
            offer_id=offer.id,
            notion_page_id=page_id,
            notion_page_url=page_url,
        )
        return NotionPageResult(page_id=page_id, page_url=page_url)


def build_page_properties(
    offer: OfferRow,
    letter: LetterResult,
    *,
    gmail_draft_url: str | None,
) -> dict[str, object]:
    """Construit le payload `properties` Notion typage-friendly."""

    properties: dict[str, object] = {
        "Titre": _title_property(offer.titre or "Offre sans titre"),
        "Score": _number_property(offer.score_match),
        "URL offre": _url_property(offer.url),
        "Source": _select_property(_normalize_source(offer.source)),
        "Lettre": _rich_text_property(_truncate(letter.lettre, LETTER_PROPERTY_LIMIT)),
        "Date detection": _date_property(date.today()),
    }
    if offer.entreprise:
        properties["Entreprise"] = _rich_text_property(offer.entreprise)
    if offer.lieu:
        properties["Lieu"] = _rich_text_property(offer.lieu)
    if offer.code_postal:
        properties["Code postal"] = _rich_text_property(offer.code_postal)
    if offer.raison_score:
        properties["Raison score"] = _rich_text_property(
            _truncate(offer.raison_score, NOTION_RICH_TEXT_LIMIT),
        )
    if offer.date_publication is not None:
        properties["Date publication"] = _date_property(offer.date_publication)
    if gmail_draft_url:
        properties["Gmail draft"] = _url_property(gmail_draft_url)
    return properties


def _normalize_source(source: str) -> str:
    lower = source.strip().lower()
    return lower if lower in SUPPORTED_SOURCES else "autres"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"  # caractere ellipse U+2026


def _title_property(value: str) -> dict[str, object]:
    return {"title": [{"type": "text", "text": {"content": value}}]}


def _rich_text_property(value: str) -> dict[str, object]:
    return {"rich_text": [{"type": "text", "text": {"content": value}}]}


def _number_property(value: int | None) -> dict[str, object]:
    return {"number": value}


def _url_property(value: str) -> dict[str, object]:
    return {"url": value}


def _select_property(value: str) -> dict[str, object]:
    return {"select": {"name": value}}


def _date_property(value: date) -> dict[str, object]:
    return {"date": {"start": value.isoformat()}}


def _str_or_raise(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise NotionSyncError(f"Notion a renvoye un champ `{field}` non-string ou vide")
    return value
