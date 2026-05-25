from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

from playwright.async_api import Browser, Page, async_playwright
from pydantic import HttpUrl

from src.lib.logging import configure_logging, get_logger
from src.scrapers.base import PAGE_TIMEOUT_MS, BaseScraper, Offer, OfferStub, SearchQuery

WHITESPACE_PATTERN = re.compile(r"\s+")
POSTAL_CODE_PATTERN = re.compile(r"\b([1-9]\d{3})\b")


@dataclass(frozen=True)
class CompanyConfig:
    """Configuration d'un site carrière entreprise.

    search_url_template : URL de recherche avec {term} et optionnellement {location}.
    link_patterns       : sous-chaînes présentes dans les hrefs des pages d'offre.
    locations_override  : si défini, remplace les locations de SearchQuery.
    """

    source: str
    base_url: str
    search_url_template: str
    link_patterns: tuple[str, ...]
    locations_override: tuple[str, ...] = field(default_factory=tuple)


# ── Configurations entreprises ─────────────────────────────────────────────────

MIGROS_CONFIG = CompanyConfig(
    source="migros",
    base_url="https://jobs.migros.ch",
    search_url_template="https://jobs.migros.ch/fr/offres-d-emploi?query={term}",
    link_patterns=("/fr/offres-d-emploi/detail/",),
)

COOP_CONFIG = CompanyConfig(
    source="coop",
    base_url="https://www.coopcareers.ch",
    search_url_template="https://www.coopcareers.ch/fr/jobs?keyword={term}",
    link_patterns=("/fr/jobs/",),
)

MANOR_CONFIG = CompanyConfig(
    source="manor",
    base_url="https://careers.manor.ch",
    search_url_template="https://careers.manor.ch/fr/offres?keyword={term}",
    link_patterns=("/fr/offres/", "/fr/jobs/"),
)

ALDI_CONFIG = CompanyConfig(
    source="aldi",
    base_url="https://www.aldi-now.ch",
    search_url_template="https://www.aldi-now.ch/fr/emplois?search={term}",
    link_patterns=("/fr/emplois/", "/fr/jobs/"),
)

LIDL_CONFIG = CompanyConfig(
    source="lidl",
    base_url="https://jobs.lidl.ch",
    search_url_template="https://jobs.lidl.ch/fr/emplois?q={term}",
    link_patterns=("/fr/emplois/", "/fr/jobs/"),
)

DENNER_CONFIG = CompanyConfig(
    source="denner",
    base_url="https://www.denner.ch",
    search_url_template="https://www.denner.ch/fr/a-propos-de-nous/emplois/?q={term}",
    link_patterns=("/a-propos-de-nous/emplois/detail/", "/emplois/detail/"),
)

LANDI_CONFIG = CompanyConfig(
    source="landi",
    base_url="https://www.landi.ch",
    search_url_template="https://www.landi.ch/fr/emplois?search={term}",
    link_patterns=("/fr/emplois/", "/fr/jobs/"),
)

ALL_COMPANY_CONFIGS: tuple[CompanyConfig, ...] = (
    MIGROS_CONFIG,
    COOP_CONFIG,
    MANOR_CONFIG,
    ALDI_CONFIG,
    LIDL_CONFIG,
    DENNER_CONFIG,
    LANDI_CONFIG,
)


# ── Scraper générique ──────────────────────────────────────────────────────────

