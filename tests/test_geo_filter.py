from __future__ import annotations

from src.filtering.geo import contains_target_city, is_in_target_area
from src.scrapers.base import Offer


def make_offer(
    *,
    location: str | None = None,
    postal_code: str | None = None,
    raw_description: str = "Poste de vente.",
) -> Offer:
    return Offer(
        source="jobup",
        url="https://example.com/jobs/1",
        url_hash="hash",
        title="Vendeur",
        location=location,
        postal_code=postal_code,
        raw_description=raw_description,
    )


def test_accepts_target_postal_code_ranges() -> None:
    assert is_in_target_area(make_offer(postal_code="2800"))
    assert is_in_target_area(make_offer(postal_code="2400"))
    assert is_in_target_area(make_offer(postal_code="2610"))


def test_accepts_target_cities_even_outside_postal_ranges() -> None:
    offer = make_offer(location="2300 La Chaux-de-Fonds NE", postal_code="2300")

    assert is_in_target_area(offer)


def test_accepts_city_names_with_missing_accents_and_hyphen_variants() -> None:
    assert contains_target_city("Delemont")
    assert contains_target_city("Saint Imier")
    assert contains_target_city("La Chaux de Fonds")


def test_rejects_offer_outside_target_area() -> None:
    offer = make_offer(location="1200 Genève GE", postal_code="1200")

    assert not is_in_target_area(offer)
