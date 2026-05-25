from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.lib.logging import get_logger

PAGE_TIMEOUT_MS = 30_000
MAX_RETRIES = 2
MAX_CONCURRENCY_PER_DOMAIN = 3

DESKTOP_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:131.0) "
    "Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) "
    "Gecko/20100101 Firefox/131.0",
)

T = TypeVar("T")


class SearchQuery(BaseModel):
    """Requête de recherche normalisée entre scrapers."""

    term: str = "vendeur"
    locations: tuple[str, ...] = ("jura", "neuchâtel", "moutier")
    max_results: int = Field(default=50, ge=1, le=100)

    model_config = ConfigDict(frozen=True)


class OfferStub(BaseModel):
    """Offre partielle issue d'une page de résultats."""

    source: str
    url: HttpUrl
    title: str
    company: str | None = None
    location: str | None = None

    model_config = ConfigDict(frozen=True)

    @property
    def url_hash(self) -> str:
        """Clé stable pour dédupliquer avant insertion DB."""

        return sha256(str(self.url).encode("utf-8")).hexdigest()


class Offer(BaseModel):
    """Offre complète après visite de la page détail."""

    source: str
    url: HttpUrl
    url_hash: str
    title: str
    company: str | None = None
    location: str | None = None
    postal_code: str | None = None
    publication_date: date | None = None
    raw_description: str

    model_config = ConfigDict(frozen=True)


@dataclass(frozen=True)
class RetryPolicy:
    """Paramètres de retry partagés par tous les scrapers."""

    attempts: int = MAX_RETRIES + 1
    base_delay_seconds: float = 1.0


class BaseScraper(ABC):
    """Contrat commun des scrapers, avec retry et rate limiting par domaine."""

    source: str

    def __init__(
        self,
        retry_policy: RetryPolicy | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        self._retry_policy = retry_policy or RetryPolicy()
        self._random = random_source or random.Random()
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._logger = get_logger(self.__class__.__name__)

    @abstractmethod
    async def fetch_listings(self, query: SearchQuery) -> list[OfferStub]:
        """Retourne les offres partielles depuis la recherche."""

    @abstractmethod
    async def parse_listing(self, stub: OfferStub) -> Offer:
        """Retourne l'offre complète depuis une offre partielle."""

    async def extract_full_offer(self, stub: OfferStub) -> Offer:
        """Alias explicite pour les pipelines qui préfèrent ce vocabulaire."""

        return await self.parse_listing(stub)

    async def _with_retry(self, label: str, operation: Callable[[], Awaitable[T]]) -> T:
        last_error: Exception | None = None
        for attempt in range(1, self._retry_policy.attempts + 1):
            try:
                return await operation()
            except Exception as error:
                last_error = error
                self._logger.warning(
                    "scraper_retry",
                    label=label,
                    attempt=attempt,
                    max_attempts=self._retry_policy.attempts,
                    error=str(error),
                )
                if attempt == self._retry_policy.attempts:
                    break
                delay = self._retry_policy.base_delay_seconds * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

        raise RuntimeError(f"{label} failed after retries") from last_error

    async def _rate_limited(self, url: str, operation: Callable[[], Awaitable[T]]) -> T:
        domain = urlparse(url).netloc
        semaphore = self._semaphores.setdefault(
            domain,
            asyncio.Semaphore(MAX_CONCURRENCY_PER_DOMAIN),
        )
        async with semaphore:
            await asyncio.sleep(self._random.uniform(2, 5))
            return await operation()

    def _pick_user_agent(self) -> str:
        return self._random.choice(DESKTOP_USER_AGENTS)
