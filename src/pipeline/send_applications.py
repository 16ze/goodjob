from __future__ import annotations

import argparse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol, cast

from pydantic import BaseModel

from src.ai.generate_letter import LetterResult
from src.db.client import OfferRow, ParametersRow, SupabaseOfferRepository
from src.integrations.gmail import ApplicationEmail, GmailAttachment, GmailClient, GmailSendResult
from src.lib.config import get_settings
from src.lib.logging import configure_logging, get_logger

DEFAULT_CV_FILENAME = "CV_Bryan_Hilaire.pdf"

logger = get_logger(__name__)


class CvAttachmentProvider(Protocol):
    """Source de CV injectable pour tester l'orchestration sans télécharger de fichier."""

    def get_attachment(self, parameters: ParametersRow) -> GmailAttachment: ...


class ApplicationRepository(Protocol):
    """Surface repository nécessaire à l'envoi de candidatures."""

    def get_parameters(self) -> ParametersRow: ...

    def get_offer(self, offer_id: str) -> OfferRow | None: ...

    def get_email_send_candidates(self, limit: int, *, score_threshold: int) -> list[OfferRow]: ...

    def get_auto_send_candidates(
        self,
        limit: int,
        *,
        score_min: int,
        score_max: int,
    ) -> list[OfferRow]: ...

    def update_offer_sent(
        self,
        offer_id: str,
        *,
        email_destinataire: str,
        sent_at: datetime | None = None,
    ) -> OfferRow: ...


class GmailSender(Protocol):
    """Surface Gmail nécessaire à l'orchestrateur."""

    def send_application_email(self, email: ApplicationEmail) -> GmailSendResult: ...


