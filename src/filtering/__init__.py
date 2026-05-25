from __future__ import annotations

from collections.abc import Iterable

from src.filtering.geo import is_in_target_area
from src.filtering.permis import requires_valid_permis_g
from src.scrapers.base import Offer


def is_eligible_offer(offer: Offer) -> bool:
    """Retourne vrai si l'offre respecte les filtres bloquants actuels."""

    return is_in_target_area(offer) and not requires_valid_permis_g(offer)


def filter_eligible_offers(offers: Iterable[Offer]) -> list[Offer]:
    """Conserve uniquement les offres exploitables par Bryan."""

    return [offer for offer in offers if is_eligible_offer(offer)]


__all__ = [
    "filter_eligible_offers",
    "is_eligible_offer",
    "is_in_target_area",
    "requires_valid_permis_g",
]
