from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Iterable
from datetime import date
from html.parser import HTMLParser
from hashlib import sha256
from typing import Any
from urllib.parse import quote_plus, urljoin

from playwright.async_api import Browser, Page, async_playwright
from pydantic import HttpUrl

from src.lib.logging import configure_logging, get_logger
from src.scrapers.base import PAGE_TIMEOUT_MS, BaseScraper, Offer, OfferStub, SearchQuery

JOBS_BASE_URL = "https://www.jobs.ch"
SOURCE = "jobs.ch"

POSTAL_CODE_PATTERN = re.compile(r"\b([1-9]\d{3})\b")
WHITESPACE_PATTERN = re.compile(r"\s+")


class JobsScraper(BaseScraper):
    """Scraper Playwright pour jobs.ch."""

    source = SOURCE

    async def fetch_listings(self, query: SearchQuery) -> list[OfferStub]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                stubs: list[OfferStub] = []
                seen_urls: set[str] = set()
                for location in query.locations:
                    if len(stubs) >= query.max_results:
                        break
                    url = build_search_url(query.term, location)
                    location_stubs = await self._fetch_search_page(browser, url)
                    for stub in location_stubs:
                        normalized_url = str(stub.url)
                        if normalized_url in seen_urls:
                            continue
                        seen_urls.add(normalized_url)
                        stubs.append(stub)
                        if len(stubs) >= query.max_results:
                            break
                return stubs
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
        """Récupère puis parse les offres en respectant la limite demandée."""

        stubs = await self.fetch_listings(query)
        offers: list[Offer] = []
        for stub in stubs:
            offers.append(await self.parse_listing(stub))
        return offers

    async def _fetch_search_page(self, browser: Browser, url: str) -> list[OfferStub]:
        async def operation() -> list[OfferStub]:
            page = await self._new_page(browser)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                await dismiss_cookie_banner(page)
                await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                cards = await extract_search_cards(page)
                if cards:
                    return cards
                html = await page.content()
                return parse_search_html(html)
            finally:
                await page.context.close()

        return await self._rate_limited(
            url,
            lambda: self._with_retry(f"jobs_search:{url}", operation),
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
                parsed = parse_offer_html(html, fallback_stub=stub)
                if parsed.raw_description:
                    return parsed
                page_text = normalize_text(await page.locator("body").inner_text())
                return offer_from_parts(
                    url=str(stub.url),
                    title=stub.title,
                    company=stub.company,
                    location=stub.location,
                    raw_description=page_text,
                )
            finally:
                await page.context.close()

        return await self._rate_limited(
            url,
            lambda: self._with_retry(f"jobs_detail:{url}", operation),
        )

    async def _new_page(self, browser: Browser) -> Page:
        context = await browser.new_context(
            user_agent=self._pick_user_agent(),
            locale="fr-CH",
            viewport={"width": 1366, "height": 900},
        )
        return await context.new_page()


def build_search_url(term: str, location: str) -> str:
    """Construit une URL jobs.ch stable avec paramètres lisibles."""

    return (
        f"{JOBS_BASE_URL}/fr/offres-demploi/"
        f"?term={quote_plus(term)}&location={quote_plus(location)}"
    )


async def dismiss_cookie_banner(page: Page) -> None:
    """Ferme la bannière cookies si elle est présente."""

    labels = ("Tout accepter", "Accepter", "Accept all", "Alle akzeptieren", "Accepter tout")
    for label in labels:
        button = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
        if await button.count() == 0:
            continue
        try:
            await button.first.click(timeout=2_000)
            return
        except Exception as error:
            get_logger("scrapers.jobs").warning(
                "cookie_banner_click_failed",
                label=label,
                error=str(error),
            )
            continue


async def extract_search_cards(page: Page) -> list[OfferStub]:
    """Extrait les liens d'offres depuis une page de recherche rendue."""

    links = page.locator("a[href*='/fr/offres-demploi/']")
    count = await links.count()
    stubs: list[OfferStub] = []
    seen_urls: set[str] = set()
    for index in range(count):
        link = links.nth(index)
        href = await link.get_attribute("href")
        if href is None or not is_job_detail_url(href):
            continue
        absolute_url = urljoin(JOBS_BASE_URL, href)
        if absolute_url in seen_urls:
            continue
        title = normalize_text(await link.inner_text())
        if not title or len(title) < 3:
            title = "Offre jobs.ch"
        seen_urls.add(absolute_url)
        stubs.append(
            OfferStub(
                source=SOURCE,
                url=HttpUrl(absolute_url),
                title=title,
            )
        )
    return stubs


class JobsHTMLParser(HTMLParser):
    """Extracteur HTML léger pour fixtures et fallback sans dépendance lourde."""

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


def parse_search_html(html: str) -> list[OfferStub]:
    """Parse une page de recherche jobs.ch depuis du HTML déjà capturé."""

    parser = JobsHTMLParser()
    parser.feed(html)
    stubs: list[OfferStub] = []
    seen_urls: set[str] = set()
    for href, title in parser.links:
        if not is_job_detail_url(href):
            continue
        absolute_url = urljoin(JOBS_BASE_URL, href)
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)
        stubs.append(
            OfferStub(
                source=SOURCE,
                url=HttpUrl(absolute_url),
                title=title or "Offre jobs.ch",
            )
        )
    return stubs


