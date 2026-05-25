from __future__ import annotations

import re
import unicodedata

from src.scrapers.base import Offer

TARGET_POSTAL_CODE_RANGES: tuple[range, ...] = (
    range(2800, 2901),
    range(2350, 2401),
    range(2520, 2611),
)

TARGET_CITIES: tuple[str, ...] = (
    "Delémont",
    "Porrentruy",
    "Saignelégier",
    "Moutier",
    "Tavannes",
    "Saint-Imier",
    "La Chaux-de-Fonds",
)

POSTAL_CODE_PATTERN = re.compile(r"\b([1-9]\d{3})\b")


def is_in_target_area(offer: Offer) -> bool:
    """Vérifie si l'offre est dans la zone géographique cible."""

    if offer.postal_code is not None and is_target_postal_code(offer.postal_code):
        return True
    return contains_target_city(_offer_geo_text(offer)) or contains_target_postal_code(
        _offer_geo_text(offer),
    )


def is_target_postal_code(value: str) -> bool:
    """Vérifie si un code postal appartient aux plages acceptées."""

    try:
        postal_code = int(value)
    except ValueError:
        return False
    return any(postal_code in postal_range for postal_range in TARGET_POSTAL_CODE_RANGES)


def contains_target_postal_code(value: str) -> bool:
    """Cherche un code postal cible dans un texte libre."""

    return any(
        is_target_postal_code(match.group(1))
        for match in POSTAL_CODE_PATTERN.finditer(value)
    )


def contains_target_city(value: str) -> bool:
    """Cherche une ville cible avec tolérance aux accents et aux tirets."""

    normalized_value = _normalize_for_search(value)
    return any(_normalize_for_search(city) in normalized_value for city in TARGET_CITIES)


def _offer_geo_text(offer: Offer) -> str:
    return " ".join(part for part in (offer.location, offer.raw_description) if part)


def _normalize_for_search(value: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
    lowered = without_accents.casefold()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()
