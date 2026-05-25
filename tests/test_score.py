from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import pytest
from pydantic import ValidationError
from src.ai.score import (
    OpenAIJobScorer,
    ScoreParsingError,
    ScoreResult,
    build_offer_prompt,
)
from src.db.client import OfferRow, hash_url


class FakeParsedScoreResponse:
    output_parsed: ScoreResult | None
    usage: object | None

    def __init__(
        self,
        output_parsed: ScoreResult | None,
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
        text_format: type[ScoreResult],
        temperature: float,
        max_output_tokens: int,
    ) -> FakeParsedScoreResponse:
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
        return FakeParsedScoreResponse(
            text_format.model_validate(payload),
            {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        )


def make_offer(*, score_match: int | None = None) -> OfferRow:
    url = "https://example.com/jobs/vente-1"
    return OfferRow(
        id="5d9d9fd5-9200-4b0a-89de-4893450f7f72",
        source="jobup",
        url=url,
        url_hash=hash_url(url),
        titre="Conseiller de vente",
        entreprise="Commerce SA",
        lieu="Delemont",
        code_postal="2800",
        description_brute="Vente en magasin, contact client, francais requis, taux 80%.",
        score_match=score_match,
        statut="nouveau",
    )


def test_parse_valid_json_accepts_score() -> None:
    client = FakeResponsesClient(
        """
        {
          "score": 78,
          "raison": "Bonne proximite et poste vente coherent.",
          "points_forts": ["Delemont", "vente magasin"],
          "points_faibles": ["taux a confirmer"]
        }
        """,
    )
    scorer = OpenAIJobScorer(client, instructions="Instructions test")

    result = scorer.score_offer(make_offer())

    assert result is not None
    assert result.score == 78
    assert len(client.calls) == 1


def test_score_outside_0_100_is_rejected() -> None:
    client = FakeResponsesClient(
        """
        {
          "score": 120,
          "raison": "Score invalide.",
          "points_forts": [],
          "points_faibles": []
        }
        """,
    )
    scorer = OpenAIJobScorer(client, instructions="Instructions test")

    with pytest.raises(ScoreParsingError) as error:
        scorer.score_offer(make_offer())

    assert isinstance(error.value.__cause__, ValidationError)


def test_already_scored_offer_does_not_call_openai() -> None:
    client = FakeResponsesClient(
        """
        {
          "score": 70,
          "raison": "Ne devrait pas etre appele.",
          "points_forts": [],
          "points_faibles": []
        }
        """,
    )
    scorer = OpenAIJobScorer(client, instructions="Instructions test")

    result = scorer.score_offer(make_offer(score_match=66))

    assert result is None
    assert client.calls == []


def test_prompt_contains_critical_fields() -> None:
    prompt = build_offer_prompt(make_offer())

    assert "Lieu: Delemont" in prompt
    assert "Permis G" in prompt
    assert "Vente en magasin" in prompt
    assert "Profil Bryan" in prompt
    assert "Code postal: 2800" in prompt