def parse_offer_html(html: str, fallback_stub: OfferStub | None = None) -> Offer:
    """Parse une page détail jobs.ch depuis du HTML, avec priorité au JSON-LD."""

    parser = JobsHTMLParser()
    parser.feed(html)
    json_offer = offer_from_json_ld(parser.json_ld_blocks, fallback_stub)
    if json_offer is not None:
        return json_offer

    url = str(fallback_stub.url) if fallback_stub else parser.meta.get("og:url", JOBS_BASE_URL)
    title = parser.meta.get("og:title") or (fallback_stub.title if fallback_stub else "Offre jobs.ch")
    description = parser.meta.get("description") or parser.visible_text
    company = fallback_stub.company if fallback_stub else None
    location = fallback_stub.location if fallback_stub else None
    return offer_from_parts(
        url=url,
        title=title,
        company=company,
        location=location,
        raw_description=description,
    )


def offer_from_json_ld(blocks: Iterable[str], fallback_stub: OfferStub | None) -> Offer | None:
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
            return offer_from_jobposting(node, fallback_stub)
    return None


def offer_from_jobposting(payload: dict[str, Any], fallback_stub: OfferStub | None) -> Offer:
    url = str(payload.get("url") or (fallback_stub.url if fallback_stub else JOBS_BASE_URL))
    title = str(payload.get("title") or (fallback_stub.title if fallback_stub else "Offre jobs.ch"))
    company = extract_company(payload) or (fallback_stub.company if fallback_stub else None)
    location = extract_location(payload) or (fallback_stub.location if fallback_stub else None)
    description = normalize_text(str(payload.get("description") or ""))
    return offer_from_parts(
        url=url,
        title=title,
        company=company,
        location=location,
        raw_description=description,
        publication_date=parse_iso_date(payload.get("datePosted")),
    )


def offer_from_parts(
    *,
    url: str,
    title: str,
    raw_description: str,
    company: str | None = None,
    location: str | None = None,
    publication_date: date | None = None,
) -> Offer:
    # jobs.ch URLs are absolute, no need to join with base
    normalized_url = url if url.startswith("http") else urljoin(JOBS_BASE_URL, url)
    normalized_location = normalize_optional(location)
    raw_text = normalize_text(raw_description)
    postal_code_source = " ".join(part for part in (normalized_location, raw_text) if part)
    return Offer(
        source=SOURCE,
        url=HttpUrl(normalized_url),
        url_hash=sha256_text(normalized_url),
        title=normalize_text(title),
        company=normalize_optional(company),
        location=normalized_location,
        postal_code=extract_postal_code(postal_code_source),
        publication_date=publication_date,
        raw_description=raw_text,
    )


def extract_company(payload: dict[str, Any]) -> str | None:
    organization = payload.get("hiringOrganization")
    if isinstance(organization, dict):
        name = organization.get("name")
        if isinstance(name, str):
            return normalize_optional(name)
    return None


def extract_location(payload: dict[str, Any]) -> str | None:
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
    return normalize_optional(" ".join(str(part) for part in parts if part))


def parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def is_job_detail_url(url: str) -> bool:
    """Détecte les URLs de pages d'offres jobs.ch (exclut la page de liste elle-même)."""

    # L'URL de liste est /fr/offres-demploi/ sans slug supplémentaire
    # Les détails ont un slug: /fr/offres-demploi/<slug>/
    stripped = url.split("?")[0].rstrip("/")
    parts = [p for p in stripped.split("/") if p]
    # structure attendue: ["fr", "offres-demploi", "<slug>"]
    return (
        "offres-demploi" in parts
        and len(parts) >= 3
        and parts[-1] != "offres-demploi"
    )


def normalize_text(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", value).strip()


def normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_text(value)
    return normalized or None


def extract_postal_code(value: str) -> str | None:
    match = POSTAL_CODE_PATTERN.search(value)
    return match.group(1) if match else None


def sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


async def async_main(max_results: int) -> None:
    configure_logging()
    logger = get_logger("scrapers.jobs")
    scraper = JobsScraper()
    query = SearchQuery(max_results=max_results)
    offers = await scraper.fetch_and_parse(query)
    for offer in offers:
        logger.info(
            "jobs_offer",
            title=offer.title,
            company=offer.company,
            location=offer.location,
            url=str(offer.url),
        )
    logger.info("jobs_scrape_done", count=len(offers))


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape jobs.ch")
    parser.add_argument("--max", type=int, default=5, dest="max_results")
    args = parser.parse_args()
    asyncio.run(async_main(max_results=args.max_results))


if __name__ == "__main__":
    main()
