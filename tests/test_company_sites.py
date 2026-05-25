from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import HttpUrl

from src.scrapers.base import OfferStub
from src.scrapers.company_sites import (
    ALL_COMPANY_CONFIGS,
    ALDI_CONFIG,
    COOP_CONFIG,
    DENNER_CONFIG,
    LANDI_CONFIG,
    LIDL_CONFIG,
    MANOR_CONFIG,
    MIGROS_CONFIG,
    CompanyConfig,
    CompanyHTMLParser,
    is_job_detail_url,
    offer_from_parts,
    parse_offer_html,
    parse_search_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def migros_listing_html() -> str:
    return (FIXTURES / "migros_listing.html").read_text(encoding="utf-8")


@pytest.fixture()
def migros_detail_html() -> str:
    return (FIXTURES / "migros_detail.html").read_text(encoding="utf-8")


# ── is_job_detail_url ──────────────────────────────────────────────────────────

class TestIsJobDetailUrl:
    def test_migros_detail_accepted(self) -> None:
        assert is_job_detail_url(
            "/fr/offres-d-emploi/detail/vendeur-delemont-123", MIGROS_CONFIG
        )

    def test_migros_list_rejected(self) -> None:
        assert not is_job_detail_url("/fr/offres-d-emploi/", MIGROS_CONFIG)

    def test_coop_detail_accepted(self) -> None:
        assert is_job_detail_url("/fr/jobs/vendeur-porrentruy-456", COOP_CONFIG)

    def test_coop_list_rejected(self) -> None:
        assert not is_job_detail_url("/fr/jobs/", COOP_CONFIG)

    def test_manor_detail_accepted(self) -> None:
        assert is_job_detail_url("/fr/offres/conseiller-vente-789", MANOR_CONFIG)

    def test_aldi_detail_accepted(self) -> None:
        assert is_job_detail_url("/fr/emplois/vendeur-101", ALDI_CONFIG)

    def test_lidl_detail_accepted(self) -> None:
        assert is_job_detail_url("/fr/emplois/employe-commerce-202", LIDL_CONFIG)

    def test_denner_detail_accepted(self) -> None:
        assert is_job_detail_url(
            "/a-propos-de-nous/emplois/detail/vendeur-303", DENNER_CONFIG
        )

    def test_landi_detail_accepted(self) -> None:
        assert is_job_detail_url("/fr/emplois/vendeur-landi-404", LANDI_CONFIG)


# ── parse_search_html ──────────────────────────────────────────────────────────

class TestParseSearchHtml:
    def test_extracts_two_detail_stubs(self, migros_listing_html: str) -> None:
        stubs = parse_search_html(migros_listing_html, MIGROS_CONFIG)
        assert len(stubs) == 2

    def test_excludes_list_page_link(self, migros_listing_html: str) -> None:
        stubs = parse_search_html(migros_listing_html, MIGROS_CONFIG)
        urls = [str(s.url) for s in stubs]
        assert not any("/fr/offres-d-emploi/" == u.split("jobs.migros.ch")[-1] for u in urls)

    def test_stubs_have_correct_source(self, migros_listing_html: str) -> None:
        stubs = parse_search_html(migros_listing_html, MIGROS_CONFIG)
        assert all(s.source == "migros" for s in stubs)

    def test_stubs_have_absolute_urls(self, migros_listing_html: str) -> None:
        stubs = parse_search_html(migros_listing_html, MIGROS_CONFIG)
        assert all(str(s.url).startswith("https://jobs.migros.ch") for s in stubs)

    def test_deduplicates_same_href(self) -> None:
        html = """
        <a href="/fr/offres-d-emploi/detail/vendeur-123">Vendeur</a>
        <a href="/fr/offres-d-emploi/detail/vendeur-123">Vendeur</a>
        """
        stubs = parse_search_html(html, MIGROS_CONFIG)
        assert len(stubs) == 1


# ── parse_offer_html ───────────────────────────────────────────────────────────

class TestParseOfferHtml:
    def test_extracts_from_json_ld(self, migros_detail_html: str) -> None:
        offer = parse_offer_html(migros_detail_html, config=MIGROS_CONFIG)
        assert offer.title == "Vendeur-conseil (h/f) 80-100%"
        assert offer.company == "Migros"
        assert offer.postal_code == "2800"
        assert offer.publication_date == date(2026, 5, 23)

    def test_source_matches_config(self, migros_detail_html: str) -> None:
        offer = parse_offer_html(migros_detail_html, config=MIGROS_CONFIG)
        assert offer.source == "migros"

    def test_description_not_empty(self, migros_detail_html: str) -> None:
        offer = parse_offer_html(migros_detail_html, config=MIGROS_CONFIG)
        assert len(offer.raw_description) > 10

    def test_fallback_to_stub_when_no_json_ld(self) -> None:
        html = "<html><body><p>Poste à Porrentruy 2900.</p></body></html>"
        stub = OfferStub(
            source="coop",
            url=HttpUrl("https://www.coopcareers.ch/fr/jobs/vendeur-999/"),
            title="Vendeur Porrentruy",
            company="Coop",
            location="Porrentruy",
        )
        offer = parse_offer_html(html, config=COOP_CONFIG, fallback_stub=stub)
        assert offer.title == "Vendeur Porrentruy"
        assert offer.company == "Coop"

    def test_url_hash_is_stable(self, migros_detail_html: str) -> None:
        o1 = parse_offer_html(migros_detail_html, config=MIGROS_CONFIG)
        o2 = parse_offer_html(migros_detail_html, config=MIGROS_CONFIG)
        assert o1.url_hash == o2.url_hash

    def test_no_json_ld_uses_og_title(self) -> None:
        html = """
        <html><head>
        <meta property="og:title" content="Employé de commerce">
        <meta property="og:url" content="https://www.manor.ch/fr/offres/employe-123">
        <meta name="description" content="Poste à Saint-Imier 2610.">
        </head><body></body></html>
        """
        offer = parse_offer_html(html, config=MANOR_CONFIG)
        assert offer.title == "Employé de commerce"
        assert offer.postal_code == "2610"


# ── offer_from_parts ───────────────────────────────────────────────────────────

class TestOfferFromParts:
    def test_postal_code_from_location(self) -> None:
        offer = offer_from_parts(
            config=MIGROS_CONFIG,
            url="https://jobs.migros.ch/fr/offres-d-emploi/detail/test-123",
            title="Vendeur",
            raw_description="CDI.",
            location="2800 Delémont",
        )
        assert offer.postal_code == "2800"

    def test_postal_code_from_description(self) -> None:
        offer = offer_from_parts(
            config=LIDL_CONFIG,
            url="https://jobs.lidl.ch/fr/emplois/vendeur-123",
            title="Vendeur",
            raw_description="Magasin situé au 2610 Saint-Imier.",
        )
        assert offer.postal_code == "2610"

    def test_relative_url_gets_base(self) -> None:
        offer = offer_from_parts(
            config=DENNER_CONFIG,
            url="/a-propos-de-nous/emplois/detail/vendeur-456",
            title="Vendeur",
            raw_description="Description.",
        )
        assert str(offer.url).startswith("https://www.denner.ch")

    def test_company_defaults_to_capitalized_source(self) -> None:
        offer = offer_from_parts(
            config=LANDI_CONFIG,
            url="https://www.landi.ch/fr/emplois/vendeur-789",
            title="Vendeur",
            raw_description="Description.",
        )
        assert offer.company == "Landi"


# ── CompanyHTMLParser ──────────────────────────────────────────────────────────

class TestCompanyHTMLParser:
    def test_parses_json_ld(self, migros_detail_html: str) -> None:
        parser = CompanyHTMLParser()
        parser.feed(migros_detail_html)
        assert len(parser.json_ld_blocks) == 1

    def test_parses_og_title(self, migros_detail_html: str) -> None:
        parser = CompanyHTMLParser()
        parser.feed(migros_detail_html)
        assert parser.meta.get("og:title") == "Vendeur-conseil (h/f) 80-100%"

    def test_visible_text_not_empty(self, migros_detail_html: str) -> None:
        parser = CompanyHTMLParser()
        parser.feed(migros_detail_html)
        assert len(parser.visible_text) > 0


# ── ALL_COMPANY_CONFIGS ────────────────────────────────────────────────────────

class TestAllCompanyConfigs:
    def test_seven_companies_defined(self) -> None:
        assert len(ALL_COMPANY_CONFIGS) == 7

    def test_all_sources_unique(self) -> None:
        sources = [c.source for c in ALL_COMPANY_CONFIGS]
        assert len(sources) == len(set(sources))

    def test_all_have_link_patterns(self) -> None:
        for config in ALL_COMPANY_CONFIGS:
            assert config.link_patterns, f"{config.source} has no link_patterns"

    def test_all_search_urls_contain_term_placeholder(self) -> None:
        for config in ALL_COMPANY_CONFIGS:
            assert "{term}" in config.search_url_template, (
                f"{config.source} search_url_template missing {{term}}"
            )

    def test_all_base_urls_are_https(self) -> None:
        for config in ALL_COMPANY_CONFIGS:
            assert config.base_url.startswith("https://"), (
                f"{config.source} base_url not https"
            )
