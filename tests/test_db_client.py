from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import cast

from postgrest.types import JSON
from src.db.client import OfferUpsert, SupabaseOfferRepository, hash_url


class FakeResponse:
    def __init__(self, data: object) -> None:
        self.data = data


class FakeExecutableQuery:
    def __init__(self, data: object) -> None:
        self._data = data

    def execute(self) -> FakeResponse:
        return FakeResponse(self._data)


class FakeFilterQuery:
    def __init__(self, data: object) -> None:
        self.limit_count: int | None = None
        self.filter_column: str | None = None
        self.filter_value: object | None = None
        self.gte_filter_column: str | None = None
        self.gte_filter_value: object | None = None
        self.null_filter_column: str | None = None
        self.null_filter_value: str | None = None
        self._data = data

    def eq(self, column: str, value: object) -> FakeFilterQuery:
        self.filter_column = column
        self.filter_value = value
        return self

    def gte(self, column: str, value: object) -> FakeFilterQuery:
        self.gte_filter_column = column
        self.gte_filter_value = value
        return self

    def is_(self, column: str, value: str) -> FakeFilterQuery:
        self.null_filter_column = column
        self.null_filter_value = value
        return self

    def limit(self, count: int) -> FakeExecutableQuery:
        self.limit_count = count
        return FakeExecutableQuery(self._data)


class FakeSelectQuery:
    def __init__(self, data: object) -> None:
        self.filter_column: str | None = None
        self.filter_value: object | None = None
        self.limit_count: int | None = None
        self._data = data
        self.filter_query = FakeFilterQuery(data)

    def eq(self, column: str, value: object) -> FakeFilterQuery:
        self.filter_column = column
        self.filter_value = value
        return self.filter_query

    def gte(self, column: str, value: object) -> FakeFilterQuery:
        return self.filter_query.gte(column, value)

    def limit(self, count: int) -> FakeExecutableQuery:
        self.limit_count = count
        return FakeExecutableQuery(self._data)


class FakeUpdateQuery:
    def __init__(self, data: object) -> None:
        self.filter_column: str | None = None
        self.filter_value: object | None = None
        self._data = data

    def eq(self, column: str, value: object) -> FakeExecutableQuery:
        self.filter_column = column
        self.filter_value = value
        return FakeExecutableQuery(self._data)


class FakeOffersTable:
    def __init__(self, row: dict[str, object]) -> None:
        self.upsert_payload: JSON | None = None
        self.upsert_conflict: str | None = None
        self.update_payload: JSON | None = None
        self.select_columns: str | None = None
        self.select_query = FakeSelectQuery([row])
        self.update_query = FakeUpdateQuery([row])
        self._row = row

    def upsert(self, json: JSON, *, on_conflict: str) -> FakeExecutableQuery:
        self.upsert_payload = json
        self.upsert_conflict = on_conflict
        return FakeExecutableQuery([self._row])

    def select(self, columns: str) -> FakeSelectQuery:
        self.select_columns = columns
        return self.select_query

    def update(self, json: JSON) -> FakeUpdateQuery:
        self.update_payload = json
        return self.update_query


class FakeSupabaseClient:
    def __init__(self, table: FakeOffersTable) -> None:
        self.table_name: str | None = None
        self.offers_table = table

    def table(self, table_name: str) -> FakeOffersTable:
        self.table_name = table_name
        return self.offers_table


def test_hash_url_is_deterministic() -> None:
    assert hash_url(" https://example.com/a ") == hash_url("https://example.com/a")


def test_offer_payload_computes_url_hash() -> None:
    offer = OfferUpsert(
        source="jobup",
        url="https://example.com/jobs/1",
        titre="Vendeur",
        date_publication=date(2026, 5, 25),
    )

    payload = offer.to_supabase_payload()

    assert payload["url_hash"] == hash_url("https://example.com/jobs/1")
    assert payload["date_publication"] == "2026-05-25"
    assert "entreprise" not in payload


def test_repository_upserts_on_url_hash() -> None:
    offer = OfferUpsert(
        source="jobup",
        url="https://example.com/jobs/2",
        titre="Vendeuse",
    )
    row: dict[str, object] = {
        "id": "5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        "source": offer.source,
        "url": offer.url,
        "url_hash": hash_url(offer.url),
        "titre": offer.titre,
        "statut": "nouveau",
    }
    table = FakeOffersTable(row)
    client = FakeSupabaseClient(table)
    repository = SupabaseOfferRepository(client)

    upserted = repository.upsert_offer(offer)
    reloaded = repository.get_by_url_hash(hash_url(offer.url))

    assert upserted.url_hash == hash_url(offer.url)
    assert reloaded is not None
    assert reloaded.url_hash == hash_url(offer.url)
    assert client.table_name == "offres"
    assert table.upsert_conflict == "url_hash"
    assert table.select_columns == "*"
    assert table.select_query.filter_column == "url_hash"


