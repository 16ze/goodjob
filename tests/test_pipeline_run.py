from __future__ import annotations

from datetime import date

import pytest
from src.db.client import OfferUpsert
from src.pipeline.run import classify_offer, ingest_jobup_offers, offer_to_upsert
from src.scrapers.base import Offer, SearchQuery


class FakeScraper:
    def __init__(self, offers: list[Offer]) -> None:
        self.offers = offers

    async def fetch_and_parse(self, query: SearchQuery) -> list[Offer]:
        return self.offers[: query.max_results]


class FakeRepository:
    def __init__(self) -> None:
        self.upserted: list[OfferUpsert] = []

    def upsert_offer(self, offer: OfferUpsert) -> object:
        self.upserted.append(offer)
        return object()


def make_offer(
    *,
    url: str = "https://www.jobup.ch/fr/emplois/detail/test/",
    title: str = "Vendeur",
    location: str | None = "2800 Delémont",
    postal_code: str | None = "2800",
    raw_description: str = "Conseil client et vente en magasin.",
) -> Offer:
    return Offer(
        source="jobup",
        url=url,
        url_hash="hash",
        title=title,
        company="Entreprise Test",
        location=location,
        postal_code=postal_code,
        publication_date=date(2026, 5, 25),
        raw_description=raw_description,
    )


def test_classify_offer_keeps_target_area_without_blocking_permis() -> None:
    classified = classify_offer(make_offer())

    assert classified.statut == "nouveau"
    assert classified.rejection_reason is None


def test_classify_offer_rejects_outside_target_area() -> None:
    classified = classify_offer(
        make_offer(location="1003 Lausanne", postal_code="1003"),
    )

    assert classified.statut == "ko_auto"
    assert classified.rejection_reason == "hors_zone_geo"


def test_classify_offer_rejects_blocking_permis_g() -> None:
    classified = classify_offer(
        make_offer(raw_description="Permis G valide obligatoire pour commencer."),
    )

    assert classified.statut == "ko_auto"
    assert classified.rejection_reason == "permis_g_valide_obligatoire"


def test_offer_to_upsert_preserves_status_and_fields() -> None:
    offer = make_offer()
    classified = classify_offer(offer)

    payload = offer_to_upsert(classified)

    assert payload.source == "jobup"
    assert payload.titre == "Vendeur"
    assert payload.statut == "nouveau"
    assert payload.code_postal == "2800"
    assert payload.date_publication == date(2026, 5, 25)


@pytest.mark.anyio
async def test_ingest_jobup_offers_dry_run_does_not_upsert() -> None:
    repository = FakeRepository()
    scraper = FakeScraper([make_offer(), make_offer(url="https://example.com/lausanne")])

    report = await ingest_jobup_offers(
        scraper,
        repository,
        query=SearchQuery(max_results=2),
        dry_run=True,
    )

    assert report.scraped == 2
    assert report.upserted == 0
    assert repository.upserted == []


@pytest.mark.anyio
async def test_ingest_jobup_offers_upserts_kept_and_rejected() -> None:
    repository = FakeRepository()
    scraper = FakeScraper(
        [
            make_offer(),
            make_offer(
                url="https://www.jobup.ch/fr/emplois/detail/outside/",
                location="1003 Lausanne",
                postal_code="1003",
            ),
        ],
    )

    report = await ingest_jobup_offers(
        scraper,
        repository,
        query=SearchQuery(max_results=2),
        dry_run=False,
    )

    assert report.scraped == 2
    assert report.kept == 1
    assert report.rejected == 1
    assert report.upserted == 2
    assert report.rejections == {"hors_zone_geo": 1}
    assert [offer.statut for offer in repository.upserted] == ["nouveau", "ko_auto"]
