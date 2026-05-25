from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

from src.ai.generate_letter import REQUIRED_PERMIS_G_SENTENCE
from src.db.client import OfferRow, ParametersRow, hash_url
from src.integrations.gmail import ApplicationEmail, GmailAttachment, GmailSendResult
from src.pipeline.send_applications import SendApplicationsService, build_application_email


class FakeRepository:
    def __init__(self, offers: list[OfferRow], parameters: ParametersRow) -> None:
        self.offers = offers
        self.parameters = parameters
        self.candidate_calls: list[Mapping[str, object]] = []
        self.sent_updates: list[Mapping[str, object]] = []

    def get_parameters(self) -> ParametersRow:
        return self.parameters

    def get_offer(self, offer_id: str) -> OfferRow | None:
        return next((offer for offer in self.offers if offer.id == offer_id), None)

    def get_email_send_candidates(self, limit: int, *, score_threshold: int) -> list[OfferRow]:
        self.candidate_calls.append({"limit": limit, "score_threshold": score_threshold})
        return self.offers[:limit]

    def update_offer_sent(
        self,
        offer_id: str,
        *,
        email_destinataire: str,
        sent_at: datetime | None = None,
    ) -> OfferRow:
        self.sent_updates.append(
            {
                "offer_id": offer_id,
                "email_destinataire": email_destinataire,
                "sent_at": sent_at,
            },
        )
        return self.offers[0].model_copy(update={"statut": "envoye"})


class FakeGmailClient:
    def __init__(self) -> None:
        self.sent_emails: list[ApplicationEmail] = []

    def send_application_email(self, email: ApplicationEmail) -> GmailSendResult:
        self.sent_emails.append(email)
        return GmailSendResult(message_id=f"message-{len(self.sent_emails)}")


class FakeCvProvider:
    def __init__(self) -> None:
        self.parameters_seen: ParametersRow | None = None

    def get_attachment(self, parameters: ParametersRow) -> GmailAttachment:
        self.parameters_seen = parameters
        return GmailAttachment(
            filename=parameters.cv_nom or "CV_Bryan_Hilaire.pdf",
            content_type="application/pdf",
            content=b"%PDF-test",
        )


def make_offer(*, offer_id: str = "offer-1") -> OfferRow:
    url = f"https://example.com/jobs/{offer_id}"
    letter_json = json.dumps(
        {
            "objet": "Candidature au poste de Conseiller de vente",
            "lettre": (
                "Madame, Monsieur,\n\n"
                "Je vous adresse ma candidature.\n\n"
                f"{REQUIRED_PERMIS_G_SENTENCE}"
            ),
            "notes_personnalisation": [],
        },
    )
    return OfferRow(
        id=offer_id,
        source="jobup",
        url=url,
        url_hash=hash_url(url),
        titre="Conseiller de vente",
        statut="draft_pret",
        score_match=72,
        lettre_generee=letter_json,
        email_destinataire="rh@example.com",
    )


def test_build_application_email_uses_generated_letter_and_cv() -> None:
    attachment = GmailAttachment(
        filename="CV.pdf",
        content_type="application/pdf",
        content=b"%PDF-test",
    )

    email = build_application_email(make_offer(), attachment)

    assert email.to_email == "rh@example.com"
    assert email.subject == "Candidature au poste de Conseiller de vente"
    assert REQUIRED_PERMIS_G_SENTENCE in email.body
    assert email.attachment.filename == "CV.pdf"


def test_send_service_respects_daily_limit_and_marks_sent() -> None:
    repository = FakeRepository(
        [make_offer(offer_id="offer-1"), make_offer(offer_id="offer-2")],
        ParametersRow(actif=True, max_par_jour=1, score_seuil=70, cv_nom="CV.pdf"),
    )
    gmail_client = FakeGmailClient()
    cv_provider = FakeCvProvider()
    service = SendApplicationsService(repository, gmail_client, cv_provider)

    report = service.send_ready_applications(limit=10)

    assert report.sent_count == 1
    assert report.failed_count == 0
    assert repository.candidate_calls == [{"limit": 1, "score_threshold": 70}]
    assert len(gmail_client.sent_emails) == 1
    assert repository.sent_updates[0]["offer_id"] == "offer-1"
    assert cv_provider.parameters_seen is repository.parameters


def test_send_service_stops_when_disabled() -> None:
    repository = FakeRepository(
        [make_offer()],
        ParametersRow(actif=False, max_par_jour=5, score_seuil=65),
    )
    gmail_client = FakeGmailClient()
    service = SendApplicationsService(repository, gmail_client, FakeCvProvider())

    report = service.send_ready_applications()

    assert report.sent_count == 0
    assert repository.candidate_calls == []
    assert gmail_client.sent_emails == []


def test_send_single_application_requires_active_settings() -> None:
    repository = FakeRepository(
        [make_offer()],
        ParametersRow(actif=False, max_par_jour=5, score_seuil=65, cv_nom="CV.pdf"),
    )
    gmail_client = FakeGmailClient()
    service = SendApplicationsService(repository, gmail_client, FakeCvProvider())

    try:
        service.send_single_application("offer-1")
    except RuntimeError as exc:
        assert "désactivé" in str(exc)
    else:
        raise AssertionError("send_single_application aurait dû refuser l'envoi")

    assert gmail_client.sent_emails == []
