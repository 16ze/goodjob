from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError
from src.ai.generate_letter import (
    REQUIRED_PERMIS_G_SENTENCE,
    BryanProfile,
    LetterGenerationError,
    LetterResult,
    OpenAIJobLetterGenerator,
    build_letter_prompt,
    generate_letters_for_matching_offers,
    load_bryan_profile,
    load_letter_instructions,
)
from src.db.client import OfferRow, SupabaseOfferRepository, hash_url


class FakeParsedLetterResponse:
    output_parsed: LetterResult | None
    usage: object | None

    def __init__(
        self,
        output_parsed: LetterResult | None,
        usage: Mapping[str, object] | None,
    ) -> None:
        self.output_parsed = output_parsed
        self.usage = usage


class FakeResponsesClient:
    def __init__(self, response_json: str) -> None:
        self.response_json = response_json
        self.calls: list[Mapping[str, object]] = []

    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[LetterResult],
        temperature: float,
        max_output_tokens: int,
    ) -> FakeParsedLetterResponse:
        self.calls.append(
            {
                "model": model,
                "instructions": instructions,
                "input": input,
                "text_format": text_format,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            },
        )
        payload = cast(dict[str, object], json.loads(self.response_json))
        return FakeParsedLetterResponse(
            text_format.model_validate(payload),
            {"input_tokens": 240, "output_tokens": 320, "total_tokens": 560},
        )


class FakeSequentialResponsesClient:
    """Renvoie une réponse différente par appel pour tester la résilience boucle."""

    def __init__(self, response_jsons: list[str]) -> None:
        self.response_jsons = list(response_jsons)
        self.call_index = 0

    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[LetterResult],
        temperature: float,
        max_output_tokens: int,
    ) -> FakeParsedLetterResponse:
        payload = cast(dict[str, object], json.loads(self.response_jsons[self.call_index]))
        self.call_index += 1
        return FakeParsedLetterResponse(
            text_format.model_validate(payload),
            {"input_tokens": 240, "output_tokens": 320, "total_tokens": 560},
        )


class FakeLetterRepository:
    def __init__(self, offers: list[OfferRow]) -> None:
        self.offers = offers
        self.candidate_calls: list[Mapping[str, object]] = []
        self.updated_letters: list[tuple[str, str]] = []

    def get_letter_candidates(self, limit: int, *, force: bool = False) -> list[OfferRow]:
        self.candidate_calls.append({"limit": limit, "force": force})
        return self.offers[:limit]

    def update_offer_letter(self, offer_id: str, lettre_generee: str) -> OfferRow:
        self.updated_letters.append((offer_id, lettre_generee))
        return self.offers[0].model_copy(update={"lettre_generee": lettre_generee})


def make_profile() -> BryanProfile:
    return BryanProfile.model_validate(
        {
            "identite": {"nom": "Bryan Hilaire", "localisation": "Belfort"},
            "permis": {"frontalier_g": REQUIRED_PERMIS_G_SENTENCE},
            "mobilite": {"base": "Belfort", "zones_cibles": ["Jura"]},
            "profil": {
                "resume": "Profil vente et relation client.",
                "competences": ["Vente", "Relation client"],
                "experiences": ["Expérience réelle en vente."],
            },
        },
    )


def make_offer(
    *,
    score_match: int | None = 72,
    lettre_generee: str | None = None,
) -> OfferRow:
    url = "https://example.com/jobs/conseiller-vente"
    return OfferRow(
        id="5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        source="jobup",
        url=url,
        url_hash=hash_url(url),
        titre="Conseiller de vente",
        entreprise="Commerce SA",
        lieu="Delémont",
        description_brute="Conseil client, vente en magasin, suivi des commandes.",
        score_match=score_match,
        raison_score="Bonne proximité et poste cohérent avec le profil vente.",
        lettre_generee=lettre_generee,
        statut="nouveau",
    )


def valid_letter_json() -> str:
    return json.dumps(
        {
            "objet": "Candidature au poste de Conseiller de vente",
            "lettre": (
                "Madame, Monsieur,\n\n"
                "Votre poste de conseiller de vente à Delémont correspond à mon parcours en "
                "vente et relation client.\n\n"
                f"{REQUIRED_PERMIS_G_SENTENCE}\n\n"
                "Je serais heureux d'échanger avec vous."
            ),
            "notes_personnalisation": ["Delémont", "Vente en magasin"],
        },
    )


def test_loads_prompt_from_markdown(tmp_path: Path) -> None:
    prompt_path = tmp_path / "letter.md"
    prompt_path.write_text("Instructions lettre test", encoding="utf-8")

    assert load_letter_instructions(prompt_path) == "Instructions lettre test"


