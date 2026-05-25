from __future__ import annotations

import re
import unicodedata

from src.scrapers.base import Offer

BLOCKING_PERMIS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpermis\s+g\s+valide\s+obligatoire\b"),
    re.compile(r"\bpermis\s+g\s+en\s+cours\s+de\s+validite\s+exige\b"),
    re.compile(r"\bpermis\s+frontalier\s+valable\s+requis\b"),
    re.compile(r"\bpermis\s+frontalier\s+en\s+cours\s+de\s+validite\s+exige\b"),
    re.compile(r"\bpermis\s+en\s+cours\s+de\s+validite\s+exige\b"),
)


def requires_valid_permis_g(offer: Offer) -> bool:
    """Détecte uniquement les exigences bloquantes de permis G déjà valide."""

    normalized_text = _normalize_for_search(
        " ".join(part for part in (offer.title, offer.raw_description) if part),
    )
    if "permis de conduire" in normalized_text:
        return False
    return any(pattern.search(normalized_text) is not None for pattern in BLOCKING_PERMIS_PATTERNS)


def _normalize_for_search(value: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
    lowered = without_accents.casefold()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()
