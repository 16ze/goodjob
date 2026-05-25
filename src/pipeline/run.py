from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from src.db.client import OfferUpsert, SupabaseOfferRepository
from src.filtering.geo import is_in_target_area
from src.filtering.permis import requires_valid_permis_g
from src.lib.config import get_database_settings
from src.lib.logging import configure_logging, get_logger
from src.scrapers.base import Offer, SearchQuery
from src.scrapers.jobs import JobsScraper
from src.scrapers.jobup import JobupScraper

DEFAULT_MAX_RESULTS = 50

logger = get_logger(__name__)


class OfferScraper(Protocol):
    """Surface minimale d'un scraper utilisée par le pipeline."""

    async def fetch_and_parse(self, query: SearchQuery) -> list[Offer]: ...


class OfferRepository(Protocol):
    """Surface minimale du repository utilisée par le pipeline."""

    def upsert_offer(self, offer: OfferUpsert) -> object: ...


class PipelineReport(BaseModel):
    """Rapport JSON stable pour valider une ingestion."""

    scraped: int
    kept: int
    rejected: int
    upserted: int
    dry_run: bool
    rejections: dict[str, int]

    model_config = ConfigDict(frozen=True)


@dataclass(frozen=True)
class ClassifiedOffer:
    """Offre enrichie du statut DB décidé par les filtres."""

    offer: Offer
    statut: str
    rejection_reason: str | None = None


def classify_offer(offer: Offer) -> ClassifiedOffer:
    """Applique les filtres Ticket 4 et décide le statut DB."""

    if not is_in_target_area(offer):
        return ClassifiedOffer(offer=offer, statut="ko_auto", rejection_reason="hors_zone_geo")
    if requires_valid_permis_g(offer):
        return ClassifiedOffer(
            offer=offer,
            statut="ko_auto",
            rejection_reason="permis_g_valide_obligatoire",
        )
    return ClassifiedOffer(offer=offer, statut="nouveau")


def offer_to_upsert(classified_offer: ClassifiedOffer) -> OfferUpsert:
    """Convertit le modèle scraper vers le payload Supabase."""

    offer = classified_offer.offer
    return OfferUpsert(
        source=offer.source,
        url=str(offer.url),
        url_hash=offer.url_hash,
        titre=offer.title,
        entreprise=offer.company,
        lieu=offer.location,
        code_postal=offer.postal_code,
        date_publication=offer.publication_date,
        description_brute=offer.raw_description,
        statut=classified_offer.statut,
    )


async def ingest_jobup_offers(
    scraper: OfferScraper,
    repository: OfferRepository | None,
    *,
    query: SearchQuery,
    dry_run: bool,
) -> PipelineReport:
    """Scrape jobup, applique les filtres, puis upsert en DB hors dry-run."""

    offers = await scraper.fetch_and_parse(query)
    classified_offers = [classify_offer(offer) for offer in offers]
    rejections = _count_rejections(classified_offers)
    upserted = 0

    if not dry_run and repository is None:
        raise ValueError("repository obligatoire quand dry_run=False")

    if not dry_run and repository is not None:
        for classified_offer in classified_offers:
            repository.upsert_offer(offer_to_upsert(classified_offer))
            upserted += 1

    kept = sum(1 for item in classified_offers if item.statut == "nouveau")
    report = PipelineReport(
        scraped=len(offers),
        kept=kept,
        rejected=len(offers) - kept,
        upserted=upserted,
        dry_run=dry_run,
        rejections=rejections,
    )
    logger.info("pipeline_ingestion_completed", **report.model_dump())
    return report


def _count_rejections(classified_offers: Sequence[ClassifiedOffer]) -> dict[str, int]:
    rejections: dict[str, int] = {}
    for classified_offer in classified_offers:
        if classified_offer.rejection_reason is None:
            continue
        rejections[classified_offer.rejection_reason] = (
            rejections.get(classified_offer.rejection_reason, 0) + 1
        )
    return rejections


async def async_main(max_results: int, dry_run: bool) -> None:
    settings = get_database_settings()
    configure_logging(settings.log_level)
    repository = None if dry_run else SupabaseOfferRepository.from_settings(settings)
    query = SearchQuery(max_results=max_results)
    scrapers: list[OfferScraper] = [JobupScraper(), JobsScraper()]
    for scraper in scrapers:
        await ingest_jobup_offers(scraper, repository, query=query, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion jobup + jobs.ch vers Supabase.")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_RESULTS, dest="max_results")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    asyncio.run(async_main(max_results=args.max_results, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