def test_loads_bryan_profile_from_toml_without_yaml_dependency(tmp_path: Path) -> None:
    profile_path = tmp_path / "profil.toml"
    profile_path.write_text(
        """
        [identite]
        nom = "Bryan Hilaire"

        [permis]
        frontalier_g = "Permis G à réactiver"

        [mobilite]
        base = "Belfort"

        [profil]
        resume = "Vente"
        """,
        encoding="utf-8",
    )

    profile = load_bryan_profile(profile_path)

    assert profile.identite["nom"] == "Bryan Hilaire"
    assert profile.mobilite["base"] == "Belfort"


def test_refuses_offer_below_threshold_without_openai_call() -> None:
    client = FakeResponsesClient(valid_letter_json())
    generator = OpenAIJobLetterGenerator(
        client,
        instructions="Instructions test",
        profile=make_profile(),
    )

    result = generator.generate_offer_letter(make_offer(score_match=64))

    assert result is None
    assert client.calls == []


def test_ignores_offer_with_existing_letter_without_force() -> None:
    client = FakeResponsesClient(valid_letter_json())
    generator = OpenAIJobLetterGenerator(
        client,
        instructions="Instructions test",
        profile=make_profile(),
    )

    result = generator.generate_offer_letter(make_offer(lettre_generee="déjà générée"))

    assert result is None
    assert client.calls == []


def test_validates_openai_json_with_pydantic() -> None:
    client = FakeResponsesClient(valid_letter_json())
    generator = OpenAIJobLetterGenerator(
        client,
        instructions="Instructions test",
        profile=make_profile(),
    )

    result = generator.generate_offer_letter(make_offer())

    assert result is not None
    assert result.objet == "Candidature au poste de Conseiller de vente"
    assert REQUIRED_PERMIS_G_SENTENCE in result.lettre
    assert len(client.calls) == 1


def test_rejects_letter_without_required_permis_g_sentence() -> None:
    client = FakeResponsesClient(
        json.dumps(
            {
                "objet": "Candidature au poste de Conseiller de vente",
                "lettre": "Madame, Monsieur, mon permis G est valide.",
                "notes_personnalisation": [],
            },
        ),
    )
    generator = OpenAIJobLetterGenerator(
        client,
        instructions="Instructions test",
        profile=make_profile(),
    )

    with pytest.raises(LetterGenerationError) as error:
        generator.generate_offer_letter(make_offer())

    assert isinstance(error.value.__cause__, ValidationError)


def test_build_letter_prompt_contains_offer_and_profile() -> None:
    prompt = build_letter_prompt(make_offer(), make_profile())

    assert "Conseiller de vente" in prompt
    assert "Commerce SA" in prompt
    assert "Bryan Hilaire" in prompt
    assert REQUIRED_PERMIS_G_SENTENCE in prompt


def test_dry_run_generates_without_db_update() -> None:
    repository = FakeLetterRepository([make_offer()])
    generator = OpenAIJobLetterGenerator(
        FakeResponsesClient(valid_letter_json()),
        instructions="Instructions test",
        profile=make_profile(),
    )

    count = generate_letters_for_matching_offers(
        cast(SupabaseOfferRepository, repository),
        generator,
        limit=3,
        dry_run=True,
    )

    assert count == 1
    assert repository.candidate_calls == [{"limit": 3, "force": False}]
    assert repository.updated_letters == []


def test_persists_generated_letter_without_gmail_call() -> None:
    repository = FakeLetterRepository([make_offer()])
    generator = OpenAIJobLetterGenerator(
        FakeResponsesClient(valid_letter_json()),
        instructions="Instructions test",
        profile=make_profile(),
    )

    count = generate_letters_for_matching_offers(
        cast(SupabaseOfferRepository, repository),
        generator,
        limit=3,
    )

    assert count == 1
    assert len(repository.updated_letters) == 1
    persisted = json.loads(repository.updated_letters[0][1])
    assert persisted["objet"] == "Candidature au poste de Conseiller de vente"
    assert not hasattr(repository, "send_email")


def invalid_letter_json_missing_permis_g() -> str:
    """Lettre OpenAI sans la phrase obligatoire — force le rejet du validator."""

    return json.dumps(
        {
            "objet": "Candidature au poste de Conseiller de vente",
            "lettre": (
                "Madame, Monsieur, je vous adresse ma candidature. "
                "Salutations distinguées."
            ),
            "notes_personnalisation": [],
        },
    )


def test_loop_skips_failing_letter_and_continues_with_next_offer() -> None:
    offer_invalid = make_offer().model_copy(update={"id": "offer-invalid"})
    offer_valid = make_offer().model_copy(update={"id": "offer-valid"})
    repository = FakeLetterRepository([offer_invalid, offer_valid])
    generator = OpenAIJobLetterGenerator(
        FakeSequentialResponsesClient(
            [invalid_letter_json_missing_permis_g(), valid_letter_json()],
        ),
        instructions="Instructions test",
        profile=make_profile(),
    )

    count = generate_letters_for_matching_offers(
        cast(SupabaseOfferRepository, repository),
        generator,
        limit=3,
    )

    assert count == 1
    assert len(repository.updated_letters) == 1
    assert repository.updated_letters[0][0] == "offer-valid"
