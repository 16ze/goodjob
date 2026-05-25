from __future__ import annotations

from src.filtering import filter_eligible_offers, is_eligible_offer
from src.filtering.permis import requires_valid_permis_g
from src.scrapers.base import Offer


def make_offer(
    raw_description: str,
    *,
    location: str = "2800 Delémont JU",
    postal_code: str | None = "2800",
) -> Offer:
    return Offer(
        source="jobup",
        url=f"https://example.com/jobs/{abs(hash(raw_description))}",
        url_hash="hash",
        title="Vendeur",
        location=location,
        postal_code=postal_code,
        raw_description=raw_description,
    )


def test_excludes_explicit_valid_permis_g_requirement() -> None:
    offer = make_offer("Permis G valide obligatoire pour commencer immédiatement.")

    assert requires_valid_permis_g(offer)
    assert not is_eligible_offer(offer)


def test_excludes_explicit_valid_frontier_permit_requirement() -> None:
    offer = make_offer("Permis frontalier valable requis.")

    assert requires_valid_permis_g(offer)


def test_excludes_valid_permit_requirement_without_driving_context() -> None:
    offer = make_offer("Permis en cours de validité exigé.")

    assert requires_valid_permis_g(offer)


def test_keeps_permis_g_when_only_accepted() -> None:
    offer = make_offer("Permis G accepté, autres profils bienvenus.")

    assert not requires_valid_permis_g(offer)
    assert is_eligible_offer(offer)


def test_keeps_driving_license_requirement() -> None:
    offer = make_offer("Permis de conduire en cours de validité exigé.")

    assert not requires_valid_permis_g(offer)


def test_filter_eligible_offers_combines_geo_and_permis_rules() -> None:
    eligible = make_offer("Permis G accepté.", location="2300 La Chaux-de-Fonds NE")
    blocked_permis = make_offer("Permis G valide obligatoire.")
    blocked_geo = make_offer("Poste de vente.", location="1200 Genève GE", postal_code="1200")

    assert filter_eligible_offers([eligible, blocked_permis, blocked_geo]) == [eligible]
