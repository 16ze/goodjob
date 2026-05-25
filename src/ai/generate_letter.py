from __future__ import annotations

import argparse
import json
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.ai.score import LETTER_GENERATION_THRESHOLD
from src.db.client import OfferRow, SupabaseOfferRepository
from src.lib.config import OpenAISettings, get_openai_settings
from src.lib.logging import configure_logging, get_logger

DEFAULT_LETTER_MODEL = "gpt-4.1-nano"
DEFAULT_LIMIT = 10
REQUIRED_PERMIS_G_SENTENCE = (
    "Mon permis frontalier G est à réactiver à l’embauche, démarche habituelle effectuée par "  # noqa: RUF001
    "l’employeur auprès du canton."  # noqa: RUF001
)
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "letter.md"
PROFILE_PATH = Path(__file__).resolve().parents[2] / "data" / "profil.toml"

logger = get_logger(__name__)


class BryanProfile(BaseModel):
    """Profil Bryan chargé depuis un fichier éditable sans dépendance YAML."""

    identite: Mapping[str, object]
    permis: Mapping[str, object]
    mobilite: Mapping[str, object]
    profil: Mapping[str, object]

    model_config = ConfigDict(extra="forbid", frozen=True)


class LetterResult(BaseModel):
    """Sortie structurée attendue du LLM pour une lettre."""

    objet: str = Field(min_length=1, max_length=180)
    lettre: str = Field(min_length=1)
    notes_personnalisation: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("lettre")
    @classmethod
    def requires_permis_g_sentence(cls, value: str) -> str:
        """Verrouille la formulation métier sensible sur le permis G."""

        if REQUIRED_PERMIS_G_SENTENCE not in value:
            raise ValueError("La lettre doit contenir la formulation obligatoire du permis G")
        return value


class ParsedLetterResponse(Protocol):
    """Partie de réponse OpenAI utile à la génération de lettre."""

    output_parsed: LetterResult | None
    usage: object | None


class OpenAIResponsesClient(Protocol):
    """Surface OpenAI injectable pour tester sans réseau."""

    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[LetterResult],
        temperature: float,
        max_output_tokens: int,
    ) -> ParsedLetterResponse: ...


class LetterGenerationError(RuntimeError):
    """Erreur explicite quand OpenAI ne retourne pas une lettre exploitable."""


def load_letter_instructions(prompt_path: Path = PROMPT_PATH) -> str:
    """Charge le prompt éditable hors du code."""

    return prompt_path.read_text(encoding="utf-8")


def load_bryan_profile(profile_path: Path = PROFILE_PATH) -> BryanProfile:
    """Charge le profil Bryan en TOML pour éviter une dépendance YAML."""

    profile_data = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    return BryanProfile.model_validate(profile_data)


def build_letter_prompt(offer: OfferRow, profile: BryanProfile) -> str:
    """Construit le prompt utilisateur avec l'offre, le score et le profil Bryan."""

    profile_json = json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return "\n".join(
        [
            "Génère une lettre de candidature personnalisée pour cette offre.",
            "",
            "Profil Bryan:",
            profile_json,
            "",
            "Offre:",
            f"Titre: {offer.titre}",
            f"Entreprise: {offer.entreprise or 'Non précisée'}",
            f"Lieu: {offer.lieu or 'Non précisé'}",
            f"Score match: {offer.score_match if offer.score_match is not None else 'Non scoré'}",
            f"Raison du score: {offer.raison_score or 'Non précisée'}",
            "",
            "Description brute:",
            offer.description_brute or "Non précisée",
        ],
    )


class OpenAIJobLetterGenerator:
    """Service de génération OpenAI avec garde-fous métier et validation Pydantic."""

    def __init__(
        self,
        responses_client: OpenAIResponsesClient,
        *,
        model: str = DEFAULT_LETTER_MODEL,
        instructions: str | None = None,
        profile: BryanProfile | None = None,
    ) -> None:
        self._responses_client = responses_client
        self._model = model
        self._instructions = instructions or load_letter_instructions()
        self._profile = profile or load_bryan_profile()

    def generate_offer_letter(self, offer: OfferRow, *, force: bool = False) -> LetterResult | None:
        """Génère une lettre uniquement pour les offres éligibles."""

        if offer.score_match is None or offer.score_match < LETTER_GENERATION_THRESHOLD:
            logger.info(
                "letter_generation_skipped",
                offer_id=offer.id,
                reason="score_below_threshold",
            )
            return None
        if offer.lettre_generee is not None and not force:
            logger.info(
                "letter_generation_skipped",
                offer_id=offer.id,
                reason="letter_already_exists",
            )
            return None

        try:
            response = self._responses_client.parse(
                model=self._model,
                instructions=self._instructions,
                input=build_letter_prompt(offer, self._profile),
                text_format=LetterResult,
                temperature=0.2,
                max_output_tokens=900,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            logger.exception(
                "letter_generation_invalid_json",
                offer_id=offer.id,
                error_type=type(exc).__name__,
            )
            raise LetterGenerationError("OpenAI a retourné un JSON de lettre invalide") from exc

        letter = response.output_parsed
        if letter is None:
            logger.error("letter_generation_missing_parsed_output", offer_id=offer.id)
            raise LetterGenerationError("OpenAI n'a pas retourné de sortie Pydantic parseable")

        logger.info(
            "letter_generated",
            offer_id=offer.id,
            usage=_usage_to_log(response.usage),
        )
        return letter


def generate_letters_for_matching_offers(
    repository: SupabaseOfferRepository,
    generator: OpenAIJobLetterGenerator,
    *,
    limit: int = DEFAULT_LIMIT,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Génère puis persiste les lettres des offres matchantes, sans aucun envoi."""

    offers = repository.get_letter_candidates(limit, force=force)
    generated_count = 0
    failed_count = 0

    for offer in offers:
        try:
            letter = generator.generate_offer_letter(offer, force=force)
        except LetterGenerationError as exc:
            logger.warning(
                "letter_generation_failed",
                offer_id=offer.id,
                reason=str(exc),
            )
            failed_count += 1
            continue

        if letter is None:
            continue

        serialized_letter = letter.model_dump_json(indent=2)
        if not dry_run:
            repository.update_offer_letter(offer.id, serialized_letter)
        generated_count += 1

    logger.info(
        "letters_generation_completed",
        offers_found=len(offers),
        letters_generated=generated_count,
        letters_failed=failed_count,
        dry_run=dry_run,
        force=force,
    )
    return generated_count


def build_openai_letter_generator(
    settings: OpenAISettings,
    *,
    model: str = DEFAULT_LETTER_MODEL,
) -> OpenAIJobLetterGenerator:
    """Instancie le générateur OpenAI depuis la configuration validée."""

    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
    return OpenAIJobLetterGenerator(cast(OpenAIResponsesClient, client.responses), model=model)


def main() -> None:
    """Point d'entrée CLI du Ticket 6."""

    parser = argparse.ArgumentParser(description="Génère les lettres pour les offres matchantes.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--model", default=DEFAULT_LETTER_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    settings = get_openai_settings()
    configure_logging(settings.log_level)
    repository = SupabaseOfferRepository.from_settings(settings)
    generator = build_openai_letter_generator(settings, model=args.model)
    generate_letters_for_matching_offers(
        repository,
        generator,
        limit=args.limit,
        dry_run=args.dry_run,
        force=args.force,
    )


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
