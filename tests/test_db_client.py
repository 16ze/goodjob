from __future__ import annotations

from datetime import date

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
        self._data = data

    def limit(self, count: int) -> FakeExecutableQuery:
        self.limit_count = count
        return FakeExecutableQuery(self._data)


class FakeSelectQuery:
    def __init__(self, data: object) -> None:
        self.filter_column: str | None = None
        self.filter_value: str | None = None
        self.filter_query = FakeFilterQuery(data)

    def eq(self, column: str, value: str) -> FakeFilterQuery:
        self.filter_column = column
        self.filter_value = value
        return self.filter_query


class FakeOffersTable:
    def __init__(self, row: dict[str, object]) -> None:
        self.upsert_payload: JSON | None = None
        self.upsert_conflict: str | None = None
        self.select_columns: str | None = None
        self.select_query = FakeSelectQuery([row])
        self._row = row

    def upsert(self, json: JSON, *, on_conflict: str) -> FakeExecutableQuery:
        self.upsert_payload = json
        self.upsert_conflict = on_conflict
        return FakeExecutableQuery([self._row])

    def select(self, columns: str) -> FakeSelectQuery:
        self.select_columns = columns
        return self.select_query


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
