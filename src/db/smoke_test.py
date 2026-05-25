from __future__ import annotations

from src.db.client import OfferUpsert, SupabaseOfferRepository, hash_url
from src.lib.config import get_database_settings
from src.lib.logging import configure_logging, get_logger


def main() -> None:
    """Insère une offre de test puis la relit par `url_hash`."""

    settings = get_database_settings()
    configure_logging(settings.log_level)
    logger = get_logger("db.smoke_test")

    repository = SupabaseOfferRepository.from_settings(settings)
    offer = OfferUpsert(
        source="smoke_test",
        url="https://example.com/goodjob/smoke-test",
        titre="Vendeur smoke test",
        entreprise="Goodjob",
        lieu="Delémont",
        code_postal="2800",
        description_brute="Offre technique créée par le smoke test local.",
    )

    upserted = repository.upsert_offer(offer)
    expected_hash = hash_url(offer.url)
    reloaded = repository.get_by_url_hash(expected_hash)

    if reloaded is None:
        raise RuntimeError("Smoke test Supabase KO: ligne introuvable après upsert")

    logger.info(
        "supabase_smoke_test_ok",
        id=upserted.id,
        url_hash=reloaded.url_hash,
        statut=reloaded.statut,
    )


if __name__ == "__main__":
    main()
