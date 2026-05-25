from __future__ import annotations

from datetime import date
from typing import cast

import pytest
from notion_client.errors import RequestTimeoutError
from src.ai.generate_letter import REQUIRED_PERMIS_G_SENTENCE, LetterResult
from src.db.client import OfferRow, hash_url
from src.integrations.notion import (
    LETTER_PROPERTY_LIMIT,
    NotionAPILike,
    NotionPageResult,
    NotionSyncClient,
    NotionSyncError,
    build_page_properties,
)


class FakeNotionPages:
    """Surface minimale d'un client Notion testable hors reseau."""

    def __init__(
        self,
        response: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response: dict[str, object] = response or {}
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeNotionAPI:
    def __init__(self, pages: FakeNotionPages) -> None:
        self.pages = pages


def make_offer(
    *,
    score_match: int | None = 72,
    titre: str = "Conseiller de vente",
    entreprise: str | None = "Commerce SA",
    lieu: str | None = "Delémont",
    code_postal: str | None = "2800",
    raison_score: str | None = "Bonne proximité",
    publication_date: date | None = date(2026, 5, 25),
    source: str = "jobup",
) -> OfferRow:
    url = "https://example.com/jobs/conseiller-vente"
    return OfferRow(
        id="5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        source=source,
        url=url,
        url_hash=hash_url(url),
        titre=titre,
        entreprise=entreprise,
        lieu=lieu,
        code_postal=code_postal,
        description_brute="Conseil client.",
        score_match=score_match,
        raison_score=raison_score,
        date_publication=publication_date,
        statut="nouveau",
    )


def make_letter(lettre: str | None = None) -> LetterResult:
    return LetterResult(
        objet="Candidature au poste de Conseiller de vente",
        lettre=lettre
        or (
            "Madame, Monsieur,\n\n"
            "Je vous adresse ma candidature.\n\n"
            f"{REQUIRED_PERMIS_G_SENTENCE}\n\n"
            "Salutations."
        ),
        notes_personnalisation=["Delémont"],
    )


def test_build_page_properties_includes_all_known_fields() -> None:
    properties = build_page_properties(make_offer(), make_letter(), gmail_draft_url=None)

    assert properties["Titre"] == {
        "title": [{"type": "text", "text": {"content": "Conseiller de vente"}}],
    }
    assert properties["Score"] == {"number": 72}
    assert properties["URL offre"] == {"url": "https://example.com/jobs/conseiller-vente"}
    assert properties["Source"] == {"select": {"name": "jobup"}}
    assert properties["Entreprise"] == {
        "rich_text": [{"type": "text", "text": {"content": "Commerce SA"}}],
    }
    assert properties["Lieu"] == {
        "rich_text": [{"type": "text", "text": {"content": "Delémont"}}],
    }
    assert properties["Code postal"] == {
        "rich_text": [{"type": "text", "text": {"content": "2800"}}],
    }
    assert properties["Date publication"] == {"date": {"start": "2026-05-25"}}
    assert "Date detection" in properties


def test_build_page_properties_omits_optional_fields_when_none() -> None:
    offer = make_offer(
        entreprise=None,
        lieu=None,
        code_postal=None,
        raison_score=None,
        publication_date=None,
    )

    properties = build_page_properties(offer, make_letter(), gmail_draft_url=None)

    assert "Entreprise" not in properties
    assert "Lieu" not in properties
    assert "Code postal" not in properties
    assert "Raison score" not in properties
    assert "Date publication" not in properties
    assert "Gmail draft" not in properties


def test_build_page_properties_truncates_long_letter_to_notion_limit() -> None:
    long_text = "A" * (LETTER_PROPERTY_LIMIT + 500)
    letter = make_letter(
        lettre=f"Madame, Monsieur,\n\n{long_text}\n\n{REQUIRED_PERMIS_G_SENTENCE}",
    )

    properties = build_page_properties(make_offer(), letter, gmail_draft_url=None)

    rich_text = properties["Lettre"]
    assert isinstance(rich_text, dict)
    content = rich_text["rich_text"][0]["text"]["content"]
    assert len(content) <= LETTER_PROPERTY_LIMIT
    assert content.endswith("…")


def test_build_page_properties_normalizes_unknown_source_to_autres() -> None:
    offer = make_offer(source="unknown.com")

    properties = build_page_properties(offer, make_letter(), gmail_draft_url=None)

    assert properties["Source"] == {"select": {"name": "autres"}}


def test_build_page_properties_includes_gmail_draft_when_provided() -> None:
    properties = build_page_properties(
        make_offer(),
        make_letter(),
        gmail_draft_url="https://mail.google.com/mail/u/0/#drafts/abc123",
    )

    assert properties["Gmail draft"] == {
        "url": "https://mail.google.com/mail/u/0/#drafts/abc123",
    }


def test_create_candidature_page_returns_page_id_and_url() -> None:
    pages = FakeNotionPages(
        response={
            "id": "11111111-2222-3333-4444-555555555555",
            "url": "https://www.notion.so/title-1111222233334444",
        },
    )
    client = NotionSyncClient(
        cast(NotionAPILike, FakeNotionAPI(pages)),
        database_id="db-id",
    )

    result = client.create_candidature_page(make_offer(), make_letter())

    assert isinstance(result, NotionPageResult)
    assert result.page_id == "11111111-2222-3333-4444-555555555555"
    assert result.page_url == "https://www.notion.so/title-1111222233334444"
    assert len(pages.calls) == 1
    assert pages.calls[0]["parent"] == {"database_id": "db-id"}


def test_create_candidature_page_raises_NotionSyncError_on_timeout() -> None:
    pages = FakeNotionPages(error=RequestTimeoutError("notion took too long"))
    client = NotionSyncClient(
        cast(NotionAPILike, FakeNotionAPI(pages)),
        database_id="db-id",
    )

    with pytest.raises(NotionSyncError):
        client.create_candidature_page(make_offer(), make_letter())


def test_create_candidature_page_raises_NotionSyncError_on_empty_response_id() -> None:
    pages = FakeNotionPages(response={"id": "", "url": "https://example.com"})
    client = NotionSyncClient(
        cast(NotionAPILike, FakeNotionAPI(pages)),
        database_id="db-id",
    )

    with pytest.raises(NotionSyncError):
        client.create_candidature_page(make_offer(), make_letter())
