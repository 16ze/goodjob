from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from src.lib.config import Settings

GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailAttachment(BaseModel):
    """Pièce jointe binaire à insérer dans un email MIME."""

    filename: str = Field(min_length=1)
    content_type: str = Field(default="application/pdf", min_length=1)
    content: bytes = Field(min_length=1)

    model_config = ConfigDict(frozen=True)


class ApplicationEmail(BaseModel):
    """Email de candidature prêt à envoyer via Gmail."""

    to_email: str = Field(min_length=3)
    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1)
    attachment: GmailAttachment

    model_config = ConfigDict(frozen=True)

    @field_validator("to_email")
    @classmethod
    def validates_recipient_shape(cls, value: str) -> str:
        """Évite les appels Gmail avec un destinataire manifestement invalide."""

        normalized = value.strip()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("Le destinataire email est invalide")
        return normalized


class GmailSendResult(BaseModel):
    """Réponse minimale retournée par Gmail après envoi."""

    message_id: str
    thread_id: str | None = None

    model_config = ConfigDict(frozen=True)


class GmailOAuthSettings(BaseModel):
    """Secrets OAuth2 nécessaires au renouvellement du token Gmail."""

    client_id: str
    client_secret: SecretStr
    refresh_token: SecretStr

    model_config = ConfigDict(frozen=True)


class HttpTransport(Protocol):
    """Transport HTTP injectable pour tester Gmail sans réseau."""

    def post_form(
        self,
        url: str,
        payload: Mapping[str, str],
        *,
        headers: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str],
    ) -> Mapping[str, object]: ...


class UrlLibHttpTransport:
    """Transport standard-library pour éviter une dépendance HTTP supplémentaire."""

    def post_form(
        self,
        url: str,
        payload: Mapping[str, str],
        *,
        headers: Mapping[str, str],
    ) -> Mapping[str, object]:
        encoded_payload = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded_payload,
            headers=dict(headers),
            method="POST",
        )
        return _read_json_response(request)

    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str],
    ) -> Mapping[str, object]:
        encoded_payload = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded_payload,
            headers={**dict(headers), "Content-Type": "application/json"},
            method="POST",
        )
        return _read_json_response(request)


class GmailClient:
    """Client Gmail REST limité à l'envoi manuel de candidatures."""

    def __init__(
        self,
        oauth_settings: GmailOAuthSettings,
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        self._oauth_settings = oauth_settings
        self._transport = transport or UrlLibHttpTransport()

    @classmethod
    def from_settings(cls, settings: Settings) -> GmailClient:
        """Construit le client depuis la configuration globale validée."""

        oauth_settings = GmailOAuthSettings(
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            refresh_token=settings.gmail_refresh_token,
        )
        return cls(oauth_settings)

    def send_application_email(self, email: ApplicationEmail) -> GmailSendResult:
        """Renouvelle le token OAuth puis envoie l'email MIME via Gmail."""

        access_token = self._refresh_access_token()
        raw_message = encode_message(build_application_message(email))
        response = self._transport.post_json(
            GMAIL_SEND_URL,
            {"raw": raw_message},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        message_id = response.get("id")
        if not isinstance(message_id, str) or message_id == "":
            raise RuntimeError("Gmail n'a pas retourné d'identifiant de message")
        thread_id = response.get("threadId")
        return GmailSendResult(
            message_id=message_id,
            thread_id=thread_id if isinstance(thread_id, str) else None,
        )

    def _refresh_access_token(self) -> str:
        """Échange le refresh token OAuth2 contre un access token court."""

        response = self._transport.post_form(
            GMAIL_TOKEN_URL,
            {
                "client_id": self._oauth_settings.client_id,
                "client_secret": self._oauth_settings.client_secret.get_secret_value(),
                "refresh_token": self._oauth_settings.refresh_token.get_secret_value(),
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        access_token = response.get("access_token")
        if not isinstance(access_token, str) or access_token == "":
            raise RuntimeError("OAuth Gmail n'a pas retourné d'access token")
        return access_token


def build_application_message(email: ApplicationEmail) -> EmailMessage:
    """Construit le MIME multipart avec la lettre en texte brut et le CV PDF."""

    message = EmailMessage()
    message["To"] = email.to_email
    message["Subject"] = email.subject
    message.set_content(email.body)

    main_type, subtype = _split_content_type(email.attachment.content_type)
    message.add_attachment(
        email.attachment.content,
        maintype=main_type,
        subtype=subtype,
        filename=email.attachment.filename,
    )
    return message


def encode_message(message: EmailMessage) -> str:
    """Encode un message MIME au format base64url attendu par Gmail."""

    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _split_content_type(content_type: str) -> tuple[str, str]:
    parts = content_type.split("/", maxsplit=1)
    if len(parts) != 2 or parts[0] == "" or parts[1] == "":
        raise ValueError("Le type MIME de la pièce jointe est invalide")
    return parts[0], parts[1]


def _read_json_response(request: urllib.request.Request) -> Mapping[str, object]:
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping):
        raise RuntimeError("Réponse HTTP JSON invalide")
    return cast(Mapping[str, object], parsed)
