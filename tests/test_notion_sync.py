from __future__ import annotations

import json
from typing import cast

from src.ai.generate_letter import REQUIRED_PERMIS_G_SENTENCE, LetterResult
from src.db.client import OfferRow, hash_url
from src.integrations.notion import (
    NotionPageResult,
    NotionSyncClient,
    NotionSyncError,
)
from src.sync.notion_sync import sync_pending_offers_to_notion


class FakeNotionSyncRepository:
    """Repository en memoire qui suit les appels pour les assertions de test."""

    def __init__(self, offers: list[OfferRow]) -> None:
        self.offers = offers
        self.candidate_calls: list[int] = []
        self.notion_updates: list[tuple[str, str]] = []

    def get_notion_sync_candidates(self, limit: int) -> list[OfferRow]:
        self.candidate_calls.append(limit)
        return list(self.offers[:limit])

    def update_offer_notion_page(self, offer_id: str, notion_page_id: str) -> OfferRow:
        self.notion_updates.append((offer_id, notion_page_id))
        return self.offers[0].model_copy(update={"notion_page_id": notion_page_id})


class FakeNotionClient:
    """Client Notion testable qui renvoie un page_id sequentiel ou une erreur."""

    def __init__(
        self,
        results: list[NotionPageResult | Exception] | None = None,
    ) -> None:
        self.results: list[NotionPageResult | Exception] = list(results or [])
        self.calls: list[tuple[str, LetterResult, str | None]] = []

    def create_candidature_page(
        self,
        offer: OfferRow,
        letter: LetterResult,
        *,
        gmail_draft_url: str | None = None,
    ) -> NotionPageResult:
        self.calls.append((offer.id, letter, gmail_draft_url))
        next_result = self.results.pop(0)
        if isinstance(next_result, Exception):
            raise next_result
        return next_result


def make_offer(
    *,
    offer_id: str = "offer-1",
    lettre_generee: str | None = None,
    notion_page_id: str | None = None,
) -> OfferRow:
    url = f"https://example.com/jobs/{offer_id}"
    return OfferRow(
        id=offer_id,
        source="jobup",
        url=url,
        url_hash=hash_url(url),
        titre="Conseiller de vente",
        entreprise="Commerce SA",
        lieu="Delémont",
        code_postal="2800",
        description_brute="Conseil client.",
        score_match=72,
        raison_score="Bonne proximité.",
        lettre_generee=lettre_generee,
        notion_page_id=notion_page_id,
        statut="nouveau",
    )


def valid_letter_json() -> str:
    return json.dumps(
        {
            "objet": "Candidature au poste de Conseiller de vente",
            "lettre": (
                f"Madame, Monsieur, je vous adresse ma candidature. "
                f"{REQUIRED_PERMIS_G_SENTENCE} Salutations."
            ),
            "notes_personnalisation": [],
        },
    )


def make_page_result(page_id: str = "page-1") -> NotionPageResult:
    return NotionPageResult(
        page_id=page_id,
        page_url=f"https://www.notion.so/{page_id}",
    )


def test_skips_offer_without_letter_without_creating_notion_page() -> None:
    repository = FakeNotionSyncRepository([make_offer(lettre_generee=None)])
    client = FakeNotionClient()

    count = sync_pending_offers_to_notion(
        repository,
        client,
        limit=5,
    )

    assert count == 0
    assert client.calls == []
    assert repository.notion_updates == []


def test_skips_offer_with_invalid_letter_json() -> None:
    repository = FakeNotionSyncRepository(
        [make_offer(lettre_generee='{"invalid": "json"}')],
    )
    client = FakeNotionClient()

    count = sync_pending_offers_to_notion(
        repository,
        client,
        limit=5,
    )

    assert count == 0
    assert client.calls == []
    assert repository.notion_updates == []


def test_dry_run_creates_page_but_does_not_persist_id() -> None:
    repository = FakeNotionSyncRepository(
        [make_offer(lettre_generee=valid_letter_json())],
    )
    client = FakeNotionClient(results=[make_page_result()])

    count = sync_pending_offers_to_notion(
        repository,
        cast(NotionSyncClient, client),
        limit=5,
        dry_run=True,
    )

    assert count == 1
    assert len(client.calls) == 1
    assert repository.notion_updates == []


def test_persists_notion_page_id_on_successful_sync() -> None:
    repository = FakeNotionSyncRepository(
        [make_offer(offer_id="offer-1", lettre_generee=valid_letter_json())],
    )
    client = FakeNotionClient(results=[make_page_result("page-xyz")])

    count = sync_pending_offers_to_notion(
        repository,
        cast(NotionSyncClient, client),
        limit=5,
    )

    assert count == 1
    assert repository.notion_updates == [("offer-1", "page-xyz")]


def test_loop_continues_when_one_offer_fails_notion_sync() -> None:
    repository = FakeNotionSyncRepository(
        [
            make_offer(offer_id="offer-fail", lettre_generee=valid_letter_json()),
            make_offer(offer_id="offer-ok", lettre_generee=valid_letter_json()),
        ],
    )
    client = FakeNotionClient(
        results=[
            NotionSyncError("Notion refused this one"),
            make_page_result("page-ok"),
        ],
    )

    count = sync_pending_offers_to_notion(
        repository,
        cast(NotionSyncClient, client),
        limit=5,
    )

    assert count == 1
    assert repository.notion_updates == [("offer-ok", "page-ok")]
    assert len(client.calls) == 2
