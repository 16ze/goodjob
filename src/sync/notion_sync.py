from __future__ import annotations

import argparse
from typing import Protocol

from pydantic import ValidationError

from src.ai.generate_letter import LetterResult
from src.db.client import OfferRow, SupabaseOfferRepository
from src.integrations.notion import (
    NotionPageResult,
    NotionSyncClient,
    NotionSyncError,
)
from src.lib.config import get_settings
from src.lib.logging import configure_logging, get_logger

DEFAULT_LIMIT = 10

logger = get_logger(__name__)


class NotionSyncRepository(Protocol):
    """Surface minimale du repository utilisee par l'orchestrateur Notion."""

    def get_notion_sync_candidates(self, limit: int) -> list[OfferRow]: ...

    def update_offer_notion_page(self, offer_id: str, notion_page_id: str) -> OfferRow: ...


class NotionSyncCreator(Protocol):
    """Surface minimale du client Notion utilisee par l'orchestrateur."""

    def create_candidature_page(
        self,
        offer: OfferRow,
        letter: LetterResult,
        *,
        gmail_draft_url: str | None = None,
    ) -> NotionPageResult: ...


def sync_pending_offers_to_notion(
    repository: NotionSyncRepository,
    client: NotionSyncCreator,
    *,
    limit: int = DEFAULT_LIMIT,
    dry_run: bool = False,
) -> int:
    """Cree une page Notion par offre prete, isole les echecs ligne par ligne."""

    offers = repository.get_notion_sync_candidates(limit)
    synced_count = 0
    failed_count = 0
    skipped_count = 0

    for offer in offers:
        if offer.lettre_generee is None:
            logger.info(
                "notion_sync_skipped",
                offer_id=offer.id,
                reason="lettre_generee_missing",
            )
            skipped_count += 1
            continue

        try:
            letter = LetterResult.model_validate_json(offer.lettre_generee)
        except ValidationError as exc:
            logger.warning(
                "notion_sync_letter_parse_failed",
                offer_id=offer.id,
                reason=str(exc),
            )
            failed_count += 1
            continue

        try:
            result = client.create_candidature_page(offer, letter)
        except NotionSyncError as exc:
            logger.warning(
                "notion_sync_failed",
                offer_id=offer.id,
                reason=str(exc),
            )
            failed_count += 1
            continue

        if not dry_run:
            repository.update_offer_notion_page(offer.id, result.page_id)
        synced_count += 1

    logger.info(
        "notion_sync_completed",
        offers_found=len(offers),
        notion_pages_created=synced_count,
        notion_pages_failed=failed_count,
        notion_pages_skipped=skipped_count,
        dry_run=dry_run,
    )
    return synced_count


def main() -> None:
    """Point d'entree CLI: cree les pages Notion pour les offres deja scorees."""

    parser = argparse.ArgumentParser(
        description="Synchronise vers Notion les offres scorees pretes.",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    repository = SupabaseOfferRepository.from_settings(settings)
    client = NotionSyncClient.from_settings(settings)
    sync_pending_offers_to_notion(
        repository,
        client,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