def test_repository_fetches_new_unscored_offers() -> None:
    row: dict[str, object] = {
        "id": "5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        "source": "jobup",
        "url": "https://example.com/jobs/3",
        "url_hash": hash_url("https://example.com/jobs/3"),
        "titre": "Vendeur",
        "statut": "nouveau",
    }
    table = FakeOffersTable(row)
    repository = SupabaseOfferRepository(FakeSupabaseClient(table))

    offers = repository.get_new_unscored_offers(limit=10)

    assert len(offers) == 1
    assert table.select_query.filter_column == "statut"
    assert table.select_query.filter_query.null_filter_column == "score_match"
    assert table.select_query.filter_query.null_filter_value == "null"
    assert table.select_query.filter_query.limit_count == 10


def test_repository_updates_offer_score() -> None:
    row: dict[str, object] = {
        "id": "5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        "source": "jobup",
        "url": "https://example.com/jobs/4",
        "url_hash": hash_url("https://example.com/jobs/4"),
        "titre": "Vendeur",
        "score_match": 72,
        "raison_score": "Bonne proximite et profil vente.",
        "statut": "nouveau",
    }
    table = FakeOffersTable(row)
    repository = SupabaseOfferRepository(FakeSupabaseClient(table))

    updated = repository.update_offer_score(
        offer_id="5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        score_match=72,
        raison_score="Bonne proximite et profil vente.",
    )

    assert updated.score_match == 72
    assert table.update_payload == {
        "score_match": 72,
        "raison_score": "Bonne proximite et profil vente.",
    }
    assert table.update_query.filter_column == "id"


def test_repository_fetches_letter_candidates_without_existing_letter() -> None:
    row: dict[str, object] = {
        "id": "5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        "source": "jobup",
        "url": "https://example.com/jobs/5",
        "url_hash": hash_url("https://example.com/jobs/5"),
        "titre": "Conseiller de vente",
        "score_match": 71,
        "statut": "nouveau",
    }
    table = FakeOffersTable(row)
    repository = SupabaseOfferRepository(FakeSupabaseClient(table))

    offers = repository.get_letter_candidates(limit=3)

    assert len(offers) == 1
    assert table.select_query.filter_query.gte_filter_column == "score_match"
    assert table.select_query.filter_query.gte_filter_value == 65
    assert table.select_query.filter_query.null_filter_column == "lettre_generee"
    assert table.select_query.filter_query.null_filter_value == "null"
    assert table.select_query.filter_query.limit_count == 3


def test_repository_fetches_letter_candidates_with_force_keeps_existing_letters() -> None:
    row: dict[str, object] = {
        "id": "5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        "source": "jobup",
        "url": "https://example.com/jobs/6",
        "url_hash": hash_url("https://example.com/jobs/6"),
        "titre": "Conseiller de vente",
        "score_match": 71,
        "lettre_generee": "{}",
        "statut": "nouveau",
    }
    table = FakeOffersTable(row)
    repository = SupabaseOfferRepository(FakeSupabaseClient(table))

    offers = repository.get_letter_candidates(limit=3, force=True)

    assert len(offers) == 1
    assert table.select_query.filter_query.gte_filter_column == "score_match"
    assert table.select_query.filter_query.null_filter_column is None


def test_repository_updates_offer_letter() -> None:
    row: dict[str, object] = {
        "id": "5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        "source": "jobup",
        "url": "https://example.com/jobs/7",
        "url_hash": hash_url("https://example.com/jobs/7"),
        "titre": "Conseiller de vente",
        "lettre_generee": "{\"objet\":\"Candidature\"}",
        "statut": "nouveau",
    }
    table = FakeOffersTable(row)
    repository = SupabaseOfferRepository(FakeSupabaseClient(table))

    updated = repository.update_offer_letter(
        "5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        "{\"objet\":\"Candidature\"}",
    )

    assert updated.lettre_generee == "{\"objet\":\"Candidature\"}"
    assert table.update_payload == {"lettre_generee": "{\"objet\":\"Candidature\"}"}
    assert table.update_query.filter_column == "id"


def test_repository_marks_offer_sent() -> None:
    row: dict[str, object] = {
        "id": "5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        "source": "jobup",
        "url": "https://example.com/jobs/8",
        "url_hash": hash_url("https://example.com/jobs/8"),
        "titre": "Conseiller de vente",
        "statut": "envoye",
        "email_destinataire": "rh@example.com",
        "envoye_at": "2026-05-25T10:00:00+00:00",
    }
    table = FakeOffersTable(row)
    repository = SupabaseOfferRepository(FakeSupabaseClient(table))

    updated = repository.update_offer_sent(
        "5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        email_destinataire="rh@example.com",
    )

    assert updated.statut == "envoye"
    assert updated.email_destinataire == "rh@example.com"
    assert table.update_payload is not None
    update_payload = cast(Mapping[str, object], table.update_payload)
    assert update_payload["statut"] == "envoye"
    assert update_payload["email_destinataire"] == "rh@example.com"


def test_repository_reads_default_parameters_when_empty() -> None:
    table = FakeOffersTable({})
    table.select_query = FakeSelectQuery([])
    repository = SupabaseOfferRepository(FakeSupabaseClient(table))

    parameters = repository.get_parameters()

    assert parameters.actif is False
    assert parameters.max_par_jour == 5
    assert parameters.score_seuil == 65