class CompanySiteScraper(BaseScraper):
    """Scraper Playwright générique pour les sites carrières entreprises."""

    def __init__(self, config: CompanyConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._config = config
        self.source = config.source

    async def fetch_listings(self, query: SearchQuery) -> list[OfferStub]:
        search_url = self._config.search_url_template.format(
            term=quote_plus(query.term),
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                stubs = await self._fetch_search_page(browser, search_url)
                return stubs[: query.max_results]
            finally:
                await browser.close()

    async def parse_listing(self, stub: OfferStub) -> Offer:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                return await self._parse_listing_with_browser(browser, stub)
            finally:
                await browser.close()

    async def fetch_and_parse(self, query: SearchQuery) -> list[Offer]:
        search_url = self._config.search_url_template.format(
            term=quote_plus(query.term),
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                stubs = await self._fetch_search_page(browser, search_url)
                stubs = stubs[: query.max_results]
                offers: list[Offer] = []
                for stub in stubs:
                    offer = await self._parse_listing_with_browser(browser, stub)
                    offers.append(offer)
                return offers
            finally:
                await browser.close()

    async def _fetch_search_page(self, browser: Browser, url: str) -> list[OfferStub]:
        async def operation() -> list[OfferStub]:
            page = await self._new_page(browser)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                await dismiss_cookie_banner(page)
                await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                cards = await self._extract_search_cards(page)
                if cards:
                    return cards
                html = await page.content()
                return parse_search_html(html, self._config)
            finally:
                await page.context.close()

        return await self._rate_limited(
            url,
            lambda: self._with_retry(f"{self.source}_search:{url}", operation),
        )

    async def _parse_listing_with_browser(self, browser: Browser, stub: OfferStub) -> Offer:
        url = str(stub.url)

        async def operation() -> Offer:
            page = await self._new_page(browser)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                await dismiss_cookie_banner(page)
                await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                html = await page.content()
                parsed = parse_offer_html(html, config=self._config, fallback_stub=stub)
                if parsed.raw_description:
                    return parsed
                page_text = normalize_text(await page.locator("body").inner_text())
                return offer_from_parts(
                    config=self._config,
                    url=url,
                    title=stub.title,
                    company=stub.company or self._config.source.capitalize(),
                    location=stub.location,
                    raw_description=page_text,
                )
            finally:
                await page.context.close()

        return await self._rate_limited(
            url,
            lambda: self._with_retry(f"{self.source}_detail:{url}", operation),
        )

    async def _extract_search_cards(self, page: Page) -> list[OfferStub]:
        selector = ", ".join(
            f"a[href*='{pattern}']" for pattern in self._config.link_patterns
        )
        links = page.locator(selector)
        count = await links.count()
        stubs: list[OfferStub] = []
        seen_urls: set[str] = set()
        for index in range(count):
            link = links.nth(index)
            href = await link.get_attribute("href")
            if href is None:
                continue
            if not is_job_detail_url(href, self._config):
                continue
            absolute_url = urljoin(self._config.base_url, href)
            if absolute_url in seen_urls:
                continue
            title = normalize_text(await link.inner_text())
            if not title or len(title) < 3:
                title = f"Offre {self._config.source}"
            seen_urls.add(absolute_url)
            stubs.append(
                OfferStub(
                    source=self._config.source,
                    url=HttpUrl(absolute_url),
                    title=title,
                )
            )
        return stubs

    async def _new_page(self, browser: Browser) -> Page:
        context = await browser.new_context(
            user_agent=self._pick_user_agent(),
            locale="fr-CH",
            viewport={"width": 1366, "height": 900},
        )
        return await context.new_page()


# ── Parseurs HTML ──────────────────────────────────────────────────────────────

class CompanyHTMLParser(HTMLParser):
    """Extracteur HTML léger réutilisable pour tous les sites entreprises."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self.json_ld_blocks: list[str] = []
        self.current_link: str | None = None
        self._link_text_parts: list[str] = []
        self._current_script_type: str | None = None
        self._script_parts: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value for name, value in attrs if value is not None}
        if tag == "a" and "href" in attr_map:
            self.current_link = attr_map["href"]
            self._link_text_parts = []
        if tag == "meta":
            name = attr_map.get("name") or attr_map.get("property")
            content = attr_map.get("content")
            if name is not None and content is not None:
                self.meta[name] = content
        if tag == "script":
            self._current_script_type = attr_map.get("type")
            self._script_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_link is not None:
            self.links.append((self.current_link, normalize_text(" ".join(self._link_text_parts))))
            self.current_link = None
            self._link_text_parts = []
        if tag == "script" and self._current_script_type == "application/ld+json":
            self.json_ld_blocks.append("".join(self._script_parts))
            self._current_script_type = None
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self.current_link is not None:
            self._link_text_parts.append(data)
        if self._current_script_type == "application/ld+json":
            self._script_parts.append(data)
        if data.strip():
            self._text_parts.append(data)

    @property
    def visible_text(self) -> str:
        return normalize_text(" ".join(self._text_parts))


def parse_search_html(html: str, config: CompanyConfig) -> list[OfferStub]:
    parser = CompanyHTMLParser()
    parser.feed(html)
    stubs: list[OfferStub] = []
    seen_urls: set[str] = set()
    for href, title in parser.links:
        if not is_job_detail_url(href, config):
            continue
        absolute_url = urljoin(config.base_url, href)
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)
        stubs.append(
            OfferStub(
                source=config.source,
                url=HttpUrl(absolute_url),
                title=title or f"Offre {config.source}",
            )
        )
    return stubs


def parse_offer_html(
    html: str,
    config: CompanyConfig,
    fallback_stub: OfferStub | None = None,
) -> Offer:
    parser = CompanyHTMLParser()
    parser.feed(html)

    json_offer = offer_from_json_ld(parser.json_ld_blocks, config=config, fallback_stub=fallback_stub)
    if json_offer is not None:
        return json_offer

    url = str(fallback_stub.url) if fallback_stub else config.base_url
    title = (
        parser.meta.get("og:title")
        or (fallback_stub.title if fallback_stub else f"Offre {config.source}")
    )
    description = parser.meta.get("description") or parser.visible_text
    company = (fallback_stub.company if fallback_stub else None) or config.source.capitalize()
    location = fallback_stub.location if fallback_stub else None
    return offer_from_parts(
        config=config,
        url=url,
        title=title,
        company=company,
        location=location,
        raw_description=description,
    )


def offer_from_json_ld(
    blocks: Iterable[str],
    config: CompanyConfig,
    fallback_stub: OfferStub | None,
) -> Offer | None:
    for block in blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") != "JobPosting":
                continue
            return offer_from_jobposting(node, config=config, fallback_stub=fallback_stub)
    return None


def offer_from_jobposting(
    payload: dict[str, Any],
    config: CompanyConfig,
    fallback_stub: OfferStub | None,
) -> Offer:
    url = str(payload.get("url") or (fallback_stub.url if fallback_stub else config.base_url))
    title = str(
        payload.get("title")
        or (fallback_stub.title if fallback_stub else f"Offre {config.source}")
    )
    company = _extract_company(payload) or (
        fallback_stub.company if fallback_stub else config.source.capitalize()
    )
    location = _extract_location(payload) or (fallback_stub.location if fallback_stub else None)
    description = normalize_text(str(payload.get("description") or ""))
    return offer_from_parts(
        config=config,
        url=url,
        title=title,
        company=company,
        location=location,
        raw_description=description,
        publication_date=_parse_iso_date(payload.get("datePosted")),
    )


def offer_from_parts(
    *,
    config: CompanyConfig,
    url: str,
    title: str,
    raw_description: str,
    company: str | None = None,
    location: str | None = None,
    publication_date: date | None = None,
) -> Offer:
    normalized_url = url if url.startswith("http") else urljoin(config.base_url, url)
    normalized_location = _normalize_optional(location)
    raw_text = normalize_text(raw_description)
    postal_code_source = " ".join(part for part in (normalized_location, raw_text) if part)
    resolved_company = _normalize_optional(company) or config.source.capitalize()
    return Offer(
        source=config.source,
        url=HttpUrl(normalized_url),
        url_hash=_sha256(normalized_url),
        title=normalize_text(title),
        company=resolved_company,
        location=normalized_location,
        postal_code=_extract_postal_code(postal_code_source),
        publication_date=publication_date,
        raw_description=raw_text,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

async def dismiss_cookie_banner(page: Page) -> None:
    labels = (
        "Tout accepter", "Accepter tout", "Accepter", "Accept all",
        "Alle akzeptieren", "Alle annehmen", "OK",
    )
    for label in labels:
        button = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
        if await button.count() == 0:
            continue
        try:
            await button.first.click(timeout=2_000)
            return
        except Exception:
            continue


def is_job_detail_url(url: str, config: CompanyConfig) -> bool:
    """Vérifie qu'un href correspond à une page d'offre (pas la liste)."""

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    # Doit contenir au moins un pattern ET avoir un segment en plus
    for pattern in config.link_patterns:
        if pattern.rstrip("/") in path:
            # S'assurer que ce n'est pas l'URL de base du pattern (page de liste)
            suffix = path.split(pattern.rstrip("/"))[-1]
            if suffix.strip("/"):
                return True
    return False


def _extract_company(payload: dict[str, Any]) -> str | None:
    org = payload.get("hiringOrganization")
    if isinstance(org, dict):
        name = org.get("name")
        if isinstance(name, str):
            return _normalize_optional(name)
    return None


def _extract_location(payload: dict[str, Any]) -> str | None:
    job_location = payload.get("jobLocation")
    if isinstance(job_location, list) and job_location:
        location_payload = job_location[0]
    else:
        location_payload = job_location
    if not isinstance(location_payload, dict):
        return None
    address = location_payload.get("address")
    if not isinstance(address, dict):
        return None
    parts = [
        address.get("postalCode"),
        address.get("addressLocality"),
        address.get("addressRegion"),
    ]
    return _normalize_optional(" ".join(str(p) for p in parts if p))


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _extract_postal_code(value: str) -> str | None:
    match = POSTAL_CODE_PATTERN.search(value)
    return match.group(1) if match else None


def normalize_text(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", value).strip()


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_text(value)
    return normalized or None


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


# ── CLI ────────────────────────────────────────────────────────────────────────

async def async_main(source: str, max_results: int) -> None:
    configure_logging()
    logger = get_logger("scrapers.company_sites")

    config_map = {c.source: c for c in ALL_COMPANY_CONFIGS}
    if source not in config_map:
        available = ", ".join(config_map)
        raise SystemExit(f"Source inconnue: {source!r}. Disponibles: {available}")

    config = config_map[source]
    scraper = CompanySiteScraper(config)
    query = SearchQuery(max_results=max_results)
    offers = await scraper.fetch_and_parse(query)
    for offer in offers:
        logger.info(
            "company_offer",
            source=offer.source,
            title=offer.title,
            company=offer.company,
            location=offer.location,
            postal_code=offer.postal_code,
            url=str(offer.url),
        )
    logger.info("company_scrape_done", source=source, count=len(offers))


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape un site carrière entreprise.")
    parser.add_argument(
        "--source",
        choices=[c.source for c in ALL_COMPANY_CONFIGS],
        required=True,
    )
    parser.add_argument("--max", type=int, default=5, dest="max_results")
    args = parser.parse_args()
    asyncio.run(async_main(source=args.source, max_results=args.max_results))


if __name__ == "__main__":
    main()
