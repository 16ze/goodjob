from __future__ import annotations

import base64
from collections.abc import Mapping

from pydantic import SecretStr
from src.integrations.gmail import (
    ApplicationEmail,
    GmailAttachment,
    GmailClient,
    GmailOAuthSettings,
    build_application_message,
    encode_message,
)


class FakeTransport:
    def __init__(self) -> None:
        self.form_calls: list[tuple[str, Mapping[str, str]]] = []
        self.json_calls: list[tuple[str, Mapping[str, object], Mapping[str, str]]] = []

    def post_form(
        self,
        url: str,
        payload: Mapping[str, str],
        *,
        headers: Mapping[str, str],
    ) -> Mapping[str, object]:
        self.form_calls.append((url, payload))
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"
        return {"access_token": "access-token-test"}

    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str],
    ) -> Mapping[str, object]:
        self.json_calls.append((url, payload, headers))
        return {"id": "gmail-message-id", "threadId": "gmail-thread-id"}


def make_email() -> ApplicationEmail:
    return ApplicationEmail(
        to_email="rh@example.com",
        subject="Candidature Conseiller de vente",
        body="Madame, Monsieur,\n\nJe vous adresse ma candidature.",
        attachment=GmailAttachment(
            filename="CV_Bryan_Hilaire.pdf",
            content_type="application/pdf",
            content=b"%PDF-test",
        ),
    )


def test_builds_multipart_message_with_pdf_attachment() -> None:
    message = build_application_message(make_email())

    assert message["To"] == "rh@example.com"
    assert message["Subject"] == "Candidature Conseiller de vente"
    assert message.is_multipart()


def test_encodes_message_as_base64url() -> None:
    encoded = encode_message(build_application_message(make_email()))

    decoded = base64.urlsafe_b64decode(encoded.encode("ascii"))
    assert b"rh@example.com" in decoded
    assert b"CV_Bryan_Hilaire.pdf" in decoded


def test_gmail_client_refreshes_token_then_sends_message() -> None:
    transport = FakeTransport()
    client = GmailClient(
        GmailOAuthSettings(
            client_id="client-id",
            client_secret=SecretStr("client-secret"),
            refresh_token=SecretStr("refresh-token"),
        ),
        transport=transport,
    )

    result = client.send_application_email(make_email())

    assert result.message_id == "gmail-message-id"
    assert transport.form_calls[0][1]["grant_type"] == "refresh_token"
    assert transport.json_calls[0][2]["Authorization"] == "Bearer access-token-test"
    assert isinstance(transport.json_calls[0][1]["raw"], str)