class UrlCvAttachmentProvider:
    """Télécharge le CV depuis l'URL stockée dans les paramètres Supabase."""

    def get_attachment(self, parameters: ParametersRow) -> GmailAttachment:
        """Retourne le PDF prêt à attacher au mail de candidature."""

        if parameters.cv_url is None:
            raise RuntimeError("Aucun CV n'est configuré dans parametres.cv_url")

        request = urllib.request.Request(parameters.cv_url, method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()

        return GmailAttachment(
            filename=parameters.cv_nom or DEFAULT_CV_FILENAME,
            content_type="application/pdf",
            content=content,
        )


class SendApplicationsReport(BaseModel):
    """Bilan d'un envoi manuel de candidatures."""

    candidates_found: int
    sent_count: int
    skipped_count: int
    failed_count: int


class SendApplicationsService:
    """Orchestre l'envoi explicite des candidatures prêtes."""

    def __init__(
        self,
        repository: ApplicationRepository,
        gmail_client: GmailSender,
        cv_provider: CvAttachmentProvider,
    ) -> None:
        self._repository = repository
        self._gmail_client = gmail_client
        self._cv_provider = cv_provider

    def send_ready_applications(self, *, limit: int | None = None) -> SendApplicationsReport:
        """Envoie au plus `max_par_jour` candidatures ayant lettre, score et destinataire."""

        parameters = self._repository.get_parameters()
        if not parameters.actif:
            logger.info("gmail_send_skipped", reason="automation_disabled")
            return SendApplicationsReport(
                candidates_found=0,
                sent_count=0,
                skipped_count=0,
                failed_count=0,
            )

        effective_limit = min(limit or parameters.max_par_jour, parameters.max_par_jour)
        attachment = self._cv_provider.get_attachment(parameters)
        offers = self._repository.get_email_send_candidates(
            effective_limit,
            score_threshold=parameters.score_seuil,
        )

        sent_count = 0
        skipped_count = 0
        failed_count = 0

        for offer in offers:
            try:
                email = build_application_email(offer, attachment)
            except ValueError as exc:
                logger.warning(
                    "gmail_send_offer_skipped",
                    offer_id=offer.id,
                    reason=str(exc),
                )
                skipped_count += 1
                continue

            try:
                result = self._gmail_client.send_application_email(email)
            except RuntimeError as exc:
                logger.warning(
                    "gmail_send_failed",
                    offer_id=offer.id,
                    reason=str(exc),
                )
                failed_count += 1
                continue

            self._repository.update_offer_sent(
                offer.id,
                email_destinataire=email.to_email,
                sent_at=datetime.now(UTC),
            )
            logger.info(
                "gmail_send_completed",
                offer_id=offer.id,
                gmail_message_id=result.message_id,
            )
            sent_count += 1

        return SendApplicationsReport(
            candidates_found=len(offers),
            sent_count=sent_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
        )

    def send_single_application(self, offer_id: str) -> GmailSendResult:
        """Envoie explicitement une seule candidature sélectionnée dans l'UI."""

        parameters = self._repository.get_parameters()
        if not parameters.actif:
            raise RuntimeError("L'envoi Gmail est désactivé dans les paramètres")
        offer = self._repository.get_offer(offer_id)
        if offer is None:
            raise RuntimeError("Offre introuvable")
        if offer.envoye_at is not None:
            raise RuntimeError("Cette offre a déjà été envoyée")
        if offer.score_match is None or offer.score_match < parameters.score_seuil:
            raise RuntimeError("Le score de cette offre est sous le seuil configuré")

        attachment = self._cv_provider.get_attachment(parameters)
        email = build_application_email(offer, attachment)
        result = self._gmail_client.send_application_email(email)
        self._repository.update_offer_sent(
            offer.id,
            email_destinataire=email.to_email,
            sent_at=datetime.now(UTC),
        )
        logger.info(
            "gmail_single_send_completed",
            offer_id=offer.id,
            gmail_message_id=result.message_id,
        )
        return result


def build_application_email(offer: OfferRow, attachment: GmailAttachment) -> ApplicationEmail:
    """Transforme une offre prête en email Gmail typé."""

    if offer.email_destinataire is None:
        raise ValueError("Aucun destinataire email n'est renseigné")
    if offer.lettre_generee is None:
        raise ValueError("Aucune lettre générée n'est disponible")

    letter = LetterResult.model_validate_json(offer.lettre_generee)
    return ApplicationEmail(
        to_email=offer.email_destinataire,
        subject=letter.objet,
        body=letter.lettre,
        attachment=attachment,
    )


def auto_send_low_priority_offers(
    repository: ApplicationRepository,
    service: SendApplicationsService,
    *,
    score_min: int = 50,
    score_max: int = 69,
    limit: int = 10,
) -> int:
    """Envoie automatiquement les offres basse priorité éligibles pendant le cron.

    Retourne le nombre d'offres envoyées avec succès.
    """

    parameters = repository.get_parameters()
    if not parameters.actif:
        logger.info("auto_send_low_priority_skipped", reason="automation_disabled")
        return 0

    candidates = repository.get_auto_send_candidates(
        limit,
        score_min=score_min,
        score_max=score_max,
    )

    sent_count = 0
    for offer in candidates:
        try:
            service.send_single_application(offer.id)
            sent_count += 1
        except Exception as exc:
            logger.warning(
                "auto_send_low_priority_offer_failed",
                offer_id=offer.id,
                error=str(exc),
            )

    logger.info(
        "auto_send_low_priority_completed",
        candidates_found=len(candidates),
        sent_count=sent_count,
    )
    return sent_count


def main() -> None:
    """Point d'entrée manuel pour envoyer les candidatures prêtes."""

    parser = argparse.ArgumentParser(description="Envoie manuellement les candidatures prêtes.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    repository = SupabaseOfferRepository.from_settings(settings)
    gmail_client = GmailClient.from_settings(settings)
    service = SendApplicationsService(
        repository,
        gmail_client,
        UrlCvAttachmentProvider(),
    )
    report = service.send_ready_applications(limit=args.limit)
    logger.info(
        "gmail_send_report",
        **cast(Mapping[str, object], report.model_dump(mode="json")),
    )


if __name__ == "__main__":
    main()
