from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.db.client import OfferRow, SupabaseOfferRepository
from src.lib.config import OpenAISettings, get_openai_settings
from src.lib.logging import configure_logging, get_logger

DEFAULT_SCORE_MODEL = "gpt-4.1-nano"
LETTER_GENERATION_THRESHOLD = 65
DEFAULT_LIMIT = 10
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "score.md"

logger = get_logger(__name__)


class ScoreResult(BaseModel):
    """Sortie structuree attendue du LLM pour une offre."""

    score: int = Field(ge=0, le=100)
    raison: str = Field(min_length=1, max_length=500)
    points_forts: list[str] = Field(default_factory=list)
    points_faibles: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ParsedScoreResponse(Protocol):
    """Partie de reponse OpenAI utile au scoring."""

    output_parsed: ScoreResult | None
    usage: object | None


class OpenAIResponsesClient(Protocol):
    """Surface OpenAI injectable pour tester sans reseau."""

    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[ScoreResult],
        temperature: float,
        max_output_tokens: int,
    ) -> ParsedScoreResponse: ...


class ScoreParsingError(RuntimeError):
    """Erreur explicite quand OpenAI ne retourne pas un score exploitable."""


def load_score_instructions(prompt_path: Path = PROMPT_PATH) -> str:
    """Charge le prompt systeme editable hors du code."""

    return prompt_path.read_text(encoding="utf-8")


def build_offer_prompt(offer: OfferRow) -> str:
    """Construit le prompt utilisateur avec les champs critiques de l'offre."""

    return "\n".join(
        [
            "Score cette offre pour le profil Bryan.",
            "",
            f"Titre: {offer.titre}",
            f"Entreprise: {offer.entreprise or 'Non precisee'}",
            f"Lieu: {offer.lieu or 'Non precise'}",
            f"Code postal: {offer.code_postal or 'Non precise'}",
            "Permis G / frontalier: Bryan a un permis G expire, reactiver par employeur.",
            "Profil Bryan: vendeur/conseiller de vente, base a Belfort, "
            "cible Jura/Jura bernois/Neuchatel ouest.",
            "",
            "Description brute:",
            offer.description_brute or "Non precisee",
        ],
    )


class OpenAIJobScorer:
    """Service de scoring OpenAI avec validation Pydantic obligatoire."""

    def __init__(
        self,
        responses_client: OpenAIResponsesClient,
        *,
        model: str = DEFAULT_SCORE_MODEL,
        instructions: str | None = None,
    ) -> None:
        self._responses_client = responses_client
        self._model = model
        self._instructions = instructions or load_score_instructions()

    def score_offer(self, offer: OfferRow) -> ScoreResult | None:
        """Score une offre non scoree et ignore explicitement les offres deja traitees."""

        if offer.score_match is not None:
            logger.info(
                "offer_scoring_skipped",
                offer_id=offer.id,
                reason="score_match_already_set",
            )
            return None

        try:
            response = self._responses_client.parse(
                model=self._model,
                instructions=self._instructions,
                input=build_offer_prompt(offer),
                text_format=ScoreResult,
                temperature=0,
                max_output_tokens=350,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            logger.exception(
                "offer_scoring_invalid_json",
                offer_id=offer.id,
                error_type=type(exc).__name__,
            )
            raise ScoreParsingError("OpenAI a retourne un JSON de scoring invalide") from exc

        score = response.output_parsed
        if score is None:
            logger.error("offer_scoring_missing_parsed_output", offer_id=offer.id)
            raise ScoreParsingError("OpenAI n'a pas retourne de sortie Pydantic parseable")

        logger.info(
            "offer_scored",
            offer_id=offer.id,
            score=score.score,
            should_generate_letter=score.score >= LETTER_GENERATION_THRESHOLD,
            usage=_usage_to_log(response.usage),
        )
        return score


def score_new_offers(
    repository: SupabaseOfferRepository,
    scorer: OpenAIJobScorer,
    *,
    limit: int = DEFAULT_LIMIT,
) -> int:
    """Score les offres nouvelles non scorees puis persiste le resultat en DB."""

    offers = repository.get_new_unscored_offers(limit)
    scored_count = 0

    for offer in offers:
        score = scorer.score_offer(offer)
        if score is None:
            continue

        repository.update_offer_score(
            offer_id=offer.id,
            score_match=score.score,
            raison_score=score.raison,
        )
        scored_count += 1

    logger.info("offers_scoring_completed", offers_found=len(offers), offers_scored=scored_count)
    return scored_count


def build_openai_scorer(
    settings: OpenAISettings,
    *,
    model: str = DEFAULT_SCORE_MODEL,
) -> OpenAIJobScorer:
    """Instancie le scorer OpenAI depuis la configuration validee."""

    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
    return OpenAIJobScorer(cast(OpenAIResponsesClient, client.responses), model=model)


def main() -> None:
    """Point d'entree CLI du Ticket 5."""

    parser = argparse.ArgumentParser(description="Score les offres nouvelles avec OpenAI.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--model", default=DEFAULT_SCORE_MODEL)
    args = parser.parse_args()

    settings = get_openai_settings()
    configure_logging(settings.log_level)
    repository = SupabaseOfferRepository.from_settings(settings)
    scorer = build_openai_scorer(settings, model=args.model)
    score_new_offers(repository, scorer, limit=args.limit)


def _usage_to_log(usage: object | None) -> Mapping[str, object] | None:
    """Normalise l'usage tokens OpenAI quand le SDK l'expose."""

    if usage is None:
        return None
    if isinstance(usage, BaseModel):
        return cast(Mapping[str, object], usage.model_dump(mode="json"))
    if isinstance(usage, Mapping):
        return cast(Mapping[str, object], usage)
    return {"raw": str(usage)}


if __name__ == "__main__":
    main()
